# -*- coding: utf-8 -*-
"""Claude Code real en Windows, sin sesión iniciada (CI: claude-windows.yml). Mide y comprueba lo que hace Chuleta con
«Con tu cuenta de Claude» (07/11/2026, msgs 1185-1187: «Comprobar» tardó minutos en el PC de Bernardo).

    python scripts/e2e/claude_windows.py nativo|npm

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
Sin login ni credenciales: Claude Code no tiene sesión y debe decirlo."""
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

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODO = sys.argv[1] if len(sys.argv) > 1 else "nativo"
TEMP = os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP") or "C:\\Temp"
RARO = "Chu leta & 100% y 50% pruebas"  # espacios, «&» y dos «%» (cmd.exe expande %…% en la línea de órdenes)
ART = os.environ.get("NOTAS_ARTEFACTOS") or os.path.join(TEMP, "artefactos")
R = {"modo": MODO, "pasos": []}
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


def instalar(env):
    t0 = time.time()
    if MODO == "nativo":
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
        for etiqueta, extra in (("frio", {}), ("caliente", {}),
                                ("sin_trafico_no_esencial", {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})):
            e = dict(base_env, **extra)
            paso(f"version_{etiqueta}", **medir([ruta, "--version"], env=e, cwd=cwd, tope=120))
            paso(f"peticion_{etiqueta}", **medir(chat, entrada, env=e, cwd=cwd, tope=240))
        # lo que hace Bernardo, por la API de la app
        for i in (1, 2):
            code, out, secs = app.post("/api/agente/comprobar", {"tipo": "claude", "modelo": "sonnet"})
            paso(f"comprobar_{i}", http=code, segundos=round(secs, 1), respuesta=out)
            msg = json.dumps(out, ensure_ascii=False)
            if code == 200 and out.get("ok"):
                paso(f"comprobar_{i}_ok_sin_sesion_inesperado", nota="respondió OK sin sesión iniciada")
            elif "sesión iniciada" not in msg:  # «sin sesión iniciada» (engines) o «no tiene la sesión iniciada» (explicar)
                fallos.append(f"«Comprobar» {i} no llega a «sesión iniciada»: {msg[:300]}")
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
        if not job or job["status"] != "error" or "sesión iniciada" not in texto:
            fallos.append(f"el resumen no acaba en «sin sesión iniciada»: {job and job.get('error')}")
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
