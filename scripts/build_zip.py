# -*- coding: utf-8 -*-
"""Genera el zip instalable con el mismo build_package que usa la app («Crear instalador para otra persona»),
sin listas de vocabulario.

    python3 scripts/build_zip.py                 -> dist/Notas - instalador.zip
    python3 scripts/build_zip.py /ruta/salida.zip

Layout: Notas/INSTALAR.bat, Notas/ABRIR NOTAS.bat, Notas/LEEME.txt (con la fecha de hoy), Notas/_app/*.py|.bat|.ps1,
Notas/_app/static/*, Notas/_app/export/* (plantillas con {FECHA}). Necesita numpy (lo importa live.py).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "_app"))

import server  # noqa: E402


def main(argv):
    dest = os.path.abspath(argv[1]) if len(argv) > 1 else os.path.join(REPO, "dist", "Notas - instalador.zip")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    n = server.build_package(dest)
    print(f"{dest}: {n} archivos, {os.path.getsize(dest):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
