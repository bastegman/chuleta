# -*- coding: utf-8 -*-
"""Lo que rodea a la grabadora propia (recorder.py) y es casi todo de Windows: atajo global Ctrl+Alt+R, pitidos,
avisos (toast), cierre de la cabecera WAV, el proceso de la grabadora y la deteccion de una reunion en curso.
Fuera de Windows cada pieza se desactiva o se limita a escribir en el log.
"""
import json
import os
import struct
import subprocess
import sys
import threading
import time

import engines
import live
import voices
from common import WINDOWS, log


class GlobalHotkey(threading.Thread):
    """Ctrl+Alt+R en todo Windows (aunque la app este en segundo plano o estes en Teams).
    RegisterHotKey exige que el registro y el bucle de mensajes vayan en el mismo hilo."""

    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_R, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x52, 0x0312

    def __init__(self, callback):
        super().__init__(daemon=True, name="hotkey")
        self.callback, self.ok, self.error, self.last = callback, False, None, 0.0

    def run(self):
        if not WINDOWS:
            self.error = "el atajo global solo existe en Windows"
            return
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT, self.VK_R):
            self.error = "Ctrl+Alt+R ya lo usa otro programa"
            log(f"   atajo no disponible: {self.error}")
            return
        self.ok = True
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY and time.time() - self.last > 1.5:  # evita dobles pulsaciones
                self.last = time.time()
                threading.Thread(target=self.callback, daemon=True).start()


def cue(kind):
    """Pitido corto y bajito: sube al empezar, baja al parar (se oye sin mirar la pantalla)."""
    if not WINDOWS:
        return

    def play():
        try:
            import io
            import wave
            import winsound
            import numpy as np
            notes = {"start": (660, 880), "stop": (880, 660), "error": (300, 220)}[kind]
            sr, parts = 44100, []
            for f in notes:
                t = np.arange(int(sr * 0.09)) / sr
                parts.append(np.sin(2 * np.pi * f * t) * np.minimum(1, np.minimum(t, t[::-1]) * 200))
                parts.append(np.zeros(int(sr * 0.04)))
            pcm = (np.concatenate(parts) * 0.18 * 32767).astype("<i2")
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(pcm.tobytes())
            winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY)
        except Exception:
            pass
    play()


# Aviso de Windows (toast) con PowerShell. El script es FIJO: el titulo y el texto le llegan por variables de entorno
# y nunca se pegan dentro del codigo. Asi un nombre de proyecto o de tema con comillas tipograficas (‘ ’ ‚ ‛, que
# PowerShell tambien toma por comillas simples), « " », $, ` o saltos de linea no puede cerrar la cadena ni ejecutar
# nada, y «Pla d’acció» sale tal cual en el aviso.
TOAST_ENV = ("CHULETA_AVISO_TITULO", "CHULETA_AVISO_TEXTO")
TOAST_PS = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] > $null;"
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$x = $t.GetElementsByTagName('text');"
            f"$x.Item(0).AppendChild($t.CreateTextNode([string]$env:{TOAST_ENV[0]})) > $null;"
            f"$x.Item(1).AppendChild($t.CreateTextNode([string]$env:{TOAST_ENV[1]})) > $null;"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
            "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe')"
            ".Show([Windows.UI.Notifications.ToastNotification]::new($t))")


def toast_text(s, limit=400):
    """Texto apto para una variable de entorno (sin NUL) y de un largo razonable para un aviso."""
    return str(s if s is not None else "").replace("\x00", "")[:limit]


def toast_command(title, body):
    """(argv, env) del PowerShell que ensena el aviso: el script no depende de los textos, que van en `env`."""
    import base64
    env = dict(os.environ)
    env[TOAST_ENV[0]], env[TOAST_ENV[1]] = toast_text(title), toast_text(body)
    argv = ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-EncodedCommand", base64.b64encode(TOAST_PS.encode("utf-16-le")).decode()]
    return argv, env


def notify(title, body):
    """Notificacion de Windows (por si la ventana de la app no esta a la vista)."""
    if not WINDOWS:
        log(f"   aviso: {title} — {body}")
        return
    argv, env = toast_command(title, body)
    try:
        subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=engines.NO_WINDOW)
    except (OSError, ValueError):
        pass


def close_wav(path):
    """Corrige la cabecera de un WAV que se quedo abierto (grabadora que murio sin cerrar)."""
    info = live.wav_info(path)
    if not info or info["data_size"] is not None:
        return
    size = os.path.getsize(path)
    with open(path, "r+b") as f:
        f.seek(4)
        f.write(struct.pack("<I", size - 8))
        f.seek(info["data_off"] - 4)
        f.write(struct.pack("<I", size - info["data_off"]))


class RecorderProcess:
    """recorder.py en un proceso aparte: graba (o solo mide niveles) y manda su estado por stdout."""

    def __init__(self, path, mic, sys_dev, app_dir, data_dir):
        self.path, self.started, self.status, self.ended = path, time.time(), {}, False
        self.levels = []  # (t, mic dB, sys dB) cada 0,25 s: cuando hablo yo (para etiquetar «Tu», sin biometria)
        args = [path] if path else ["--monitor"]
        err = open(os.path.join(data_dir, "recorder.log"), "ab")
        self.p = subprocess.Popen([sys.executable, "-u", os.path.join(app_dir, "recorder.py"), *args,
                                   "--mic", mic or "default", "--sys", sys_dev or "default"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, cwd=app_dir,
                                  # prioridad alta: gasta muy poco, pero si se retrasa se pierde audio
                                  creationflags=engines.NO_WINDOW
                                  | getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0))
        err.close()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            try:
                self.status = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            self.note_levels(self.status)
        self.ended = True
        if self.path and os.path.isfile(self.path):
            try:
                close_wav(self.path)  # por si murio sin cerrar la cabecera
            except OSError:
                pass

    def note_levels(self, st):
        if not self.path or not isinstance(st, dict) or st.get("t") is None:
            return
        mic = (st.get("mic") or {}).get("db") if isinstance(st.get("mic"), dict) else None
        if mic is None or st.get("mic_muted"):
            return
        sysdb = (st.get("sys") or {}).get("db") if isinstance(st.get("sys"), dict) else None
        self.levels.append((st["t"], mic, sysdb))

    def timeline(self):
        """{"paso", "activo"} de la grabacion (None si no se grabo el micro)."""
        return voices.mic_timeline(self.levels) if self.levels else None

    def alive(self):
        return not self.ended and self.p.poll() is None

    def send(self, cmd):
        try:
            self.p.stdin.write((cmd + "\n").encode())
            self.p.stdin.flush()
            return True
        except OSError:
            return False

    def stop(self, timeout=10):
        try:
            self.p.stdin.write(b"stop\n")
            self.p.stdin.flush()
        except OSError:
            pass
        end = time.time() + timeout
        while time.time() < end and not self.ended:
            time.sleep(0.05)
        if not self.ended:
            self.p.kill()
            time.sleep(0.3)
            if self.path:
                close_wav(self.path)
        return self.status.get("t", 0)


# ----------------------------------------------------------------------------- reunion en curso
# --- deteccion de reunion (como Notion): Teams/Zoom/… usando el micro y sin grabar
MEETING_APPS = (("msteams", "Teams"), ("teams.exe", "Teams"), ("zoom", "Zoom"), ("webex", "Webex"),
                ("slack", "Slack"), ("skype", "Skype"), ("chrome.exe", "el navegador"),
                ("msedge.exe", "el navegador"), ("firefox.exe", "el navegador"))


def mic_meeting_app():
    """App de reuniones que tiene ahora mismo el micro abierto (segun el registro de privacidad)."""
    if not WINDOWS:
        return None
    import winreg
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    for sub in (base, base + r"\NonPackaged"):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                low = name.lower()
                app = next((label for k, label in MEETING_APPS if k in low), None)
                if not app:
                    continue
                try:
                    with winreg.OpenKey(key, name) as sk:
                        start = winreg.QueryValueEx(sk, "LastUsedTimeStart")[0]
                        stop = winreg.QueryValueEx(sk, "LastUsedTimeStop")[0]
                except OSError:
                    continue
                if start and not stop:  # en uso ahora mismo
                    return app
    return None
