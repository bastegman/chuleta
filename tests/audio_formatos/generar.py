# -*- coding: utf-8 -*-
"""Genera los audios de prueba de «Subir audio» (tests/test_subir_audio.py): la misma señal sintética (tres pitidos
de 440, 880 y 660 Hz con silencio entre medias, 1,6 s) en los formatos del móvil, de WhatsApp y de otras grabadoras.
Señal propia, sin voces ni grabaciones de terceros. Se generan en un Mac (afconvert es el codificador de Apple, el
mismo que usa el iPhone, y ffmpeg el resto); los tests solo los leen.

    python tests/audio_formatos/generar.py
"""
import os
import struct
import subprocess
import sys
import tempfile
import wave
from math import pi, sin

AQUI = os.path.dirname(os.path.abspath(__file__))
SR = 16000
PITIDOS = ((0.20, 0.50, 440.0), (0.70, 1.00, 880.0), (1.20, 1.50, 660.0))  # (desde, hasta, Hz)
DURACION = 1.6


def senal():
    out = []
    for i in range(int(DURACION * SR)):
        t, v = i / SR, 0.0
        for a, b, f in PITIDOS:
            if a <= t < b:
                env = min(1.0, (t - a) / 0.01, (b - t) / 0.01)  # rampas de 10 ms: sin chasquidos
                v = 0.5 * env * sin(2 * pi * f * t)
        out.append(int(v * 32767))
    return out


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def af(*args):
    subprocess.run(["afconvert", *args], check=True)


def amr_artesano(path):
    """AMR-NB a mano (no hay codificador AMR libre en el Mac): 50 tramas de 12,2 kbit/s, 5 tramas SID (las que FFmpeg
    no implementa: «patches welcome») y otras 50. Sale ruido, no los pitidos; sirve para el AMR y el 3GP."""
    import random
    rnd = random.Random(1296)
    voz = lambda: bytes([0x3C]) + bytes(rnd.randrange(256) for _ in range(31))  # noqa: E731 (FT 7, Q=1)
    sid = lambda: bytes([0x44]) + bytes(rnd.randrange(256) for _ in range(5))  # noqa: E731 (FT 8, SID)
    with open(path, "wb") as f:
        f.write(b"#!AMR\n" + b"".join(voz() for _ in range(50)) + b"".join(sid() for _ in range(5))
                + b"".join(voz() for _ in range(50)))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "senal.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(struct.pack(f"<{int(DURACION * SR)}h", *senal()))
        o = lambda n: os.path.join(AQUI, n)  # noqa: E731
        af("-f", "m4af", "-d", "aac", "-b", "32000", wav, o("iphone_notas_de_voz.m4a"))  # Notas de voz (AAC)
        af("-f", "m4af", "-d", "alac", wav, o("iphone_sin_perdida.m4a"))  # Notas de voz «sin pérdida» (ALAC)
        af("-f", "caff", "-d", "aac", wav, o("apple.caf"))
        af("-f", "adts", "-d", "aac@44100", "-b", "64000", wav, o("adts.aac"))
        af("-f", "AIFF", "-d", "BEI16@8000", wav, o("aiff.aiff"))
        ff("-i", wav, "-ar", "44100", "-c:a", "aac", "-b:a", "64k", o("android_grabadora.m4a"))
        ff("-i", wav, "-ar", "16000", "-c:a", "libopus", "-b:a", "16k", "-application", "voip", o("whatsapp.opus"))
        ff("-i", wav, "-ar", "44100", "-ac", "2", "-c:a", "vorbis", "-strict", "-2", o("vorbis.ogg"))
        ff("-i", wav, "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "64k", o("mp3.mp3"))
        ff("-i", wav, "-ar", "48000", "-c:a", "libopus", "-b:a", "24k", o("navegador.webm"))
        ff("-i", wav, "-c:a", "flac", o("flac.flac"))
        ff("-i", wav, "-ar", "44100", "-c:a", "wmav2", "-b:a", "48k", o("wma.wma"))
        ff("-f", "lavfi", "-i", "color=c=green:s=64x64:r=10", "-i", wav, "-shortest", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "48k", o("video.mp4"))
        ff("-f", "lavfi", "-i", "color=c=green:s=64x64:r=10", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           o("video_sin_audio.mp4"))
        amr = os.path.join(tmp, "artesano.amr")
        amr_artesano(amr)
        ff("-i", amr, "-c:a", "copy", o("android_antiguo.3gp"))
    print("hecho:", ", ".join(sorted(n for n in os.listdir(AQUI) if not n.endswith(".py"))))


if __name__ == "__main__":
    sys.exit(main())
