# -*- coding: utf-8 -*-
"""«Quién habla» con una reunión larga: pico de memoria del subproceso de voces, con la app de este commit y, si se da,
con la de antes (fix-subir-memoria, 09/11/2026: con un audio de 55 min, la de `fe29522` llegaba a ~2,3 GB y en el
portátil de Bernardo acabó en «bad allocation»).

    python scripts/e2e/voces_memoria.py --salida DIR [--minutos 55] [--antes DIR_APP] [--tope MB]

1. Sintetiza turnos con las voces del sistema (SAPI en Windows; `say` en macOS) y los repite hasta --minutos (WAV
   16 kHz mono).
2. Baja los modelos de voces con voices.ensure_models (sha256 anclado) a DIR/datos.
3. Ejecuta `voices.py --diarize` con cada app en un proceso nuevo que, al acabar, mide su propio pico: en Windows,
   PeakPagefileUsage (memoria comprometida, la que se acaba en un «bad allocation») y PeakWorkingSetSize; en macOS,
   ri_lifetime_max_phys_footprint y ru_maxrss.
4. Escribe DIR/voces_memoria.json y sale con 1 si la de ahora no baja de la de antes o pasa de --tope MB.
Necesita sherpa-onnx, numpy, PyAV y requests (lo que instala INSTALAR.bat para «Quién habla»).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import wave

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRASES = (
    "Good morning everyone, let's start with the status of the platform migration and the milestones for this quarter.",
    "Thanks. On my side, the data team has finished moving the reports and we are validating them with the business.",
    "I am worried about the second phase, because we depend on the vendor delivering the integrations on time.",
    "I spoke with them yesterday and they promise a first version on the fifteenth, but without the load tests.",
    "Then I suggest we run the load tests ourselves in parallel, so that we do not lose two weeks waiting.",
    "Agreed, but we will need a staging environment with anonymised data, and that takes time to prepare.",
)

# en el proceso hijo: voices.main y, al acabar, su propio pico de memoria
HIJO = r'''
import ctypes, json, os, sys
app = sys.argv[1]
sys.path.insert(0, app)
os.chdir(app)
import voices
rc = voices.main(sys.argv[3:])
out = {"rc": rc}
if sys.platform == "win32":
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                           "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                           "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    c = PMC()
    c.cb = ctypes.sizeof(c)
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    ok = k32.K32GetProcessMemoryInfo(ctypes.c_void_p(k32.GetCurrentProcess()), ctypes.byref(c), c.cb)
    out.update(ok=bool(ok), comprometida_mb=round(c.PeakPagefileUsage / 2**20), working_set_mb=round(
        c.PeakWorkingSetSize / 2**20))
elif sys.platform == "darwin":
    import resource
    buf = (ctypes.c_uint64 * 64)()
    ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage(os.getpid(), 4, ctypes.byref(buf))
    out.update(comprometida_mb=round(list(buf)[2:][28] / 2**20),
               working_set_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20))
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(out, f)
'''


def sintetizar(dir_, minutos):
    """WAV de --minutos con turnos de las voces del sistema que haya (al menos una)."""
    turnos = []
    if sys.platform == "win32":
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }")
        voces = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                               timeout=120).stdout.split("\n")
        voces = [v.strip() for v in voces if v.strip()][:3]
        print("voces:", voces, flush=True)
        q = lambda t: t.replace("'", "''")  # noqa: E731 - comillas simples de PowerShell («let's»: run 42691646093)
        for i, frase in enumerate(FRASES):
            v, p = voces[i % len(voces)], os.path.join(dir_, f"t{i}.wav")
            ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.SelectVoice('{q(v)}'); $f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
                  "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
                  "[System.Speech.AudioFormat.AudioChannel]::Mono);"
                  f" $s.SetOutputToWaveFile('{q(p)}', $f); $s.Speak('{q(frase)}'); $s.Dispose()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=120)
            turnos.append(p)
    else:
        voces = [v for v in ("Samantha", "Daniel", "Karen", "Alex") if subprocess.run(
            ["say", "-v", v, ""], capture_output=True).returncode == 0][:3] or [None]
        for i, frase in enumerate(FRASES):
            v, p = voces[i % len(voces)], os.path.join(dir_, f"t{i}.wav")
            subprocess.run(["say", "-o", p + ".aiff"] + (["-v", v] if v else []) + [frase], check=True, timeout=120)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", p + ".aiff", p], check=True,
                           timeout=60)
            turnos.append(p)
    pcm = []
    for p in turnos:
        with wave.open(p, "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2), p
            pcm.append(w.readframes(w.getnframes()) + b"\0\0" * 4800)  # 0,3 s entre turnos
    ronda = b"".join(pcm)
    n = int(minutos * 60 * 16000 * 2)
    audio = (ronda * (n // len(ronda) + 1))[:n]
    path = os.path.join(dir_, "reunion.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(audio)
    return path


def medir(app, audio, datos, salida, etiqueta):
    res, side = os.path.join(salida, f"{etiqueta}.json"), os.path.join(salida, f"{etiqueta}.hablantes.json")
    t0 = time.time()
    r = subprocess.run([sys.executable, "-c", HIJO, app, res, "--diarize", audio, "--out", side, "--data", datos,
                        "--threads", "2"], capture_output=True, timeout=3600)
    with open(res, encoding="utf-8") as f:
        out = json.load(f)
    with open(side, encoding="utf-8") as f:
        d = json.load(f)
    out.update(segundos=round(time.time() - t0), hablantes=sum(1 for h in d["hablantes"].values() if not h["breve"]))
    proto = [json.loads(line) for line in r.stdout.decode("utf-8", "replace").splitlines() if line.startswith("{")]
    out["error"] = next((m["error"] for m in proto if m.get("type") == "error"), None)
    print(etiqueta, out, flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", required=True)
    ap.add_argument("--minutos", type=float, default=55)
    ap.add_argument("--antes", help="carpeta _app de la versión de antes (p. ej. la de fe29522)")
    ap.add_argument("--tope", type=float, default=None, help="MB de memoria comprometida que la de ahora no pasa")
    a = ap.parse_args()
    os.makedirs(a.salida, exist_ok=True)
    trabajo = os.path.join(a.salida, "trabajo")
    shutil.rmtree(trabajo, ignore_errors=True)
    os.makedirs(trabajo)
    audio = sintetizar(trabajo, a.minutos)
    datos = os.path.join(trabajo, "datos")
    sys.path.insert(0, os.path.join(REPO, "_app"))
    import voices
    voices.ensure_models(datos)
    out = {"minutos": a.minutos, "ahora": medir(os.path.join(REPO, "_app"), audio, datos, a.salida, "ahora")}
    if a.antes:
        out["antes"] = medir(os.path.abspath(a.antes), audio, datos, a.salida, "antes")
    with open(os.path.join(a.salida, "voces_memoria.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    mal = []
    if out["ahora"]["rc"] != 0 or out["ahora"]["error"]:
        mal.append(f"la de ahora ha fallado: {out['ahora']['error']}")
    if a.antes and out["ahora"]["comprometida_mb"] >= out["antes"]["comprometida_mb"]:
        mal.append("la de ahora no gasta menos que la de antes")
    if a.tope and out["ahora"]["comprometida_mb"] > a.tope:
        mal.append(f"la de ahora pasa de {a.tope:.0f} MB")
    for m in mal:
        print("ERROR:", m, flush=True)
    shutil.rmtree(trabajo, ignore_errors=True)
    return 1 if mal else 0


if __name__ == "__main__":
    sys.exit(main())
