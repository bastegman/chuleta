# -*- coding: utf-8 -*-
"""Claude Code real en Windows, sin sesión iniciada (CI: claude-windows.yml). Mide y comprueba lo que hace Chuleta con
«Con tu cuenta de Claude» (07/11/2026, msgs 1185-1187: «Comprobar» tardó minutos en el PC de Bernardo).

    python scripts/e2e/claude_windows.py nativo|npm|vscode|pasarela

1. Arranca Chuleta ANTES de instalar Claude Code, con su carpeta de datos en una ruta con espacios, «&» y «%».
2. Instala Claude Code: el instalador nativo oficial (irm https://claude.ai/install.ps1 | iex) o npm en una carpeta con
   espacios, «&» y «%» que solo está en el PATH del registro del usuario (como un instalador que cambia el PATH con
   la app ya abierta): así solo la encuentra windows_path(). Con npm, claude.cmd pasa por cmd.exe.
3. Mide a mano `claude --version` y la petición mínima de «Comprobar» (`-p … --tools ""`), en frío y en caliente, con y
   sin CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: cuándo sale el proceso y cuándo se cierran sus tuberías (un hijo que
   las herede y siga vivo, p. ej. el actualizador, haría esperar a quien lee hasta EOF), y qué procesos quedan.
4. Lo que hace Bernardo, por la API de la app: «Comprobar» dos veces y un resumen de una reunión ya transcrita.
5. Escribe resultados.json y copia server.log en NOTAS_ARTEFACTOS. Falla si Chuleta no encuentra claude, si la
   invocación no llega a «sin sesión iniciada» (argumentos rotos por cmd.exe, por ejemplo) o si algo se cuelga.
Sin login ni credenciales: Claude Code no tiene sesión y debe decirlo.

Cuenta de empresa (07/11/2026, msgs 1224-1228):
- vscode: sin programa suelto, solo la extensión de VS Code tal como la deja VS Code al instalarla (el paquete
  win32-x64 de Open VSX, extension/ en %USERPROFILE%\\.vscode\\extensions\\anthropic.claude-code-<versión>-win32-x64).
  Chuleta debe encontrar su copia, decir «Claude Code de VS Code» y pedir que se entre en VS Code.
- pasarela: el instalador nativo y, con Chuleta ya abierta, una pasarela de empresa falsa en este equipo puesta en el
  registro del usuario (ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN falso y ANTHROPIC_MODEL, como haría IT con setx). La
  pasarela no conoce el modelo de «--model sonnet»: Chuleta debe repetir sin --model y contestar con el de la empresa.
  Es la única pasada con una respuesta de verdad de Claude Code (contra la pasarela falsa, sin Anthropic)."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODO = sys.argv[1] if len(sys.argv) > 1 else "nativo"
TEMP = os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP") or "C:\\Temp"
RARO = "Chu leta & 100% y 50% pruebas"  # espacios, «&» y dos «%» (cmd.exe expande %…% en la línea de órdenes)
ART = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(TEMP, "artefactos")
R = {"modo": MODO, "pasos": []}
# sin sesión: «sin sesión iniciada» (engines) o «no tiene la sesión iniciada» (explicar); con la copia de VS Code, que
# se entre en VS Code (allí no hay `claude` en PowerShell)
ESPERADO = "abre VS Code" if MODO == "vscode" else "sesión iniciada"
PROMPT = ("Sigue las INSTRUCCIONES que vienen por la entrada estandar y aplicalas al texto que las sigue. "
          "Responde solo con el resultado pedido.")


for _s in (sys.stdout, sys.stderr):  # la consola del runner es cp1252: lo que no quepa, sustituido (no una excepción)
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def paso(nombre, **datos):
    datos = {"paso": nombre, **datos}
    R["pasos"].append(datos)
    print("·", json.dumps(datos, ensure_ascii=False)[:900], flush=True)


def vivos():
    """Procesos de claude/node/bun que hay ahora mismo (nombre y PID)."""
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace").stdout
    res = []
    for line in out.splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if cols and any(k in cols[0].lower() for k in ("claude", "node", "bun")):
            res.append(f"{cols[0]}:{cols[1] if len(cols) > 1 else '?'}")
    return res


def medir(cmd, stdin_text=None, env=None, cwd=None, tope=240):
    """Lanza cmd con tuberías como AgenteLocal.run y mide: salida del proceso y cierre de stdout/stderr (EOF)."""
    t0 = time.time()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    bufs, eof = {"out": b"", "err": b""}, {}

    def leer(nombre, f):
        bufs[nombre] = f.read()
        eof[nombre] = time.time() - t0
    hilos = [threading.Thread(target=leer, args=("out", p.stdout), daemon=True),
             threading.Thread(target=leer, args=("err", p.stderr), daemon=True)]
    for h in hilos:
        h.start()
    try:
        p.stdin.write((stdin_text or "").encode("utf-8"))
        p.stdin.close()
    except OSError:
        pass
    try:
        rc = p.wait(tope)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        rc = "timeout"
    t_exit = time.time() - t0
    for h in hilos:
        h.join(max(0.1, tope - t_exit))
    return {"rc": rc, "salida_s": round(t_exit, 2), "eof_out_s": round(eof.get("out", -1), 2),
            "eof_err_s": round(eof.get("err", -1), 2), "out": bufs["out"].decode("utf-8", "replace")[:600],
            "err": bufs["err"].decode("utf-8", "replace")[:600], "vivos_despues": vivos()}


def puerto():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    n = s.getsockname()[1]
    s.close()
    return n


class App:
    def __init__(self, root, datos, env):
        self.port, self.datos = puerto(), datos
        self.log = open(os.path.join(TEMP, f"chuleta-{MODO}.log"), "w", encoding="utf-8")
        self.p = subprocess.Popen([sys.executable, os.path.join(root, "_app", "server.py"), "--port", str(self.port),
                                   "--data", datos, "--no-browser"], cwd=root, stdout=self.log,
                                  stderr=subprocess.STDOUT, env=env)
        self.base = f"http://127.0.0.1:{self.port}"
        fin = time.time() + 60
        while time.time() < fin:
            try:
                self.token = open(os.path.join(datos, "sesion.token"), encoding="utf-8").read().strip()
                if self.get("/api/ping").get("app") == "audios":
                    return
            except Exception:
                pass
            time.sleep(0.3)
        raise SystemExit("Chuleta no arrancó")

    def get(self, path):
        r = urllib.request.Request(self.base + path, headers={"X-Chuleta-Token": self.token})
        return json.load(urllib.request.urlopen(r, timeout=30))

    def post(self, path, body, tope=400):
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                   headers={"X-Chuleta-Token": self.token, "X-Audios": "1",
                                            "Content-Type": "application/json"})
        t0 = time.time()
        try:
            out = json.load(urllib.request.urlopen(r, timeout=tope))
            return 200, out, time.time() - t0
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}"), time.time() - t0

    def cerrar(self):
        self.p.terminate()
        try:
            self.p.wait(15)
        except subprocess.TimeoutExpired:
            self.p.kill()
        self.log.close()


def reunion(root):
    """Una reunión ya transcrita (WAV corto + .srt) para pedir su resumen."""
    d = os.path.join(root, "Pruebas")
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, time.strftime("%Y-%m-%d %H-%M-%S") + " - Prueba resumen")
    import struct
    n = 16000 * 2
    fmt = struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
    with open(base + ".wav", "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " + fmt + b"data" + struct.pack("<I", n) + bytes(n))
    with open(base + ".srt", "w", encoding="utf-8") as f:
        f.write("1\n00:00:00,000 --> 00:00:02,000\nHola, esto es una reunión de prueba para el resumen.\n")
    return "Pruebas/" + os.path.basename(base) + ".wav"


class Pasarela:
    """Una pasarela de empresa falsa (API de mensajes de Anthropic, en este equipo): contesta «OK» a cualquier modelo
    salvo los de RECHAZA (404 not_found_error, como una pasarela que no conoce ese nombre) y apunta lo que le llega."""
    RECHAZA = ("sonnet",)

    def __init__(self):
        self.peticiones = []
        pas = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def responder(self, code, body, tipo="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("request-id", "req_pasarela")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                pas.peticiones.append({"metodo": "GET", "ruta": self.path})
                self.responder(404, {"type": "error", "error": {"type": "not_found_error", "message": "no"}})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    body = {}
                modelo = str(body.get("model") or "")
                pas.peticiones.append({"metodo": "POST", "ruta": self.path, "modelo": modelo,
                                       "stream": bool(body.get("stream")),
                                       "credencial": bool(self.headers.get("Authorization")
                                                          or self.headers.get("x-api-key"))})
                if "count_tokens" in self.path:
                    return self.responder(200, {"input_tokens": 5})
                if any(k in modelo.lower() for k in Pasarela.RECHAZA):
                    return self.responder(404, {"type": "error", "error": {"type": "not_found_error",
                                                                           "message": f"model: {modelo}"}})
                msg = {"id": "msg_pasarela", "type": "message", "role": "assistant", "model": modelo, "content": [],
                       "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 1}}
                if not body.get("stream"):
                    msg.update(content=[{"type": "text", "text": "OK"}], stop_reason="end_turn")
                    return self.responder(200, msg)
                ev = [("message_start", {"type": "message_start", "message": msg}),
                      ("content_block_start", {"type": "content_block_start", "index": 0,
                                               "content_block": {"type": "text", "text": ""}}),
                      ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": "text_delta", "text": "OK"}}),
                      ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                      ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn",
                                                                            "stop_sequence": None},
                                         "usage": {"output_tokens": 1}}),
                      ("message_stop", {"type": "message_stop"})]
                self.responder(200, "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in ev).encode(),
                               tipo="text/event-stream")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


def registro_usuario(valores):
    """Variables de usuario en el registro, como `setx` o IT con Chuleta ya abierta (la app no las hereda)."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
        for n, v in valores.items():
            winreg.SetValueEx(k, n, 0, winreg.REG_SZ, v)


def instalar_vscode():
    """La extensión de VS Code como la deja VS Code al instalar el paquete: extension/ del .vsix (Open VSX, win32-x64)
    en %USERPROFILE%\\.vscode\\extensions\\anthropic.claude-code-<versión>-win32-x64. Sin VS Code ni programa suelto."""
    t0 = time.time()
    ua = {"User-Agent": "Chuleta-CI (github actions; claude-windows)"}
    meta = urllib.request.urlopen(urllib.request.Request(
        "https://open-vsx.org/api/Anthropic/claude-code/win32-x64/latest", headers=ua), timeout=60).read()
    meta = json.loads(meta, strict=False)
    ver, url = meta["version"], meta["files"]["download"]
    vsix = os.path.join(TEMP, "claude-code.vsix")
    with urllib.request.urlopen(urllib.request.Request(url, headers=ua), timeout=600) as r, open(vsix, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    dest = os.path.join(os.environ["USERPROFILE"], ".vscode", "extensions", f"anthropic.claude-code-{ver}-win32-x64")
    with zipfile.ZipFile(vsix) as z:
        for m in z.infolist():
            if not m.filename.startswith("extension/") or m.is_dir():
                continue
            out = os.path.join(dest, *m.filename[len("extension/"):].split("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(m) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    exe = os.path.join(dest, "resources", "native-binary", "claude.exe")
    paso("instalar", segundos=round(time.time() - t0, 1), version=ver, mb=round(os.path.getsize(vsix) / 1e6, 1),
         existe=os.path.isfile(exe), ruta=exe)
    return exe


def instalar(env):
    t0 = time.time()
    if MODO == "vscode":
        return instalar_vscode()
    if MODO in ("nativo", "pasarela"):
        # Windows PowerShell 5.1, como el usuario; sin el PSModulePath de pwsh 7 (el paso de la CI corre en pwsh y
        # powershell.exe con ese PSModulePath no encuentra Get-FileHash, que usa el instalador)
        e = {k: v for k, v in env.items() if k.upper() != "PSMODULEPATH"}
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                            "irm https://claude.ai/install.ps1 | iex"], capture_output=True, text=True,
                           errors="replace", timeout=900, env=e)
        exe = os.path.join(os.environ["USERPROFILE"], ".local", "bin", "claude.exe")
        paso("instalar", segundos=round(time.time() - t0, 1), rc=r.returncode, salida=(r.stdout + r.stderr)[-600:],
             existe=os.path.isfile(exe))
        return exe
    prefijo = os.path.join(TEMP, "npm " + RARO)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    r = subprocess.run([npm, "install", "-g", "--prefix", prefijo, "@anthropic-ai/claude-code"], capture_output=True,
                       text=True, errors="replace", timeout=900, env=env)
    exe = os.path.join(prefijo, "claude.cmd")
    paso("instalar", segundos=round(time.time() - t0, 1), rc=r.returncode, salida=(r.stdout + r.stderr)[-600:],
         existe=os.path.isfile(exe), prefijo=prefijo)
    # como un instalador con la app ya abierta: la carpeta va al PATH del usuario en el registro, no al de la app
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
        try:
            actual, tipo = winreg.QueryValueEx(k, "Path")
        except OSError:
            actual, tipo = "", winreg.REG_EXPAND_SZ
        winreg.SetValueEx(k, "Path", 0, tipo, (actual.rstrip(";") + ";" + prefijo).lstrip(";"))
    return exe


def main():
    os.makedirs(ART, exist_ok=True)
    root = os.path.join(TEMP, "raiz " + RARO)
    datos = os.path.join(TEMP, "datos " + RARO)
    shutil.copytree(os.path.join(REPO, "_app"), os.path.join(root, "_app"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    rel = reunion(root)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("ANTHROPIC_API_KEY", None)
    app = App(root, datos, env)
    fallos = []
    try:
        c = app.get("/api/config")["agente"]
        paso("antes_de_instalar", encontrado=c["encontrado"], ruta=c["ruta"])
        exe = instalar(env)
        c = app.get("/api/config")["agente"]
        paso("despues_de_instalar", encontrado=c["encontrado"], ruta=c["ruta"])
        if not c["encontrado"]:
            fallos.append("Chuleta no encuentra claude después de instalarlo")
        ruta = c["ruta"] or exe
        if not os.path.isfile(ruta):
            fallos.append(f"no hay ejecutable de Claude Code tras instalar: {ruta}")
            raise SystemExit(1)  # el finally deja resultados.json y server.log
        est = app.get("/api/agente/estado")
        paso("estado", **est)
        if MODO == "vscode" and (est.get("origen") != "Claude Code de VS Code" or os.path.normcase(ruta)
                                 != os.path.normcase(exe)):
            fallos.append(f"no dice que es la copia de VS Code: {est} · {ruta}")
        pasarela = None
        if MODO == "pasarela":  # con la app ya abierta, como IT con setx: solo lo ve quien lea el registro
            pasarela = Pasarela()
            registro_usuario({"ANTHROPIC_BASE_URL": pasarela.url, "ANTHROPIC_AUTH_TOKEN": "token-falso-de-la-ci",
                              "ANTHROPIC_MODEL": "empresa-modelo"})
            paso("pasarela", url=pasarela.url)
        # medidas a mano: lo mismo que AgenteLocal.run/ClaudeCode.chat
        settings = os.path.join(datos, "agente-claude-settings.json")
        cwd = os.path.join(datos, "agente-cwd")
        os.makedirs(cwd, exist_ok=True)
        with open(settings, "w", encoding="utf-8") as f:
            f.write(json.dumps({"disableAllHooks": True}))
        chat = [ruta, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence", "--strict-mcp-config",
                "--settings", settings, "--model", "sonnet", PROMPT]
        entrada = "INSTRUCCIONES:\nResponde exactamente con la palabra OK, sin nada más.\n\nOK"
        base_env = {k: v for k, v in env.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        for etiqueta, extra in (() if pasarela else (
                ("frio", {}), ("caliente", {}),
                ("sin_trafico_no_esencial", {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}))):
            e = dict(base_env, **extra)
            paso(f"version_{etiqueta}", **medir([ruta, "--version"], env=e, cwd=cwd, tope=120))
            paso(f"peticion_{etiqueta}", **medir(chat, entrada, env=e, cwd=cwd, tope=240))
        # lo que hace Bernardo, por la API de la app
        for i in (1, 2):
            code, out, secs = app.post("/api/agente/comprobar", {"tipo": "claude", "modelo": "sonnet"})
            paso(f"comprobar_{i}", http=code, segundos=round(secs, 1), respuesta=out)
            msg = json.dumps(out, ensure_ascii=False)
            if pasarela:  # la pasarela no conoce «sonnet»: se repite sin --model y contesta con el de la empresa
                if not (code == 200 and out.get("ok") and out.get("modelo_usado") == "empresa-modelo"
                        and out.get("aviso")):
                    fallos.append(f"«Comprobar» {i} con la pasarela no acaba en OK con empresa-modelo: {msg[:300]}")
            elif code == 200 and out.get("ok"):
                paso(f"comprobar_{i}_ok_sin_sesion_inesperado", nota="respondió OK sin sesión iniciada")
            elif ESPERADO not in msg:
                fallos.append(f"«Comprobar» {i} no llega a «{ESPERADO}»: {msg[:300]}")
        if pasarela:
            paso("pasarela_peticiones", peticiones=pasarela.peticiones[:20])
            modelos = [x.get("modelo") for x in pasarela.peticiones if x["metodo"] == "POST"]
            if not any("sonnet" in (m or "") for m in modelos) or "empresa-modelo" not in modelos:
                fallos.append(f"la pasarela no vio sonnet y luego empresa-modelo: {modelos}")
            if not all(x.get("credencial") for x in pasarela.peticiones if x["metodo"] == "POST"):
                fallos.append("alguna petición llegó a la pasarela sin su token (ANTHROPIC_AUTH_TOKEN)")
        app.post("/api/settings", {"procesado": "agente"})
        code, out, _ = app.post("/api/summarize", {"path": rel})
        paso("resumir_pedido", http=code, respuesta=out)
        t0, job = time.time(), None
        while time.time() - t0 < 300:
            jobs = [j for j in app.get("/api/jobs")["jobs"] if j.get("kind") == "summary" and j["path"] == rel]
            job = jobs[-1] if jobs else None
            if job and job["status"] in ("done", "error", "cancelled"):
                break
            time.sleep(1)
        paso("resumen", segundos=round(time.time() - t0, 1), estado=job and job["status"],
             error=job and job.get("error"))
        texto = (job or {}).get("error") or ""
        texto += " " + (((job or {}).get("aviso") or {}).get("frase") or "")
        if pasarela:
            if not job or job["status"] != "done":
                fallos.append(f"el resumen con la pasarela no termina bien: {job and job.get('error')}")
        elif not job or job["status"] != "error" or ESPERADO not in texto:
            fallos.append(f"el resumen no acaba en «{ESPERADO}»: {job and job.get('error')}")
    finally:
        app.cerrar()
        R["fallos"] = fallos
        with open(os.path.join(ART, f"resultados-{MODO}.json"), "w", encoding="utf-8") as f:
            json.dump(R, f, ensure_ascii=False, indent=1)
        for n in ("server.log",):
            if os.path.isfile(os.path.join(datos, n)):
                shutil.copy(os.path.join(datos, n), os.path.join(ART, f"server-{MODO}.log"))
    print("\nFALLOS:" if fallos else "\nTodo bien.", *fallos, sep="\n  ")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
