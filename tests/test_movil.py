# -*- coding: utf-8 -*-
"""«Subir audio › Desde el móvil» (movil.py, 09/11/2026; la lámina validada en el msg 1390): qué red vale (solo una
privada de Windows), el código QR, y el servidor de la wifi: solo el código, un solo móvil, 10 minutos, nada de la API
de la app, y lo recibido por el mismo camino que «Subir audio». Sin red de verdad: todo en 127.0.0.1. La interfaz (el
menú, la tarjeta, el móvil emulado) la prueba test_ui_movil."""
import http.client
import json
import os
import re
import time
import unittest
from unittest import mock

from support import APP, TempTree, server  # noqa: F401  (support prepara sys.path)
import movil  # noqa: E402
from common import ApiError  # noqa: E402

STATIC = os.path.join(APP, "static")


class RedTests(unittest.TestCase):
    def test_elige_la_privada(self):
        perfiles = [{"nombre": "Hotel", "categoria": "Public", "ips": ["10.1.2.3"]},
                    {"nombre": "Casa", "categoria": "Private", "ips": ["169.254.3.4", "192.168.1.34"]}]
        self.assertEqual(movil.elegir_red(perfiles), {"ok": True, "ip": "192.168.1.34", "red": "Casa"})

    def test_publica_empresa_y_sin_red(self):
        self.assertEqual(movil.elegir_red([{"nombre": "Hotel", "categoria": "Public", "ips": ["10.0.0.5"]}]),
                         {"ok": False, "motivo": "publica", "red": "Hotel"})
        self.assertEqual(movil.elegir_red([{"nombre": "corp.example", "categoria": "DomainAuthenticated",
                                            "ips": ["172.20.1.9"]}])["motivo"], "empresa")
        # una privada sin IPv4 de red local (solo la autoasignada, la de bucle, 0.0.0.0 o una VPN 100.64) no vale
        for ips in (["169.254.1.1"], ["127.0.0.1"], ["8.8.8.8"], [], None, ["no es una ip"], ["0.0.0.0"],
                    ["100.101.1.2"]):
            with self.subTest(ips=ips):
                self.assertEqual(movil.elegir_red([{"nombre": "X", "categoria": "Private", "ips": ips}])["motivo"],
                                 "sin_red")
        self.assertEqual(movil.elegir_red("raro")["motivo"], "sin_red")

    def test_la_red_con_ruta_a_internet_manda(self):
        """Un adaptador virtual (Hyper-V, WSL) marcado como privado y sin ruta no se elige: el móvil no llegaría."""
        wsl = {"nombre": "vEthernet (WSL)", "categoria": "Private", "ips": ["172.24.80.1"], "ruta": False,
               "metrica": 9999}
        casa = {"nombre": "Casa", "categoria": "Private", "ips": ["192.168.1.34"], "ruta": True, "metrica": 35}
        hotel = {"nombre": "Hotel", "categoria": "Public", "ips": ["10.0.0.5"], "ruta": True, "metrica": 25}
        vpn = {"nombre": "corp.example", "categoria": "DomainAuthenticated", "ips": ["10.8.0.2"], "ruta": True,
               "metrica": 1}
        self.assertEqual(movil.elegir_red([wsl, casa])["ip"], "192.168.1.34")
        self.assertEqual(movil.elegir_red([wsl, hotel]), {"ok": False, "motivo": "publica", "red": "Hotel"})
        self.assertEqual(movil.elegir_red([vpn, casa])["ip"], "192.168.1.34")  # la de casa, aunque la VPN pese menos
        self.assertEqual(movil.elegir_red([vpn, wsl])["motivo"], "empresa")
        self.assertEqual(movil.elegir_red([wsl])["ip"], "172.24.80.1")  # sin ninguna ruta: como antes

    def test_textos_y_fuera_de_windows(self):
        self.assertIn("(«Hotel»)", movil.motivo_texto({"motivo": "publica", "red": "Hotel"}))
        self.assertIn("Tipo de perfil de red: Privada", movil.motivo_texto({"motivo": "publica", "red": ""}))
        self.assertIn("«Desde este PC»", movil.motivo_texto({"motivo": "empresa", "red": ""}))
        with mock.patch.dict(os.environ, {movil.ENV_RED: ""}), mock.patch.object(movil, "WINDOWS", False):
            self.assertEqual(movil.red_para_movil()["motivo"], "no_windows")
        with mock.patch.dict(os.environ, {movil.ENV_RED: "127.0.0.1"}):
            self.assertEqual(movil.red_para_movil(), {"ok": True, "ip": "127.0.0.1", "red": "pruebas"})

    def test_lee_los_perfiles_de_windows(self):
        salida = json.dumps({"nombre": "Casa", "categoria": "Private", "alias": "Wi-Fi", "ruta": True, "metrica": 0,
                             "ips": ["192.168.0.12"]})
        r = mock.Mock(stdout=salida)  # (UTF-8: un nombre de red con «ñ» no rompe la lectura)
        with mock.patch.dict(os.environ, {movil.ENV_RED: ""}), mock.patch.object(movil, "WINDOWS", True), \
                mock.patch.object(movil.subprocess, "run", return_value=r) as run:
            self.assertEqual(movil.red_para_movil(), {"ok": True, "ip": "192.168.0.12", "red": "Casa"})
        self.assertIn("Get-NetConnectionProfile", run.call_args[0][0][-1])
        with mock.patch.dict(os.environ, {movil.ENV_RED: ""}), mock.patch.object(movil, "WINDOWS", True), \
                mock.patch.object(movil.subprocess, "run", side_effect=OSError("sin powershell")), \
                mock.patch.object(movil, "log"):
            self.assertEqual(movil.red_para_movil()["motivo"], "error")

    @unittest.skipUnless(os.name == "nt", "solo en Windows")
    def test_powershell_de_verdad(self):
        """En el Windows de la CI: el script de PowerShell corre y se entiende (el resultado depende de la red del
        runner; lo que no puede ser es «error»)."""
        with mock.patch.dict(os.environ, {movil.ENV_RED: ""}), mock.patch.object(movil, "PS_TIEMPO", 120):
            r = movil.red_para_movil()  # (en la CI, PowerShell en frío ha tardado más de 15 s: aquí, margen)
        self.assertIn(r.get("motivo", "ok"), ("ok", "publica", "empresa", "sin_red"), r)


class QrTests(unittest.TestCase):
    def test_svg(self):
        url = "http://192.168.1.34:51234/m/" + "A" * 22
        svg = movil.qr_svg(url)
        self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
        self.assertIn('stroke="none"', svg)  # sin el trazo de los iconos de la app (lo volvía una mancha)
        qr = movil.qrcodegen.QrCode.encode_text(url, movil.qrcodegen.QrCode.Ecc.MEDIUM)
        n = qr.get_size()
        self.assertIn(f'viewBox="0 0 {n + 8} {n + 8}"', svg)  # con el margen de 4 módulos
        self.assertEqual(svg.count("h1v1h-1z"), sum(qr.get_module(x, y) for y in range(n) for x in range(n)))


# ----------------------------------------------------------------------------- el servidor de la wifi

class Peticiones:
    def __init__(self, rec):
        self.rec = rec

    def pedir(self, metodo, ruta, body=None, cookie=None, host=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.rec.puerto, timeout=10)
        h = dict({"Host": host or f"127.0.0.1:{self.rec.puerto}"}, **(headers or {}))
        if cookie:
            h["Cookie"] = cookie
        c.request(metodo, ruta, body=body, headers=h)
        r = c.getresponse()
        datos = r.read()
        c.close()
        return r, datos


class ServidorWifiTests(unittest.TestCase):
    def setUp(self):
        self.subidos = []

        def subir(peticion, q):
            n = int(peticion.headers.get("Content-Length") or 0)
            cuerpo = peticion.rfile.read(n)
            peticion.body_read = True
            self.subidos.append((dict(q), cuerpo))
            if q["archivo"].endswith(".txt"):
                raise ApiError("«x.txt» no es un audio que Tenca sepa leer.")
            return {"path": "Proyecto/2026-11-09 10-00-00 - Nota.m4a", "job": "j1", "carpeta": "Proyecto"}
        datos = {"folder": "Proyecto", "vocab": "Proyecto/Vocabulario/General.txt", "language": "es", "contexto": "",
                 "preset": "rapido"}
        with mock.patch.object(movil, "log"):
            self.rec = movil.Recepcion("127.0.0.1", datos, subir, STATIC, {".m4a", ".ogg"})
            self.rec.empezar()
        self.addCleanup(self._parar)
        self.p = Peticiones(self.rec)
        self.ruta = f"/m/{self.rec.token}"

    def _parar(self):
        with mock.patch.object(movil, "log"):
            self.rec.parar()
            time.sleep(0.4)

    def abrir(self):
        with mock.patch.object(movil, "log"):
            r, datos = self.p.pedir("GET", self.ruta)
        self.assertEqual(r.status, 200)
        galleta = r.getheader("Set-Cookie")
        self.assertRegex(galleta, rf"^tenca_movil_{self.rec.puerto}=[^;]+; Path=/m/{re.escape(self.rec.token)}; "
                                  r"Max-Age=(6[0-5][0-9]); HttpOnly; SameSite=Strict$")
        return galleta.split(";")[0], datos.decode("utf-8")

    def test_la_pagina_y_un_solo_movil(self):
        cookie, html = self.abrir()
        self.assertIn("Enviar a Tenca", html)
        self.assertIn('data-exts=".m4a,.ogg"', html)
        self.assertIn(f'src="/m/{self.rec.token}/movil.js"', html)
        self.assertNotIn("http", html.split("<body", 1)[1])  # nada de fuera
        r, _ = self.p.pedir("GET", self.ruta, cookie=cookie)  # el mismo móvil, otra vez
        self.assertEqual(r.status, 200)
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")  # el código va en la dirección
        r, datos = self.p.pedir("GET", self.ruta)  # otro móvil: no
        self.assertEqual(r.status, 409)
        self.assertIn("Este código ya lo usa otro móvil", datos.decode("utf-8"))
        r, _ = self.p.pedir("GET", self.ruta, cookie=f"tenca_movil_{self.rec.puerto}=otra")
        self.assertEqual(r.status, 409)

    def test_estado_no_ata_al_movil(self):
        """Solo abrir la página ata el código a un móvil: algo que pida /estado antes no deja fuera al de verdad."""
        r, _ = self.p.pedir("GET", f"{self.ruta}/estado")
        self.assertEqual(r.status, 409)
        self.assertIsNone(self.rec.dispositivo)
        self.abrir()

    def test_solo_lo_suyo(self):
        cookie, _ = self.abrir()
        for ruta in ("/", "/api/config", "/m/", f"/m/{'x' * 22}", f"{self.ruta}/../server.py", f"{self.ruta}/app.js",
                     f"{self.ruta}/../../movil.js", f"{self.ruta}/index.html", "/media?path=x.wav"):
            with self.subTest(ruta=ruta):
                r, _ = self.p.pedir("GET", ruta, cookie=cookie)
                self.assertEqual(r.status, 404)
        r, _ = self.p.pedir("GET", self.ruta, cookie=cookie, host="tenca.example:80")  # otro nombre: no
        self.assertEqual(r.status, 404)
        r, cuerpo = self.p.pedir("GET", f"{self.ruta}/movil.js", cookie=cookie)
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "application/javascript; charset=utf-8"))
        with open(os.path.join(STATIC, "movil.js"), "rb") as f:
            self.assertEqual(cuerpo, f.read())
        r, _ = self.p.pedir("HEAD", self.ruta)  # un visor de enlaces no se queda con el código
        self.assertEqual(r.status, 405)

    def test_recibir(self):
        cookie, _ = self.abrir()
        q = "archivo=Nota%2012.m4a&nombre=Nota%2012&fecha=1762680000000&folder=Otra&preset=nube"
        r, datos = self.p.pedir("POST", f"{self.ruta}/subir?{q}", body=b"audio" * 1000, cookie=cookie)
        self.assertEqual(r.status, 200, datos)
        self.assertEqual(json.loads(datos), {"ok": True, "estado": "en_cola", "aviso": None})
        # un nombre larguísimo se corta sin perder la extensión
        largo = "x" * 300 + ".m4a"
        r, _ = self.p.pedir("POST", f"{self.ruta}/subir?archivo={largo}", body=b"a", cookie=cookie)
        self.assertEqual(r.status, 200)
        self.assertEqual((len(self.subidos[1][0]["archivo"]), self.subidos[1][0]["archivo"][-4:]), (200, ".m4a"))
        q0, cuerpo = self.subidos[0]
        self.assertEqual(cuerpo, b"audio" * 1000)
        # lo elegido en el PC manda: del móvil solo el archivo, su nombre y su fecha (no la carpeta ni el modelo)
        self.assertEqual(q0, {"folder": "Proyecto", "vocab": "Proyecto/Vocabulario/General.txt", "language": "es",
                              "contexto": "", "preset": "rapido", "archivo": "Nota 12.m4a", "name": "Nota 12",
                              "fecha": "1762680000000"})
        pub = self.rec.publico()
        self.assertEqual([(x["archivo"], x["estado"], x["pct"], x["path"]) for x in pub["recibidos"]][0],
                         ("Nota 12.m4a", "en_cola", 100, "Proyecto/2026-11-09 10-00-00 - Nota.m4a"))
        self.assertTrue(pub["movil"])
        # un fallo: al móvil con su frase y al PC en la lista
        with mock.patch.object(movil, "log"):
            r, datos = self.p.pedir("POST", f"{self.ruta}/subir?archivo=x.txt", body=b"hola", cookie=cookie)
        self.assertEqual(r.status, 400)
        self.assertIn("no es un audio", json.loads(datos)["error"])
        self.assertEqual(self.rec.publico()["recibidos"][-1]["estado"], "error")

    def test_recibir_sin_ser_el_movil_ni_tamano(self):
        r, _ = self.p.pedir("POST", f"{self.ruta}/subir?archivo=a.m4a", body=b"x")
        self.assertEqual(r.status, 409)  # sin abrir la página: no hay móvil
        cookie, _ = self.abrir()
        r, _ = self.p.pedir("POST", f"{self.ruta}/subir?archivo=a.m4a", body=b"x",
                            cookie=f"tenca_movil_{self.rec.puerto}=otra")
        self.assertEqual(r.status, 409)
        r, _ = self.p.pedir("POST", f"{self.ruta}/subir?archivo=a.m4a", body=b"x", cookie=cookie,
                            headers={"Content-Length": str(movil.MAX_BYTES + 1)})
        self.assertEqual(r.status, 413)
        self.assertEqual(self.subidos, [])

    def test_caduca_y_se_cierra(self):
        cookie, _ = self.abrir()
        self.rec.caduca = time.time() - 1  # a los 10 minutos (o cerrado en el PC): ya no vale
        r, datos = self.p.pedir("GET", self.ruta, cookie=cookie)
        self.assertEqual(r.status, 410)
        self.assertIn("Este código ya no vale", datos.decode("utf-8"))
        r, datos = self.p.pedir("POST", f"{self.ruta}/subir?archivo=a.m4a", body=b"x", cookie=cookie)
        self.assertEqual(r.status, 410)
        self.assertFalse(self.rec.publico()["activa"])
        self._parar()  # y deja de escuchar
        with self.assertRaises(OSError):
            self.p.pedir("GET", self.ruta, cookie=cookie)


# ----------------------------------------------------------------------------- la API de la app

class ApiTests(TempTree):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.root, "Proyecto"))
        from test_carpetas import make_app
        self.app = make_app(self)
        self.addCleanup(self.app._movil_parar)
        self._log = mock.patch.object(movil, "log")
        self._log.start()
        self.addCleanup(self._log.stop)
        self.addCleanup(setattr, server.App, "_movil_ultima", None)
        self.addCleanup(setattr, server.App, "_movil_red", (0.0, None))

    def test_abrir_estado_datos_y_cerrar(self):
        with mock.patch.dict(os.environ, {movil.ENV_RED: "127.0.0.1"}):
            r = self.app.movil_abrir({"folder": "Proyecto", "language": "es", "preset": "rapido"})
        self.assertTrue(r["ok"] and r["activa"])
        self.assertTrue(r["url"].startswith("http://127.0.0.1:"))
        self.assertIn("<svg", r["qr"])
        self.assertEqual(r["restante"], 600)
        rec = server.App._movil_rec
        self.assertEqual(rec.datos["folder"], "Proyecto")
        self.app.movil_datos({"folder": "", "language": "en"})  # «Cambiar» con el código en pantalla
        self.assertEqual((rec.datos["folder"], rec.datos["language"]), ("", "en"))
        self.assertEqual(self.app.movil_estado({})["url"], rec.url)
        # otro código: el anterior deja de valer
        with mock.patch.dict(os.environ, {movil.ENV_RED: "127.0.0.1"}):
            r2 = self.app.movil_abrir({"folder": ""})
        self.assertNotEqual(r2["url"], r["url"])
        self.assertFalse(rec.vigente())
        out = self.app.movil_cerrar({})
        self.assertFalse(out["activa"])
        # cerrado, la tarjeta sigue viendo lo que recibió (y lo que estaba llegando, al terminar)
        e = self.app.movil_estado({})
        self.assertEqual((e["activa"], e["recibidos"], e["url"]), (False, [], r2["url"]))
        self.assertIsNone(server.App._movil_rec)
        with self.assertRaises(ApiError):
            self.app.movil_datos({"folder": ""})

    def test_la_red_de_hace_un_momento_vale(self):
        """«Mostrar el código» justo después de abrir la tarjeta no vuelve a lanzar PowerShell (puede tardar); una red
        mala no se guarda: tras cambiarla en Windows, se vuelve a mirar."""
        buena = {"ok": True, "ip": "127.0.0.1", "red": "Casa"}
        with mock.patch.object(movil, "red_para_movil", return_value=buena) as m:
            self.app.movil_red({})
            self.app.movil_abrir({"folder": ""})
        self.assertEqual(m.call_count, 1)
        server.App._movil_red = (0.0, None)
        with mock.patch.object(movil, "red_para_movil", return_value={"ok": False, "motivo": "publica", "red": ""}):
            self.app.movil_red({})
        self.assertEqual(server.App._movil_red, (0.0, None))

    def test_sin_red_privada_no_hay_codigo(self):
        with mock.patch.object(movil, "red_para_movil", return_value={"ok": False, "motivo": "publica", "red": "Bar"}):
            r = self.app.movil_abrir({"folder": ""})
            self.assertEqual(self.app.movil_red({})["motivo"], "publica")
        self.assertFalse(r["ok"])
        self.assertIn("(«Bar») está como pública", r["texto"])
        self.assertIsNone(server.App._movil_rec)

    def test_la_carpeta_se_comprueba_antes(self):
        with mock.patch.dict(os.environ, {movil.ENV_RED: "127.0.0.1"}):
            for malo in ("No existe", "_app", "../fuera"):
                with self.subTest(folder=malo), self.assertRaises(ApiError):
                    self.app.movil_abrir({"folder": malo})
        self.assertIsNone(server.App._movil_rec)

    def test_apagar_cierra_el_codigo(self):
        with mock.patch.dict(os.environ, {movil.ENV_RED: "127.0.0.1"}):
            self.app.movil_abrir({"folder": ""})
        rec = server.App._movil_rec
        self.app._movil_parar()
        self.assertFalse(rec.vigente())

    def test_el_diagnostico_no_lleva_nombres(self):
        for linea, esperado in (("📱 recibiendo del móvil en la wifi (51234) durante 10 min",
                                 "📱 recibiendo del móvil en la wifi (51234) durante 10 min"),
                                ("📱 no se ha guardado un audio del móvil (Comité Omega)",
                                 "📱 no se ha guardado un audio del móvil (<texto>)")):
            self.assertEqual(server.diag_line(linea), esperado)


if __name__ == "__main__":
    unittest.main()
