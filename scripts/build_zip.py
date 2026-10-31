# -*- coding: utf-8 -*-
"""Genera el zip instalable con el mismo build_package que usa la app («Crear instalador para otra persona»).

    python3 scripts/build_zip.py                       -> dist/Chuleta - instalador.zip, sin listas de vocabulario
    python3 scripts/build_zip.py /ruta/salida.zip
    python3 scripts/build_zip.py --vocab [CARPETA]     -> además, las listas <proyecto>/Vocabulario/*.txt de CARPETA
                                                          (por defecto la raíz del repo), como
                                                          Chuleta/<proyecto>/Vocabulario/<tema>.txt

Layout: Chuleta/INSTALAR.bat, Chuleta/ABRIR CHULETA.bat, Chuleta/LEEME.txt (con la versión en la primera línea),
Chuleta/LICENSE, Chuleta/_app/*.py|.bat|.ps1, Chuleta/_app/version.txt, Chuleta/_app/static/*, Chuleta/_app/export/*
(plantillas con {FECHA}). Necesita numpy (lo importa live.py).

Versión: «AAAA-MM-DD · <sha corto>» (fecha del commit y `git rev-parse --short HEAD`; sin git, la fecha de hoy). Se
escribe en _app/version.txt del repo (ignorado por git), va dentro del zip y sustituye al {FECHA} del LEEME; server.py
la enseña en Ajustes > Acerca de y el instalador en su «Listo».
Las listas de vocabulario suelen llevar nombres de personas y empresas: solo para un zip privado, nunca para publicar.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "_app"))

import server  # noqa: E402


def vocab_entries(folder):
    """[(fichero, ruta en el zip)] de las listas <proyecto>/Vocabulario/*.txt que haya en `folder`."""
    out = []
    for proj in sorted(os.listdir(folder)):
        d = os.path.join(folder, proj, "Vocabulario")
        if proj.startswith((".", "_")) or not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.lower().endswith(".txt"):
                out.append((os.path.join(d, name), f"{server.PKG_ROOT}/{proj}/Vocabulario/{name}"))
    return out


def git(*args):
    try:
        r = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, encoding="utf-8", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def version_string():
    """«AAAA-MM-DD · abc1234»: fecha del commit (committer date) y sha corto de HEAD. Sin git (o sin commits), la fecha
    de hoy a secas."""
    date, sha = git("log", "-1", "--format=%cs"), git("rev-parse", "--short", "HEAD")
    if date and sha:
        return f"{date} · {sha}"
    return time.strftime("%Y-%m-%d")


def write_version(version):
    """_app/version.txt del repo (ignorado por git): server.py desde el repo ensena entonces lo mismo que el zip."""
    path = os.path.join(REPO, "_app", "version.txt")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(version + "\n")
    return path


def main(argv):
    ap = argparse.ArgumentParser(description="Genera el zip instalable.")
    ap.add_argument("dest", nargs="?", default=os.path.join(REPO, "dist", f"{server.APP_NAME} - instalador.zip"))
    ap.add_argument("--vocab", nargs="?", const=REPO, default=None, metavar="CARPETA",
                    help="incluir las listas <proyecto>/Vocabulario/*.txt de CARPETA (por defecto, la raíz del repo)")
    ap.add_argument("--version", default=None, metavar="TEXTO",
                    help="texto de version para _app/version.txt y el LEEME (por defecto, fecha del commit · sha)")
    a = ap.parse_args(argv[1:])
    dest = os.path.abspath(a.dest)
    extra = vocab_entries(os.path.abspath(a.vocab)) if a.vocab else []
    version = a.version or version_string()
    write_version(version)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    n = server.build_package(dest, extra, version=version)
    listas = f", {len(extra)} listas de vocabulario" if extra else ""
    print(f"{dest}: {n} archivos, {os.path.getsize(dest):,} bytes, versión {version}{listas}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
