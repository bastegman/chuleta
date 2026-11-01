# -*- coding: utf-8 -*-
"""Seguridad de la API local (auditoría final del 31/10/2026): peticiones escondidas en el cuerpo de otra y POST de
otras webs (P0-1), el icono de una reunión pintado sin escapar y la CSP (P0-2), el token de sesión (P1-02), el proxy
del sistema en las llamadas a 127.0.0.1 y la segunda copia que creaba App() antes de abrir el puerto (P1-13), y la
caché del audio (P2-13). Con el servidor HTTP de verdad en un puerto libre y dobles: sin red y sin Windows (salvo el
parseo de los .ps1, que solo corre allí)."""
import contextlib
import ctypes
import importlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from support import APP, REPO, TempTree, patched_paths, server

import aviso  # noqa: E402

TOKEN_HEADER = "X-Chuleta-Token"  # literal a propósito: es el contrato con el instalador, el aviso y los scripts
TOKEN = "token-de-prueba-" + "x" * 27
STATIC = os.path.join(APP, "static")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "media-src 'self'; connect-src 'self'; frame-ancestors 'none'")


def read(*parts):
    with open(os.path.join(APP, *parts), encoding="utf-8-sig") as f:
        return f.read()


def write_token_file(data_dir, token):
    """sesion.token tal como lo deja el servidor (sin usar su función: el contrato es el fichero)."""
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "sesion.token"), "w", encoding="utf-8", newline="") as f:
        f.write(token)


def exchange(port, data, idle=1.0, total=6.0):
    """Manda `data` tal cual por UN socket (lo que hace un navegador con keep-alive) y lee lo que conteste hasta que
    cierre o pase `idle` sin datos. Devuelve (bytes, cerrada)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=idle)
    out, closed, end = b"", False, time.time() + total
    try:
        s.sendall(data)
        while time.time() < end:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            except (ConnectionResetError, ConnectionAbortedError):
                closed = True
                break
            if not chunk:
                closed = True
                break
            out += chunk
    finally:
        s.close()
    return out, closed


def statuses(raw):
    return [int(x) for x in re.findall(rb"HTTP/1\.[01] (\d{3}) ", raw)]  # (las respuestas pueden ir pegadas)


def request_bytes(method, path, port, headers=None, body=b""):
    h = {"Host": f"127.0.0.1:{port}", "Content-Length": str(len(body))}
    h.update(headers or {})
    head = "".join(f"{k}: {v}\r\n" for k, v in h.items() if v is not None)
    return f"{method} {path} HTTP/1.1\r\n{head}\r\n".encode("latin-1") + body


class ServidorBase(unittest.TestCase):
    """El servidor de verdad con un App de mentira (mock): cada ruta es un Mock que devuelve {"ok": True}."""

    @classmethod
    def setUpClass(cls):
        cls.srv = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        cls.saved = (server.PORT, server.URL, server.Handler.app, getattr(server.Handler, "token", None))
        server.PORT, server.URL = cls.port, f"http://127.0.0.1:{cls.port}/"
        server.Handler.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        server.PORT, server.URL, server.Handler.app, server.Handler.token = cls.saved

    def setUp(self):
        self.app = mock.Mock()
        for name in ("apagar", "ocultar", "mostrar", "delete", "save_settings", "notes_save"):
            getattr(self.app, name).return_value = {"ok": True}
        self.app.config.return_value = {"ok": True}
        server.Handler.app = self.app

    def call(self, method, path, headers=None, body=None):
        """(código, cabeceras, cuerpo) de una petición normal (urllib sin proxy)."""
        data = json.dumps(body).encode("utf-8") if body is not None else (b"{}" if method == "POST" else None)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                     headers=headers or {})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(req, timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.headers, e.read()
            finally:
                e.close()

    def post_ok_headers(self, **extra):
        h = {TOKEN_HEADER: TOKEN, "X-Audios": "1", "Content-Type": "application/json"}
        h.update(extra)
        return h


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


# ----------------------------------------------------------------------------- P0-1

class PeticionesEscondidasTests(ServidorBase):
    def smuggle(self, outer_method, outer_path, outer_headers):
        inner = request_bytes("POST", "/api/apagar", self.port,
                              {TOKEN_HEADER: TOKEN, "X-Audios": "1", "Content-Type": "application/json"}, b"{}")
        return exchange(self.port, request_bytes(outer_method, outer_path, self.port, outer_headers, inner))

    def test_post_rechazado_no_ejecuta_la_peticion_incrustada(self):
        """El caso de la auditoría: una web manda un POST text/plain (sin consulta previa del navegador) cuyo cuerpo es
        otra petición completa, con su Host, X-Audios y, aquí, hasta el token. El 403 no leía el cuerpo y con keep-alive
        el servidor lo tomaba por la petición siguiente y lo ejecutaba."""
        raw, _ = self.smuggle("POST", "/api/ocultar", {"Content-Type": "text/plain;charset=UTF-8",
                                                       "Origin": "http://evil.example"})
        self.app.apagar.assert_not_called()
        self.assertNotIn(200, statuses(raw), raw[:400])
        self.assertIn(403, statuses(raw), raw[:400])

    def test_post_sin_x_audios_tampoco(self):
        raw, _ = self.smuggle("POST", "/api/ocultar", {"Content-Type": "text/plain"})
        self.app.apagar.assert_not_called()
        self.assertEqual(statuses(raw), [403], raw[:400])

    def test_get_con_cuerpo_no_ejecuta_la_incrustada(self):
        raw, _ = self.smuggle("GET", "/api/ping", {TOKEN_HEADER: TOKEN})
        self.app.apagar.assert_not_called()
        self.assertEqual(statuses(raw), [200], raw[:400])

    def test_cuerpo_grande_sin_leer_cierra_la_conexion(self):
        """Más de DRAIN_MAX sin leer (una subida rechazada): no se lee entero, se cierra la conexión y lo incrustado al
        principio del cuerpo tampoco se ejecuta."""
        inner = request_bytes("POST", "/api/apagar", self.port, self.post_ok_headers(), b"{}")
        raw, closed = exchange(self.port, request_bytes("POST", "/api/ocultar", self.port, {
            "Content-Type": "text/plain", "Content-Length": str((1 << 20) + 10)}, inner))
        self.app.apagar.assert_not_called()
        self.app.ocultar.assert_not_called()
        self.assertTrue(closed)
        if raw:  # al cerrar con datos sin leer el sistema puede mandar un RST y perder la respuesta: da igual aquí
            self.assertIn(b"Connection: close", raw)
            self.assertEqual(statuses(raw), [403])

    def test_transfer_encoding_se_rechaza_y_cierra(self):
        inner = request_bytes("POST", "/api/apagar", self.port, {TOKEN_HEADER: TOKEN, "X-Audios": "1"}, b"{}")
        chunked = f"{len(inner):x}\r\n".encode() + inner + b"\r\n0\r\n\r\n"
        raw, closed = exchange(self.port, request_bytes("POST", "/api/ocultar", self.port, {
            TOKEN_HEADER: TOKEN, "X-Audios": "1", "Content-Length": None, "Transfer-Encoding": "chunked"}, chunked))
        self.app.apagar.assert_not_called()
        self.app.ocultar.assert_not_called()
        self.assertTrue(closed)
        if raw:  # (el RST al cerrar con datos sin leer puede llevarse la respuesta)
            self.assertEqual(statuses(raw), [411], raw[:400])

    def test_content_length_raro_cierra(self):
        for cl in ("-5", "abc"):
            raw, closed = exchange(self.port, request_bytes("POST", "/api/ocultar", self.port, {
                TOKEN_HEADER: TOKEN, "X-Audios": "1", "Content-Length": cl}))
            self.assertTrue(closed, cl)
            self.assertEqual(len(statuses(raw)), 1, raw[:300])
            self.assertGreaterEqual(statuses(raw)[0], 400, raw[:300])
        self.app.ocultar.assert_not_called()

    def test_keep_alive_normal_sigue(self):
        """Dos peticiones legítimas por la misma conexión: las dos contestan y la conexión sigue abierta."""
        one = request_bytes("POST", "/api/ocultar", self.port, self.post_ok_headers(), b"{}")
        two = request_bytes("GET", "/api/ping", self.port, {TOKEN_HEADER: TOKEN})
        raw, closed = exchange(self.port, one + two)
        self.assertEqual(statuses(raw), [200, 200], raw[:400])
        self.assertFalse(closed)
        self.app.ocultar.assert_called_once()

    def test_origen_ajeno_prohibido(self):
        for extra in ({"Origin": "http://evil.example"}, {"Origin": "null"},
                      {"Origin": f"http://127.0.0.1:{self.port + 1}"},  # otra web en este PC (otro puerto)
                      {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            code, _, _ = self.call("POST", "/api/apagar", self.post_ok_headers(**extra))
            self.assertEqual(code, 403, extra)
        self.app.apagar.assert_not_called()

    def test_la_propia_interfaz_y_los_programas_si(self):
        for extra in ({"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"},
                      {"Origin": f"http://localhost:{self.port}", "Sec-Fetch-Site": "same-origin"},
                      {}):  # el acceso directo, el aviso, el instalador: sin Origin ni Sec-Fetch-Site
            code, _, body = self.call("POST", "/api/ocultar", self.post_ok_headers(**extra))
            self.assertEqual(code, 200, (extra, body))
        self.assertEqual(self.app.ocultar.call_count, 3)


class ConexionesOciosasTests(ServidorBase):
    """Cada conexión es un hilo de ThreadingHTTPServer, y sin plazo una keep-alive ociosa (un cliente que deja
    conexiones abiertas) lo retenía para siempre: los hilos se acumulaban. Se cierra sola a los Handler.timeout s,
    también tras descartar el cuerpo de un POST rechazado (P0-1, que dejaba el socket sin plazo). El plazo es por
    operación del socket: lo que tarda la app en contestar (el selector de carpetas espera al usuario) no cuenta."""

    CORTO = 0.4  # s, en vez de los de verdad, para no esperar en los tests

    def ociosa(self, data):
        """Manda `data` por un socket nuevo con el plazo corto, lee lo que conteste y espera sin mandar nada más:
        (bytes, cerrada por el servidor antes de 3 s)."""
        with mock.patch.object(server.Handler, "timeout", self.CORTO):
            return exchange(self.port, data, idle=3.0, total=8.0)

    def test_hay_plazo(self):
        self.assertIsInstance(server.Handler.timeout, (int, float))
        self.assertTrue(10 <= server.Handler.timeout <= 120, server.Handler.timeout)

    def test_ociosa_tras_una_peticion_normal_se_cierra(self):
        t = time.time()
        raw, closed = self.ociosa(request_bytes("GET", "/api/ping", self.port, {TOKEN_HEADER: TOKEN}))
        self.assertEqual(statuses(raw), [200], raw[:300])
        self.assertTrue(closed)
        self.assertLess(time.time() - t, 2.5)

    def test_ociosa_tras_descartar_un_cuerpo_tambien(self):
        """Un POST rechazado con un cuerpo pequeño: se lee y se descarta (la conexión sigue, sin Connection: close) y
        el plazo de la conexión vuelve a ser el de siempre, no ninguno."""
        raw, closed = self.ociosa(request_bytes("POST", "/api/ocultar", self.port, {"Content-Type": "text/plain"},
                                                b"hola"))
        self.assertEqual(statuses(raw), [403], raw[:300])
        self.assertNotIn(b"Connection: close", raw)
        self.assertTrue(closed)

    def test_una_respuesta_lenta_no_se_corta(self):
        self.app.config.side_effect = lambda q: time.sleep(3 * self.CORTO) or {"ok": True}
        raw, _ = self.ociosa(request_bytes("GET", "/api/config", self.port, {TOKEN_HEADER: TOKEN}))
        self.assertEqual(statuses(raw), [200], raw[:300])
        self.assertIn(b'{"ok": true}', raw)


# ----------------------------------------------------------------------------- P1-02

class TokenDeSesionTests(ServidorBase):
    def test_api_y_media_exigen_el_token(self):
        for path in ("/api/ping", "/api/config", "/api/tree", "/media?path=x.wav"):
            code, _, body = self.call("GET", path)
            self.assertEqual(code, 403, path)
            self.assertIn("Sesión no válida", json.loads(body)["error"], path)
            code, _, _ = self.call("GET", path, {TOKEN_HEADER: "otro-token"})
            self.assertEqual(code, 403, path)
        code, _, body = self.call("GET", "/api/ping", {TOKEN_HEADER: TOKEN})
        self.assertEqual((code, json.loads(body)), (200, {"app": "audios"}))

    def test_post_exige_el_token(self):
        h = self.post_ok_headers()
        del h[TOKEN_HEADER]
        code, _, body = self.call("POST", "/api/apagar", h)
        self.assertEqual(code, 403)
        self.assertIn("Sesión no válida", json.loads(body)["error"])
        self.app.apagar.assert_not_called()

    def test_sin_token_configurado_nada(self):
        """Si el servidor no tiene token (no lo ha puesto main()), la API no se abre: falla cerrado."""
        with mock.patch.object(server.Handler, "token", None):
            self.assertEqual(self.call("GET", "/api/ping", {TOKEN_HEADER: ""})[0], 403)
            self.assertEqual(self.call("GET", "/api/ping", {TOKEN_HEADER: "None"})[0], 403)

    def test_la_interfaz_recibe_la_cookie_y_la_url_queda_limpia(self):
        code, headers, _ = self.call("GET", f"/?t={TOKEN}")
        self.assertEqual(code, 303)
        self.assertEqual(headers["Location"], "/")
        cookie = headers["Set-Cookie"]
        name = f"audios_sesion_{self.port}"
        self.assertTrue(cookie.startswith(f"{name}={TOKEN};"), cookie)
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)
        self.assertNotIn("Max-Age", cookie)  # de sesión: vale lo que dure el navegador
        code, _, body = self.call("GET", "/api/ping", {"Cookie": f"otra=1; {name}={TOKEN}"})
        self.assertEqual((code, json.loads(body)), (200, {"app": "audios"}))
        # la cookie de otra copia (otro puerto) no vale aquí
        self.assertEqual(self.call("GET", "/api/ping", {"Cookie": f"audios_sesion_1={TOKEN}"})[0], 403)
        self.assertEqual(self.call("GET", "/api/ping", {"Cookie": f"{name}=otro"})[0], 403)

    def test_token_equivocado_sin_cookie(self):
        code, headers, _ = self.call("GET", "/?t=otro")
        self.assertEqual((code, headers["Location"]), (303, "/"))
        self.assertIsNone(headers["Set-Cookie"])

    def test_estaticos_sin_token(self):
        """La página y sus .js/.css no llevan datos: se sirven sin token (la interfaz explica qué hacer si falta)."""
        for path in ("/", "/app.js", "/app.css", "/chuleta.png"):
            self.assertEqual(self.call("GET", path)[0], 200, path)

    def test_media_con_cookie(self):
        tmp = tempfile.mkdtemp(prefix="chuleta-media-")
        with open(os.path.join(tmp, "a.wav"), "wb") as f:
            f.write(b"RIFF" + b"\0" * 100)
        with patched_paths(ROOT=tmp):
            code, _, body = self.call("GET", "/media?path=a.wav",
                                      {"Cookie": f"audios_sesion_{self.port}={TOKEN}"})
        self.assertEqual((code, len(body)), (200, 104))


class FicheroDelTokenTests(unittest.TestCase):
    def test_start_session_escribe_y_el_apagado_lo_borra(self):
        tmp = tempfile.mkdtemp(prefix="chuleta-sesion-")
        with mock.patch.object(server.Handler, "token", None), mock.patch.object(server.atexit, "register"):
            token = server.start_session(tmp)
            self.assertEqual(server.Handler.token, token)
            with open(os.path.join(tmp, "sesion.token"), "rb") as f:
                raw = f.read()
            self.assertEqual(raw, token.encode("utf-8"))  # UTF-8, sin salto de línea
            self.assertGreaterEqual(len(token), 40)
            self.assertRegex(token, r"^[A-Za-z0-9_-]+$")
            self.assertNotEqual(server.start_session(tmp), token)  # uno nuevo en cada arranque
            token = server.Handler.token
            with mock.patch.object(server, "DATA_DIR", tmp), mock.patch.object(server, "SERVER", None), \
                    mock.patch.object(server, "WINDOW", None), mock.patch.object(server.Handler, "app", None), \
                    mock.patch.object(server.logging, "shutdown"):
                server.shutdown("prueba", exit_fn=mock.Mock())
        self.assertFalse(os.path.exists(os.path.join(tmp, "sesion.token")))

    def test_remove_token_no_borra_el_de_otra_copia(self):
        tmp = tempfile.mkdtemp(prefix="chuleta-sesion-")
        write_token_file(tmp, "el-de-otra-copia")
        server.remove_token(tmp, "el-mio")
        self.assertTrue(os.path.exists(os.path.join(tmp, "sesion.token")))
        server.remove_token(tmp, "el-de-otra-copia")
        self.assertFalse(os.path.exists(os.path.join(tmp, "sesion.token")))

    def test_ui_url_lleva_el_token(self):
        with mock.patch.object(server.Handler, "token", "abc"), mock.patch.object(server, "URL", "http://h:1/"):
            self.assertEqual(server.ui_url(), "http://h:1/?t=abc")
            self.assertEqual(server.ui_url("xyz"), "http://h:1/?t=xyz")
        with mock.patch.object(server.Handler, "token", None), mock.patch.object(server, "URL", "http://h:1/"):
            self.assertEqual(server.ui_url(), "http://h:1/")

    def test_open_ui_abre_la_url_con_token(self):
        with mock.patch.object(server.Handler, "token", "abc"), mock.patch.object(server, "URL", "http://h:1/"), \
                mock.patch.object(server.os.path, "exists", return_value=False), \
                mock.patch.object(server.webbrowser, "open") as wb:
            server.open_ui()
        wb.assert_called_once_with("http://h:1/?t=abc")

    def test_la_ventana_abre_la_url_con_token(self):
        src = read("server.py")
        main = src[src.index("def main():"):]
        self.assertIn("Window(ui_url(), DATA_DIR", main)
        self.assertNotIn("Window(URL,", main)


# ----------------------------------------------------------------------------- P0-2

class IconoTests(TempTree):
    MALOS = ('<img src=x onerror=alert(1)>', '"><script>x()</script>', "a'b", "R&D", "<b>", "x" * 9,
             "\x00", "🎯🎯🎯🎯🎯🎯🎯🎯🎯")
    BUENOS = ("📝", "⚠️", "🇪🇸", "👩🏽‍💻", "🏳️‍🌈", "1️⃣", "QA", "★ v2")

    def test_parse_notes_descarta_iconos_con_marcado(self):
        for bad in self.MALOS:
            props, body = server.parse_notes(f"---\ntitulo: Hola\nicono: {bad}\n---\n\nnotas\n")
            self.assertNotIn("icono", props, repr(bad))
            self.assertEqual(props["titulo"], "Hola")
            self.assertEqual(body, "notas\n")

    def test_parse_notes_conserva_los_emojis(self):
        for good in self.BUENOS:
            props, _ = server.parse_notes(f"---\nicono: {good}\n---\n")
            self.assertEqual(props.get("icono"), good, repr(good))

    def test_notes_save_no_guarda_un_icono_con_marcado(self):
        audio = self.wav("Alfa/Reuniones/r.wav", 0.1)
        app = server.App.__new__(server.App)
        app.notes_lock = threading.Lock()
        app.jobs = mock.Mock()
        app.library = mock.Mock()
        r = app.notes_save({"path": "Alfa/Reuniones/r.wav", "props": {"icono": '<img src=x onerror=alert(1)>'}})
        self.assertFalse(r["props"].get("icono"))
        md = os.path.splitext(audio)[0] + ".md"
        self.assertFalse(os.path.exists(md) and "onerror" in server.read_text(md))
        r = app.notes_save({"path": "Alfa/Reuniones/r.wav", "props": {"icono": "🎯"}})
        self.assertEqual(r["props"]["icono"], "🎯")
        self.assertIn("icono: 🎯", server.read_text(md))
        r = app.notes_save({"path": "Alfa/Reuniones/r.wav", "props": {"icono": ["<script>"], "titulo": "T"}})
        self.assertFalse(r["props"].get("icono"))
        self.assertIn("titulo: T", server.read_text(md))
        self.assertNotIn("icono", server.read_text(md))

    def test_el_arbol_no_publica_el_icono_malicioso(self):
        """Un .md con un icono preparado en una carpeta que la app escanea (p. ej. una vinculada y compartida)."""
        self.wav("Alfa/Reuniones/r.wav", 0.1)
        self.write("Alfa/Reuniones/r.md", "---\nicono: <img src=x onerror=alert(1)>\n---\n")
        lib = server.Library(server.Durations(os.path.join(self.data_dir, "d.json")))
        lib.refresh()
        app = server.App.__new__(server.App)
        app.library = lib
        tree = json.dumps(app.tree({}), ensure_ascii=False)
        self.assertIn("r.wav", tree)
        self.assertNotIn("onerror", tree)


def js_line(src, name):
    m = re.search(rf"^const {name} = .*$", src, re.M)
    assert m, name
    return m.group(0)


class IconoEnLaInterfazTests(unittest.TestCase):
    def test_el_icono_solo_se_lee_en_iconof_y_escapado(self):
        """Los 7 sitios que lo pintan (app.js 286, 638, 738; notas.js 36, 219, 695, 753) pasan por iconOf."""
        hits = []
        for n in os.listdir(STATIC):
            if n.endswith(".js"):
                hits += [(n, l) for l in read("static", n).splitlines() if "props.icono" in l]
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0][0], "notas.js")
        self.assertIn("const iconOf = a => esc(", hits[0][1])

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_iconof_escapa_con_node(self):
        code = "\n".join([js_line(read("static", "app.js"), "esc"), js_line(read("static", "notas.js"), "iconOf"),
                          "console.log(JSON.stringify([iconOf({props: {icono: '<img src=x onerror=alert(1)>'}}),"
                          " iconOf({props: {icono: '📝'}}), iconOf({}), iconOf(null), iconOf({props: {}})]))"])
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["&lt;img src=x onerror=alert(1)&gt;", "📝", "", "", ""])


class CspTests(ServidorBase):
    def test_la_pagina_lleva_la_csp(self):
        code, headers, body = self.call("GET", "/")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(server.CSP, CSP)
        self.assertIn(b"<script src=", body)

    def test_la_interfaz_cumple_la_csp(self):
        """Lo que haría saltar la CSP: <script> en línea, manejadores on…= en el HTML o en las plantillas, javascript:,
        eval o new Function, recursos de fuera."""
        html = read("static", "index.html")
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [])
        for n in sorted(os.listdir(STATIC)):
            if not n.endswith((".js", ".html", ".css")):
                continue
            src = read("static", n)
            self.assertEqual(re.findall(r"\son[a-z]+\s*=\s*['\"`$]", src), [], n)
            self.assertNotIn("javascript:", src, n)
            self.assertIsNone(re.search(r"\beval\(|new Function\(|set(?:Timeout|Interval)\(\s*['\"`]", src), n)
            self.assertIsNone(re.search(r"(?:src|href)=[\"']https?://|url\(\s*['\"]?https?://|@import", src), n)

    def test_media_solo_sirve_audio(self):
        """Un .html o .js de una carpeta vinculada no puede salir por /media (sería del mismo origen y se saltaría la
        CSP)."""
        tmp = tempfile.mkdtemp(prefix="chuleta-media-")
        for n in ("x.html", "x.js", "x.svg"):
            with open(os.path.join(tmp, n), "w", encoding="utf-8") as f:
                f.write("<script>alert(1)</script>")
        with patched_paths(ROOT=tmp):
            for n in ("x.html", "x.js", "x.svg"):
                code, headers, _ = self.call("GET", f"/media?path={n}", {TOKEN_HEADER: TOKEN})
                self.assertEqual(code, 404, n)
                self.assertNotIn("text/html", headers.get("Content-Type", ""), n)


class VentanaConCspTests(unittest.TestCase):
    def test_cerrar_pregunta_a_la_pagina_aunque_la_csp_bloquee_eval(self):
        """pywebview mete el script de evaluate_js en un eval(), que la CSP (sin 'unsafe-eval') puede bloquear: el aviso
        «sigue en segundo plano» se pregunta entonces con run_js, que lo ejecuta tal cual."""
        import window
        w = window.Window("http://127.0.0.1:8765/?t=x", tempfile.mkdtemp(), "x.ico", webview=mock.Mock())
        w.w = mock.Mock()
        w.w.evaluate_js.side_effect = RuntimeError("EvalError: Evaluating a string as JavaScript violates CSP")
        w.w.run_js.return_value = "handled"
        w._ask_page_then_hide()
        w.w.run_js.assert_called_once_with('typeof onWindowClosing === "function" ? onWindowClosing() : "no"')
        w.w.hide.assert_not_called()  # la pagina ensena el aviso y pedira /api/ocultar
        w.w.run_js.side_effect = RuntimeError("tampoco")
        w._ask_page_then_hide()
        w.w.hide.assert_called_once()  # sin respuesta de la pagina: se oculta como siempre


# ----------------------------------------------------------------------------- P2-13

class MediaSinCacheTests(ServidorBase):
    def test_media_no_store(self):
        tmp = tempfile.mkdtemp(prefix="chuleta-media-")
        with open(os.path.join(tmp, "a.wav"), "wb") as f:
            f.write(b"RIFF" + b"\0" * 100)
        h = {TOKEN_HEADER: TOKEN}
        with patched_paths(ROOT=tmp):
            for rng, want in ((None, 200), ("bytes=0-9", 206), ("bytes=500-", 416)):
                hh = dict(h, Range=rng) if rng else h
                code, headers, _ = self.call("GET", "/media?path=a.wav", hh)
                self.assertEqual(code, want, rng)
                self.assertEqual(headers["Cache-Control"], "no-store", rng)
        code, headers, _ = self.call("GET", "/api/ping", h)
        self.assertEqual(headers["Cache-Control"], "no-store")


# ----------------------------------------------------------------------------- P1-13 y P1-02 (segunda copia)

def dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def proxy_env():
    """Un proxy fijo del sistema que no responde (lo que urllib toma de HTTP_PROXY o del registro de Windows)."""
    proxy = f"http://127.0.0.1:{dead_port()}"
    env = {"HTTP_PROXY": proxy, "http_proxy": proxy, "NO_PROXY": "", "no_proxy": ""}
    return mock.patch.dict(os.environ, env)


class SegundaCopiaTests(ServidorBase):
    def setUp(self):
        super().setUp()
        self.data = tempfile.mkdtemp(prefix="chuleta-datos-")
        write_token_file(self.data, TOKEN)
        # urlopen guarda el proxy del primer uso: se olvida para que lo lea ahora (como al arrancar la app)
        self._opener = mock.patch.object(urllib.request, "_opener", None)
        self._opener.start()
        self.addCleanup(self._opener.stop)

    def test_already_running_sin_proxy(self):
        with proxy_env(), patched_paths(DATA_DIR=self.data):
            self.assertTrue(server.already_running())

    def test_ask_running_to_show_sin_proxy(self):
        with proxy_env(), patched_paths(DATA_DIR=self.data):
            self.assertTrue(server.ask_running_to_show())
        self.app.mostrar.assert_called_once()

    def test_already_running_valida_el_token(self):
        """Con otra sesión de Windows (otro usuario, el 127.0.0.1 es común), su app no es la nuestra."""
        write_token_file(self.data, "el-de-otro-usuario")
        with patched_paths(DATA_DIR=self.data):
            self.assertFalse(server.already_running())
            self.assertFalse(server.ask_running_to_show())
        self.app.mostrar.assert_not_called()

    def run_main(self, port, data, wait=0.4):
        argv = ["server.py", "--port", str(port), "--data", data]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(server, "App") as App, \
                mock.patch.object(server, "Window"), \
                mock.patch.object(server, "fatal", side_effect=SystemExit(1)) as fatal, \
                mock.patch.object(server, "setup_logging"), \
                mock.patch.object(server, "set_app_id", return_value=False), \
                mock.patch.object(server, "PORT_WAIT", wait, create=True), \
                mock.patch.object(server, "PORT", server.PORT), mock.patch.object(server, "URL", server.URL), \
                mock.patch.object(server, "DATA_DIR", server.DATA_DIR), mock.patch.object(server, "SERVER", None), \
                mock.patch.object(server, "WINDOW", None), mock.patch.object(server.Handler, "app", self.app), \
                mock.patch.object(server.Handler, "token", getattr(server.Handler, "token", None), create=True), \
                mock.patch.object(server, "open_ui") as open_ui, mock.patch.dict(os.environ):
            try:
                server.main()
                exited = False
            except SystemExit:
                exited = True
        return App, fatal, open_ui, exited

    def test_acceso_directo_con_proxy_no_arranca_otra_copia(self):
        """P1-13: con un proxy fijo, el acceso directo no veía la app abierta, creaba App() (atajo, vigilante...) y
        acababa en «lo tiene otro programa». Ahora le pide su ventana."""
        with proxy_env():
            App, fatal, open_ui, exited = self.run_main(self.port, self.data)
        App.assert_not_called()
        fatal.assert_not_called()
        self.assertFalse(exited)
        self.app.mostrar.assert_called_once()

    def test_puerto_ocupado_sin_contestar_no_crea_app(self):
        """P1-13: el puerto se reserva antes de App(); si lo tiene alguien que no contesta, el aviso de siempre."""
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(8)
        try:
            App, fatal, _, exited = self.run_main(busy.getsockname()[1], tempfile.mkdtemp(prefix="chuleta-datos-"))
        finally:
            busy.close()
        self.assertTrue(exited)
        App.assert_not_called()
        fatal.assert_called_once()
        self.assertIn("No se puede usar el puerto", fatal.call_args[0][0])

    def test_app_de_otro_usuario_no_se_toma_por_la_nuestra(self):
        """P1-02: en un Windows multisesión la copia de otro usuario contesta 403 a nuestro token: no se le pide la
        ventana (se la enseñaría en su sesión) y no se espera a que conteste."""
        write_token_file(self.data, "el-de-otro-usuario")
        t = time.time()
        App, fatal, open_ui, exited = self.run_main(self.port, self.data, wait=30)
        self.assertLess(time.time() - t, 10)
        self.assertTrue(exited)
        App.assert_not_called()
        self.app.mostrar.assert_not_called()


class RecordingHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self.server.seen.append({k.lower(): v for k, v in self.headers.items()})
        body = json.dumps({"recording": False, "path": "x.wav"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _reply


class AvisoTests(unittest.TestCase):
    """aviso.py (proceso aparte): manda el token de sesion que lee de la carpeta de datos y no pasa por el proxy."""

    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        self.srv.seen = []
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.fake = type("A", (), {"url": f"http://127.0.0.1:{self.srv.server_address[1]}"})()
        self.data = tempfile.mkdtemp(prefix="chuleta-aviso-")
        write_token_file(self.data, TOKEN)
        p = mock.patch.object(urllib.request, "_opener", None)
        p.start()
        self.addCleanup(p.stop)

    def test_pedir_manda_el_token_sin_proxy(self):
        with proxy_env(), mock.patch.dict(os.environ, {"CHULETA_DATOS": self.data}):
            self.assertEqual(aviso.Aviso._pedir(self.fake, "/api/rec/status")["recording"], False)
            aviso.Aviso._pedir(self.fake, "/api/rec/toggle", {"source": "aviso", "start_only": True})
        get, post = self.srv.seen
        self.assertEqual(get.get(TOKEN_HEADER.lower()), TOKEN)
        self.assertEqual((post.get(TOKEN_HEADER.lower()), post.get("x-audios")), (TOKEN, "1"))

    def test_sin_fichero_no_manda_token(self):
        with mock.patch.dict(os.environ, {"CHULETA_DATOS": tempfile.mkdtemp(prefix="chuleta-vacio-")}):
            aviso.Aviso._pedir(self.fake, "/api/rec/status")
        self.assertNotIn(TOKEN_HEADER.lower(), self.srv.seen[0])


# ----------------------------------------------------------------------------- scripts de Windows

class ScriptsTests(unittest.TestCase):
    def test_instalador_manda_el_token(self):
        """instalar.ps1 y desinstalar.ps1 (instalacion-empresa) hablan con la app abierta solo por Invoke-Api, que pone
        las cabeceras de Get-ApiHeaders: X-Chuleta-Token con el contenido de AudiosApp\\sesion.token (el DATA_DIR de la
        app) y X-Audios en los POST. Ninguna otra llamada HTTP se salta ese camino."""
        for name, fichero in (("instalar.ps1", 'Join-Path $env:LOCALAPPDATA "AudiosApp\\sesion.token"'),
                              ("desinstalar.ps1", 'Join-Path $Datos "sesion.token"')):
            with self.subTest(name):
                ps1 = read(name)
                cabeceras = re.search(r"^function Get-ApiHeaders\(\$post\) \{$(.*?)^\}$", ps1, re.S | re.M).group(1)
                self.assertIn(fichero, cabeceras)
                self.assertIn('$h["X-Chuleta-Token"] = $tok', cabeceras)
                self.assertIn('if ($post) { $h["X-Audios"] = "1" }', cabeceras)
                api = re.search(r"^function Invoke-Api\(.*?^\}$", ps1, re.S | re.M).group(0)
                self.assertIn('Uri = "http://127.0.0.1:$Port$path"', api)
                self.assertIn("Headers = (Get-ApiHeaders $post)", api)
                fuera = ps1.replace(api, "")
                self.assertNotRegex(fuera, r"Invoke-(RestMethod|WebRequest)")
                self.assertGreaterEqual(len(re.findall(r'Invoke-Api "/api/(?:ping|rec/status|apagar)"', fuera)), 3)
        self.assertIn('$Datos = $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "AudiosApp" }',
                      read("desinstalar.ps1"))

    def test_e2e_manda_el_token(self):
        with open(os.path.join(REPO, "scripts", "e2e", "lookfeel_e2e.ps1"), encoding="utf-8") as f:
            ps1 = f.read()
        calls = [l for l in ps1.splitlines() if re.search(r"Invoke-(RestMethod|WebRequest) \"\$Base/api/", l)]
        self.assertGreaterEqual(len(calls), 7)
        for l in calls:
            self.assertIn("(Api-Headers", l, l)
        self.assertIn('Check "e.api_sin_token_403"', ps1)
        self.assertIn('Check "e.apagar_borra_token"', ps1)

    @unittest.skipUnless(sys.platform == "win32", "solo Windows")
    def test_ps1_parsean(self):
        """El parser de PowerShell 5.1 (el que lanza INSTALAR.bat) acepta los dos scripts tocados."""
        for p in (os.path.join(APP, "instalar.ps1"), os.path.join(REPO, "scripts", "e2e", "lookfeel_e2e.ps1")):
            cmd = ("$e = $null; [System.Management.Automation.Language.Parser]::ParseFile($env:PS1, [ref]$null, "
                   "[ref]$e) | Out-Null; if ($e.Count) { $e | ForEach-Object { $_.ToString() }; exit 1 }")
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                               text=True, timeout=120, env=dict(os.environ, PS1=p))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# ----------------------------------------------------------------------------- sondas del workflow lookfeel-spike

SONDAS = ("lookfeel_spike", "lookfeel_webview_probe")


def import_sondas():
    """scripts/lookfeel_spike.py y lookfeel_webview_probe.py (solo Windows: miden la ventana en el runner). Al
    importarse preparan funciones de ctypes.windll: aquí con uno de mentira, también en Windows, porque los tests no
    hacen ninguna llamada Win32 y así no cambian los argtypes de las de verdad para el resto de la suite."""
    with mock.patch.object(ctypes, "windll", mock.MagicMock(), create=True), \
            mock.patch.object(ctypes, "WINFUNCTYPE", mock.MagicMock(), create=True), \
            mock.patch.object(sys, "path", [os.path.join(REPO, "scripts")] + sys.path):
        for name in SONDAS:
            sys.modules.pop(name, None)
        try:
            return importlib.import_module(SONDAS[0]), importlib.import_module(SONDAS[1])
        finally:
            for name in SONDAS:
                sys.modules.pop(name, None)  # llevan los dobles dentro: que no los reutilice otro import


class SondasTests(ServidorBase):
    """Las sondas con el servidor de verdad: el ping manda el token de <data>/sesion.token y la interfaz se abre con
    ?t=<token>, la dirección con la que la abre la app (server.ui_url). La sonda de WebView2 llamaba a wait_ping(40)
    (el 40 hacía de carpeta de datos) y acababa siempre en «sin ping en 40 s», y las dos abrían la interfaz sin el
    token: la página que medían recibía 403 en toda la API."""

    @classmethod
    def setUpClass(cls):
        cls.spike, cls.probe = import_sondas()  # antes de arrancar el servidor (el windll de mentira es global)
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.url = f"http://127.0.0.1:{self.port}/"
        self.out = tempfile.mkdtemp(prefix="chuleta-sonda-")
        self.addCleanup(shutil.rmtree, self.out, True)

    def assert_abre_la_interfaz(self, url):
        """La misma dirección que abre la app, y con ella el servidor entrega la cookie de la sesión."""
        self.assertEqual(url, server.ui_url())
        parts = urllib.parse.urlsplit(url)
        code, headers, _ = self.call("GET", f"{parts.path}?{parts.query}")
        self.assertEqual((code, headers["Location"]), (303, "/"))
        self.assertTrue((headers["Set-Cookie"] or "").startswith(f"audios_sesion_{self.port}={TOKEN};"),
                        headers["Set-Cookie"])

    def test_la_sonda_usa_el_mismo_modulo_del_spike(self):
        self.assertIs(self.probe.spike, self.spike)

    def test_wait_ping_manda_el_token_de_la_carpeta_de_datos(self):
        """Reintenta lo que pasa mientras el servidor arranca: aún sin sesion.token, o con el de un arranque anterior
        (403). Con el token bueno contesta en cuanto el servidor responde."""
        data = os.path.join(self.out, "data")
        with mock.patch.object(self.spike, "URL", self.url):
            self.assertFalse(self.spike.wait_ping(data, timeout=1))
            write_token_file(data, "token-de-un-arranque-anterior")
            self.assertFalse(self.spike.wait_ping(data, timeout=1))
            write_token_file(data, TOKEN)
            self.assertTrue(self.spike.wait_ping(data, timeout=5))

    def test_wait_ping_mal_llamado_falla_al_momento(self):
        """Un error de programación (como aquel wait_ping(40)) sale en vez de dar vueltas hasta agotar el tiempo: el
        except Exception se lo tragaba y la sonda solo decía «sin ping»."""
        with mock.patch.object(self.spike, "URL", self.url):
            t0 = time.time()
            with self.assertRaises(TypeError):
                self.spike.wait_ping(40, timeout=3)
            self.assertLess(time.time() - t0, 1)

    def test_spike_abre_edge_con_la_url_de_la_app(self):
        base = os.path.join(self.out, "base")
        write_token_file(os.path.join(base, "ico", "data"), TOKEN)  # lo que deja el servidor de la variante
        reloj = types.SimpleNamespace(time=time.time, sleep=mock.Mock())  # sin las esperas de 6 y 4 s
        ventana = (101, "Chuleta", "Chrome_WidgetWin_1", 4321)
        with mock.patch.object(self.spike, "URL", self.url), \
                mock.patch.object(self.spike, "make_variant", return_value="server.py"), \
                mock.patch.object(self.spike, "subprocess") as sp, mock.patch.object(self.spike, "time", reloj), \
                mock.patch.object(self.spike, "windows", return_value=[ventana]), \
                mock.patch.object(self.spike, "aumid", return_value="AUMID"), \
                mock.patch.object(self.spike, "window_icons", return_value={}), \
                mock.patch.object(self.spike, "appusermodel_props", return_value={}), \
                mock.patch.object(self.spike, "kill_edge"):
            rec = self.spike.measure_variant("ico", base, self.out, "msedge.exe")
        self.assertEqual((rec["notes"], rec.get("hwnd")), ([], 101))
        edge = [c.args[0] for c in sp.Popen.call_args_list if c.args[0][0] == "msedge.exe"]
        self.assertEqual(len(edge), 1)
        app = [a for a in edge[0] if a.startswith("--app=")]
        self.assertEqual(len(app), 1, edge[0])
        self.assert_abre_la_interfaz(app[0][len("--app="):])

    def test_las_variantes_del_spike_tienen_los_iconos_de_la_app(self):
        """make_variant parte de la cabecera de iconos de index.html. Tras el renombrado (chuleta.ico) el spike seguía
        buscando la de notas.ico: las 4 variantes acababan en AssertionError sin llegar a abrir Edge (run 41212715121
        del workflow lookfeel-spike), y la sonda de WebView2 cargaba static/notas.ico, que ya no existe."""
        self.assertTrue(os.path.isfile(self.probe.ICO), self.probe.ICO)
        base = os.path.join(self.out, "variantes")
        for kind in ("ico", "ico-udd", "svg", "manifest"):
            with contextlib.redirect_stdout(io.StringIO()):  # sin Pillow avisa de que usa el PNG de 256
                static = os.path.join(os.path.dirname(self.spike.make_variant(kind, base)), "static")
            with open(os.path.join(static, "index.html"), encoding="utf-8") as f:
                refs = re.findall(r'<link rel="(?:icon|apple-touch-icon|manifest)" href="/([^"]+)"', f.read())
            if kind == "manifest":
                with open(os.path.join(static, "manifest.webmanifest"), encoding="utf-8") as f:
                    refs += [i["src"].lstrip("/") for i in json.load(f)["icons"]]
            self.assertTrue(refs, kind)
            for ref in refs:
                self.assertTrue(os.path.isfile(os.path.join(static, ref)), f"{kind}: /{ref}")

    def test_webview_probe_ve_el_servidor_y_abre_la_interfaz_con_token(self):
        write_token_file(os.path.join(self.out, "webview-data"), TOKEN)  # lo que deja el servidor al arrancar
        webview = types.ModuleType("webview")
        webview.create_window = mock.Mock(return_value=mock.Mock())
        webview.start = mock.Mock()  # no llama a on_start: sin ventana que medir
        # threading de mentira: el vigilante de la sonda haría os._exit(3) a los 120 s, con la suite en marcha
        with mock.patch.dict(sys.modules, {"webview": webview}), \
                mock.patch("importlib.metadata.version", return_value="0-prueba"), \
                mock.patch.dict(os.environ, {"NOTAS_PROBE_NETFX": "0"}), \
                mock.patch.object(self.probe, "URL", self.url), mock.patch.object(self.spike, "URL", self.spike.URL), \
                mock.patch.object(self.probe, "subprocess"), mock.patch.object(self.probe, "shell32"), \
                mock.patch.object(self.probe, "threading") as hilos, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.probe.main(["sonda", self.out]), 0)
        with open(os.path.join(self.out, "webview.json"), encoding="utf-8") as f:
            steps = {s["step"]: s for s in json.load(f)["steps"]}
        self.assertEqual(steps["servidor"], {"step": "servidor", "ok": True, "url": self.url})
        webview.create_window.assert_called_once()
        self.assert_abre_la_interfaz(webview.create_window.call_args.args[1])
        webview.start.assert_called_once()
        hilos.Thread.assert_called_once()  # el vigilante iba por el threading de mentira


if __name__ == "__main__":
    unittest.main()
