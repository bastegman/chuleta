# -*- coding: utf-8 -*-
"""Utilidades comunes de los tests. `_app/` no es un paquete: se añade a sys.path y se importan los módulos
tal como los importa el servidor."""
import contextlib
import os
import struct
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP = os.path.join(REPO, "_app")
if APP not in sys.path:
    sys.path.insert(0, APP)

os.environ.setdefault("LOCALAPPDATA", tempfile.mkdtemp(prefix="notas-localappdata-"))  # DATA_DIR de los tests

import server  # noqa: E402  (después de preparar sys.path)
import engines  # noqa: E402
import live  # noqa: E402

PATH_NAMES = ("ROOT", "APP_DIR", "STATIC_DIR", "EXPORT_DIR", "DATA_DIR")


def app_modules():
    """Módulos de _app ya importados (cuando server.py se reparta en varios, todos comparten rutas y LINKS)."""
    return [m for name, m in list(sys.modules.items())
            if m is not None and getattr(m, "__file__", None) and os.path.dirname(os.path.abspath(m.__file__)) == APP]


@contextlib.contextmanager
def patched_paths(**values):
    """Sustituye ROOT, APP_DIR… (y vacía LINKS) en todos los módulos de _app mientras dura el bloque."""
    saved = []
    for m in app_modules():
        for k, v in values.items():
            if hasattr(m, k):
                saved.append((m, k, getattr(m, k)))
                setattr(m, k, v)
    links_saved = dict(server.LINKS)
    server.LINKS.clear()
    try:
        yield
    finally:
        for m, k, v in reversed(saved):
            setattr(m, k, v)
        server.LINKS.clear()
        server.LINKS.update(links_saved)


class TempTree(unittest.TestCase):
    """Un ROOT temporal con su _app/ y un DATA_DIR aparte; COMPLETE apunta a ese DATA_DIR."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notas-test-")
        self.root = os.path.join(self.tmp, "Notas")
        self.app_dir = os.path.join(self.root, "_app")
        self.data_dir = os.path.join(self.tmp, "datos")
        os.makedirs(os.path.join(self.app_dir, "static"))
        os.makedirs(self.data_dir)
        self._ctx = patched_paths(ROOT=self.root, APP_DIR=self.app_dir, DATA_DIR=self.data_dir,
                                  STATIC_DIR=os.path.join(self.app_dir, "static"))
        self._ctx.__enter__()
        self._complete = server.COMPLETE
        server.COMPLETE = server.Complete(os.path.join(self.data_dir, "completas.json"))

    def tearDown(self):
        server.COMPLETE = self._complete
        self._ctx.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- ficheros de prueba
    def write(self, rel, text="", encoding="utf-8"):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding=encoding, newline="") as f:
            f.write(text)
        return p

    def wav(self, rel, seconds=2.0, open_header=False, rate=48000, channels=2):
        """WAV 16 bit con cabecera cerrada (normal) o abierta (0xFFFFFFFF: grabación en curso)."""
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(wav_bytes(seconds, open_header, rate, channels))
        return p


def wav_bytes(seconds=2.0, open_header=False, rate=48000, channels=2, data=None):
    block = channels * 2
    if data is None:
        data = b"\0" * (int(seconds * rate) * block)
    n = 0xFFFFFFFF if open_header else len(data)
    riff = 0xFFFFFFFF if open_header else 36 + len(data)
    head = (b"RIFF" + struct.pack("<I", riff) + b"WAVE" + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, 16)
            + b"data" + struct.pack("<I", n))
    return head + data


def srt_text(segs):
    return "\n".join(server.srt_block(i, a, b, t) for i, (a, b, t) in enumerate(segs, 1))


class FakeJob:
    """Lo mínimo que leen live.run / choose_cut y Jobs.active_for de un trabajo."""

    def __init__(self, path="x.wav", kind="transcribe", status="running", live=False, preset="rapido"):
        self.path, self.kind, self.status, self.live, self.preset = path, kind, status, live, preset
        self.cancel = self.restart = False
        self.recording, self.recorded, self.transcribed, self.rev, self.progress = False, 0.0, 0.0, 0, 0.0
        self.stage, self.id, self.duration, self.edits = "", "job1", None, {}
        self.final_pass = False


def bare_jobs(jobs):
    """Un Jobs sin hilos ni motores: solo la lista y el candado (para probar active_for y compañía)."""
    j = server.Jobs.__new__(server.Jobs)
    j.list, j.lock, j.claimed = list(jobs), threading.Lock(), set()
    return j


__all__ = ["server", "engines", "live", "TempTree", "patched_paths", "wav_bytes", "srt_text", "FakeJob",
           "bare_jobs", "REPO", "APP"]
