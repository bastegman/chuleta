# -*- coding: utf-8 -*-
"""Pasar audios del móvil al PC con un código QR («Subir audio › Desde el móvil», 09/11/2026). Bernardo validó la
propuesta con sus cuatro decisiones por defecto (msg 1390):

1. El proyecto se elige en el PC, antes de que salga el código, como al subir un audio: el móvil solo manda.
2. Cada código vale 10 minutos, para todos los audios que quieras, desde un solo móvil (el primero que lo abre).
3. Solo en las redes que Windows tiene como privadas (la de casa). En una pública o en la de la empresa no se ofrece:
   el audio iría por esa red sin cifrar, y muchas no dejan que el móvil vea el PC.
4. El botón va dentro de «Subir audio»: «Desde este PC» y «Desde el móvil».

Mientras el código está en pantalla, Tenca escucha además en la IP de la wifi (no en todas: solo en esa), en un
servidor aparte que solo sabe tres cosas: la página del móvil, sus dos ficheros y recibir un audio. Todo lleva el
código (128 bits) en la ruta y, tras abrirla, la cookie del móvil que la abrió primero. Al cerrar el código, o a los
10 minutos, ese servidor se apaga. El audio va directo del móvil al PC por la wifi: no pasa por internet.

Lo que llega entra por el mismo camino que «Subir audio» (App.subir): se comprueba que se puede transcribir, se guarda
en su carpeta de reunión con su fecha y se pone en la cola."""
import hmac
import ipaddress
import json
import os
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import qrcodegen
from common import APP_NAME, ApiError, WINDOWS, log, log_exc

MINUTOS = 10
MAX_BYTES = 2 * 1024 ** 3  # un audio de más de 2 GB desde el móvil no es una nota de voz: no se acepta
# pruebas (y desarrollo fuera de Windows): la IP en la que escuchar, sin mirar las redes de Windows
ENV_RED = "CHULETA_PRUEBAS_RED_MOVIL"
# los ficheros que el móvil puede pedir (de _app/static): la página no carga nada más, ni de fuera
ESTATICOS = {"movil.js": "application/javascript; charset=utf-8", "movil.css": "text/css; charset=utf-8",
             "nombres-audio.js": "application/javascript; charset=utf-8", "manrope.woff2": "font/woff2",
             "fraunces.woff2": "font/woff2", "logo.svg": "image/svg+xml"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
CSP_AVISO = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"

# los perfiles de red de Windows con sus IPv4 (una línea JSON). NetworkCategory: Public, Private o DomainAuthenticated
PS_REDES = (
    "$ErrorActionPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$o=@(Get-NetConnectionProfile | ForEach-Object {"
    "$i=$_.InterfaceIndex;"
    "[pscustomobject]@{nombre=[string]$_.Name; categoria=[string]$_.NetworkCategory; alias=[string]$_.InterfaceAlias;"
    "ips=@(Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 | ForEach-Object { [string]$_.IPAddress })}});"
    "ConvertTo-Json -InputObject $o -Compress -Depth 3")


# ----------------------------------------------------------------------------- la red

def _ip_de_red_local(ip):
    """Una IPv4 de red local de verdad: privada, y no la de bucle ni una autoasignada (169.254, sin router)."""
    try:
        a = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return False
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local


def elegir_red(perfiles):
    """De los perfiles de red de Windows ([{nombre, categoria, ips}]) la primera privada con una IPv4 de red local:
    {"ok": True, "ip", "red"}. Si no hay ninguna: {"ok": False, "motivo": "publica"|"empresa"|"sin_red", "red"}."""
    otras = []
    for p in perfiles if isinstance(perfiles, list) else []:
        if not isinstance(p, dict):
            continue
        ips = [ip for ip in (p.get("ips") or []) if _ip_de_red_local(ip)]
        if not ips:
            continue
        if p.get("categoria") == "Private":
            return {"ok": True, "ip": str(ips[0]).strip(), "red": str(p.get("nombre") or "")}
        otras.append(p)
    if otras:
        p = otras[0]
        motivo = "empresa" if p.get("categoria") == "DomainAuthenticated" else "publica"
        return {"ok": False, "motivo": motivo, "red": str(p.get("nombre") or "")}
    return {"ok": False, "motivo": "sin_red", "red": ""}


def red_para_movil():
    """Dónde puede escuchar el PC para el móvil (elegir_red), mirando los perfiles de red de Windows."""
    ip = os.environ.get(ENV_RED, "").strip()
    if ip:
        return {"ok": True, "ip": ip, "red": "pruebas"}
    if not WINDOWS:
        return {"ok": False, "motivo": "no_windows", "red": ""}
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_REDES],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        datos = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        log(f"   móvil: no se han podido leer las redes ({e.__class__.__name__})")
        return {"ok": False, "motivo": "error", "red": ""}
    return elegir_red(datos if isinstance(datos, list) else [datos])


MOTIVOS = {
    "publica": "Tu wifi{red} está como pública en Windows, y en una red pública no se reciben audios del móvil: el "
               "audio iría por ella sin cifrar. Si es la de tu casa, márcala como privada en Windows: Configuración › "
               "Red e Internet › Wi-Fi › tu red › Tipo de perfil de red: Privada. Y vuelve a probar.",
    "empresa": "Estás en la red de tu empresa{red}: ahí no se reciben audios del móvil, porque el audio iría por esa "
               "red sin cifrar y muchas no dejan que el móvil vea el PC. Pásalos al PC de otra forma (OneDrive, el "
               "correo) y súbelos con «Desde este PC».",
    "sin_red": "Este PC no está en ninguna wifi. Conéctalo a la misma que el móvil (la de casa) y vuelve a probar.",
    "no_windows": "Recibir audios del móvil solo funciona en Windows.",
    "error": "No he podido mirar en qué red está el PC. Vuelve a probar en un momento.",
}


def motivo_texto(red):
    t = MOTIVOS.get(red.get("motivo"), MOTIVOS["error"])
    return t.replace("{red}", f" («{red['red']}»)" if red.get("red") else "")


# ----------------------------------------------------------------------------- el código QR

def qr_svg(texto):
    """El código QR de `texto` como SVG: módulos negros sobre blanco (también en modo oscuro: es lo que leen las
    cámaras), con el margen de 4 módulos que pide la norma."""
    qr = qrcodegen.QrCode.encode_text(texto, qrcodegen.QrCode.Ecc.MEDIUM)
    n, borde = qr.get_size(), 4
    trazos = [f"M{x + borde},{y + borde}h1v1h-1z" for y in range(n) for x in range(n) if qr.get_module(x, y)]
    lado = n + 2 * borde
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {lado} {lado}" shape-rendering="crispEdges" '
            f'role="img" aria-label="Código QR para abrir {APP_NAME} en el móvil"><rect width="100%" height="100%" '
            f'fill="#fff" stroke="none"/><path d="{"".join(trazos)}" fill="#000" stroke="none"/></svg>')


# ----------------------------------------------------------------------------- la recepción

class Recepcion:
    """Un código: el servidor de la wifi, lo que se ha elegido en el PC (`datos`: carpeta, tema, idioma, contexto y
    modelo, como en /api/subir) y lo que va llegando. `subir(handler, q)` es App.subir."""

    def __init__(self, ip, datos, subir, static_dir, exts=(), minutos=MINUTOS, puerto=0):
        self.ip, self.datos, self._subir = ip, dict(datos), subir
        self.static_dir, self.exts = static_dir, ",".join(sorted(exts))
        self.token = secrets.token_urlsafe(16)
        self.creada = time.time()
        self.caduca = self.creada + minutos * 60
        self.dispositivo = None  # la cookie del móvil que abrió el código primero
        self.recibidos, self.lock = [], threading.Lock()
        self.activa = False
        self.servidor = ThreadingHTTPServer((ip, puerto), MovilHandler)
        self.servidor.daemon_threads = True
        self.servidor.recepcion = self
        self.puerto = self.servidor.server_address[1]
        self._timer = None

    @property
    def url(self):
        return f"http://{self.ip}:{self.puerto}/m/{self.token}"

    def empezar(self):
        self.activa = True
        threading.Thread(target=self.servidor.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True,
                         name="movil").start()
        self._timer = threading.Timer(max(0.0, self.caduca - time.time()), self.parar)
        self._timer.daemon = True
        self._timer.start()
        log(f"📱 recibiendo del móvil en la wifi ({self.puerto}) durante {round((self.caduca - time.time()) / 60)} min")

    def parar(self):
        """Deja de escuchar en la wifi. Lo que estaba llegando termina (su hilo sigue) y se guarda."""
        if not self.activa:
            return
        self.activa = False
        if self._timer:
            self._timer.cancel()
        threading.Thread(target=self._cerrar, daemon=True).start()  # shutdown espera al bucle: desde otro hilo

    def _cerrar(self):
        try:
            self.servidor.shutdown()
            self.servidor.server_close()
        except OSError:
            log_exc()
        log("📱 ya no se reciben audios del móvil")

    def vigente(self):
        return self.activa and time.time() < self.caduca

    def es_el_movil(self, cookie):
        return bool(self.dispositivo and cookie) and hmac.compare_digest(str(cookie), self.dispositivo)

    def nuevo(self, archivo, total):
        with self.lock:
            r = {"id": len(self.recibidos) + 1, "archivo": archivo, "total": total, "bytes": 0, "estado": "llegando",
                 "msg": "", "path": None, "carpeta": None}
            self.recibidos.append(r)
            return r

    def publico(self):
        with self.lock:
            rs = [{"id": r["id"], "archivo": r["archivo"], "estado": r["estado"], "msg": r["msg"], "path": r["path"],
                   "carpeta": r["carpeta"], "total": r["total"],
                   "pct": round(100 * r["bytes"] / r["total"]) if r["total"] else 0} for r in self.recibidos]
        return {"activa": self.vigente(), "url": self.url, "caduca": self.caduca,
                "restante": max(0, round(self.caduca - time.time())), "movil": bool(self.dispositivo),
                "recibidos": rs}


class _Contador:
    """El cuerpo de la petición, contando lo leído (para «Llegando · 46 %» en el PC)."""

    def __init__(self, f, r):
        self.f, self.r = f, r

    def read(self, n=-1):
        b = self.f.read(n)
        self.r["bytes"] += len(b)
        return b


class _Peticion:
    """Lo que App.subir lee de la petición: cabeceras, cuerpo (contado) y si se ha leído entero."""

    def __init__(self, handler, r):
        self.headers, self.rfile, self.body_read = handler.headers, _Contador(handler.rfile, r), False


class MovilHandler(BaseHTTPRequestHandler):
    """El servidor de la wifi: solo /m/<código>, sus ficheros y /m/<código>/subir. Nada de la API de la app."""
    protocol_version = "HTTP/1.1"
    server_version = f"{APP_NAME}"
    timeout = 60  # por operación del socket: un móvil que deja de mandar a medias no deja el hilo colgado

    def log_message(self, fmt, *args):
        pass

    @property
    def rec(self):
        return self.server.recepcion

    def _cookie(self):
        nombre = f"tenca_movil_{self.rec.puerto}="
        for parte in (self.headers.get("Cookie") or "").split(";"):
            parte = parte.strip()
            if parte.startswith(nombre):
                return parte[len(nombre):]
        return ""

    def _send(self, code, body, ctype, extra=None, csp=CSP):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")  # el código va en la dirección
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", csp)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _ruta(self):
        """(código bien, lo que va detrás de /m/<código>) o None si la petición no es para este código."""
        if self.headers.get("Host", "") != f"{self.rec.ip}:{self.rec.puerto}":
            return None  # otro nombre (un DNS que apunta aquí): no
        partes = urlparse(self.path).path.split("/")
        if len(partes) < 3 or partes[1] != "m":
            return None
        if not hmac.compare_digest(partes[2].encode("utf-8", "replace"), self.rec.token.encode("utf-8")):
            return None
        return "/".join(partes[3:])

    def do_GET(self):
        resto = self._ruta()
        if resto is None:
            return self._send(404, "No encontrado", "text/plain; charset=utf-8")
        rec = self.rec
        if not rec.vigente():
            return self._aviso(410, "Este código ya no vale", "Ha caducado o se ha cerrado en el PC. Pide uno nuevo en "
                               f"{APP_NAME}: «Subir audio» › «Desde el móvil».")
        if resto in ESTATICOS:
            return self._estatico(resto)
        if resto not in ("", "estado"):
            return self._send(404, "No encontrado", "text/plain; charset=utf-8")
        cookie, extra = self._cookie(), {}
        with rec.lock:  # el primer móvil que lo abre se queda con el código
            if rec.dispositivo is None:
                rec.dispositivo = secrets.token_urlsafe(16)
                cookie = rec.dispositivo
                extra["Set-Cookie"] = (f"tenca_movil_{rec.puerto}={cookie}; Path=/m/{rec.token}; HttpOnly; "
                                       "SameSite=Strict")
                log("📱 un móvil ha abierto el código")
        if not rec.es_el_movil(cookie):
            return self._aviso(409, "Este código ya lo usa otro móvil", "Cada código es para un solo móvil. Si eres tú "
                               f"desde otro navegador, pide uno nuevo en {APP_NAME}.")
        if resto == "estado":
            return self._json(dict(rec.publico(), url=None))
        return self._send(200, pagina_movil(rec), "text/html; charset=utf-8", extra)

    def _aviso(self, code, titulo, texto):
        self._send(code, pagina_aviso(titulo, texto), "text/html; charset=utf-8", csp=CSP_AVISO)

    def _estatico(self, nombre):
        try:
            with open(os.path.join(self.rec.static_dir, nombre), "rb") as f:
                body = f.read()
        except OSError:
            return self._send(404, "No encontrado", "text/plain; charset=utf-8")
        self._send(200, body, ESTATICOS[nombre])

    def do_POST(self):
        resto = self._ruta()
        if resto is None or resto != "subir":
            return self._send(404, "No encontrado", "text/plain; charset=utf-8")
        rec = self.rec
        if not rec.vigente():
            return self._json({"error": f"Este código ya no vale: pide uno nuevo en {APP_NAME}."}, 410)
        if not rec.es_el_movil(self._cookie()):
            return self._json({"error": "Este código lo usa otro móvil."}, 409)
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length") is None:
            return self._json({"error": "Falta el tamaño del audio."}, 411)
        try:
            total = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            total = -1
        if total < 0:
            return self._json({"error": "Petición no válida."}, 400)
        if total > MAX_BYTES:
            return self._json({"error": "Es demasiado grande para mandarlo desde el móvil (más de 2 GB)."}, 413)
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query, keep_blank_values=True).items()}
        archivo = os.path.basename(str(q.get("archivo") or "").replace("\\", "/"))[:200] or "audio"
        r = rec.nuevo(archivo, total)
        # lo elegido en el PC manda; del móvil, solo el archivo, su nombre propuesto y su fecha
        pedido = dict(rec.datos, archivo=archivo, name=str(q.get("nombre") or "")[:120],
                      fecha=str(q.get("fecha") or ""))
        peticion = _Peticion(self, r)
        try:
            out = self._subir(peticion, pedido)
        except ApiError as e:
            r["estado"], r["msg"] = "error", e.msg
            log(f"📱 no se ha guardado un audio del móvil ({e.msg[:80]})")
            return self._json({"error": e.msg}, e.code if 400 <= e.code < 600 else 400)
        except Exception as e:  # noqa: BLE001 — lo que sea, al móvil con una frase, y al log entero
            log_exc()
            r["estado"], r["msg"] = "error", f"No se ha podido guardar ({e.__class__.__name__})."
            return self._json({"error": r["msg"]}, 500)
        r.update(estado="en_cola" if out.get("job") else "guardado", path=out.get("path"), carpeta=out.get("carpeta"),
                 msg=out.get("aviso") or "")
        r["bytes"] = r["total"]
        return self._json({"ok": True, "estado": r["estado"], "aviso": r["msg"] or None})

    def _subir(self, peticion, pedido):
        return self.rec._subir(peticion, pedido)

    def do_HEAD(self):  # sin HEAD: un visor de enlaces que «mira» la dirección no se quedaría con el código
        self._send(405, "", "text/plain; charset=utf-8", {"Allow": "GET, POST"})


# ----------------------------------------------------------------------------- las páginas del móvil

def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&#39;"))


def _cabeza(titulo, rec=None):
    base = f"/m/{quote(rec.token)}/" if rec else ""
    css = f'<link rel="stylesheet" href="{base}movil.css">' if rec else ""
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="robots" content="noindex">'
            f'<meta name="color-scheme" content="light dark"><title>{_esc(titulo)}</title>{css}</head>')


def pagina_aviso(titulo, texto):
    """Una página sin nada más que un aviso (código caducado o de otro móvil); sin ficheros: sin el código no se
    pueden pedir."""
    estilo = ("body{font:17px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;padding:32px 22px;"
              "background:#f4f1e8;color:#18211d}h1{font-size:22px;margin:0 0 10px}"
              "@media(prefers-color-scheme:dark){body{background:#101613;color:#e8ede9}}")
    return (_cabeza(titulo).replace("</head>", f"<style>{estilo}</style></head>")
            + f"<body><h1>{_esc(titulo)}</h1><p>{_esc(texto)}</p></body></html>")


def pagina_movil(rec):
    """La página «Enviar a Tenca» del móvil: el botón para elegir grabaciones y la lista de lo enviado. Lo que hace va
    en movil.js; los datos que necesita, en atributos del <body>."""
    base = f"/m/{quote(rec.token)}/"
    return (_cabeza(f"Enviar a {APP_NAME}", rec)
            + f'<body data-base="{_esc(base)}" data-caduca="{int(rec.caduca * 1000)}"'
            f' data-ahora="{int(time.time() * 1000)}" data-exts="{_esc(rec.exts)}" data-app="{_esc(APP_NAME)}">'
            f'<main class="m"><header class="m-cab"><img src="{base}logo.svg" alt="" class="m-logo"><h1>Enviar a '
            f'{_esc(APP_NAME)}</h1></header>'
            '<p class="m-estado" id="mEstado"><i class="m-punto"></i><span>Conectado con tu PC</span>'
            ' · caduca en <b id="mCaduca">10:00</b></p>'
            '<label class="m-elegir" id="mElegir"><input type="file" id="mArchivos" multiple>'
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V3M7 8l5-5 5 5M4 15v4a2 2 0 0 0 2 2h12a2 2 '
            '0 0 0 2-2v-4"/></svg><span>Elegir grabaciones</span></label>'
            '<div class="m-ayuda"><p><b>En iPhone</b>, las Notas de voz y los audios de WhatsApp se guardan antes en '
            'Archivos: <b>Compartir › Guardar en Archivos</b>.</p><p><b>En Android</b> los encuentras en <b>Audio</b> '
            'o en la carpeta de tu grabadora.</p></div>'
            '<ul class="m-lista" id="mLista" aria-live="polite"></ul>'
            '<p class="m-pie">Cuando termine, puedes cerrar esta página.</p></main>'
            f'<script src="{base}nombres-audio.js"></script><script src="{base}movil.js"></script></body></html>')
