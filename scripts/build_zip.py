# -*- coding: utf-8 -*-
"""Genera el zip instalable con el mismo build_package que usa la app («Crear instalador para otra persona»).

    python3 scripts/build_zip.py                       -> dist/Notas - instalador.zip, sin listas de vocabulario
    python3 scripts/build_zip.py /ruta/salida.zip
    python3 scripts/build_zip.py --vocab [CARPETA]     -> además, las listas <proyecto>/Vocabulario/*.txt de CARPETA
                                                          (por defecto la raíz del repo), como
                                                          Notas/<proyecto>/Vocabulario/<tema>.txt

Layout: Notas/INSTALAR.bat, Notas/ABRIR NOTAS.bat, Notas/LEEME.txt (con la fecha de hoy), Notas/_app/*.py|.bat|.ps1,
Notas/_app/static/*, Notas/_app/export/* (plantillas con {FECHA}). Necesita numpy (lo importa live.py).
Las listas de vocabulario suelen llevar nombres de personas y empresas: solo para un zip privado, nunca para publicar.
"""
import argparse
import os
import sys

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
                out.append((os.path.join(d, name), f"Notas/{proj}/Vocabulario/{name}"))
    return out


def main(argv):
    ap = argparse.ArgumentParser(description="Genera el zip instalable.")
    ap.add_argument("dest", nargs="?", default=os.path.join(REPO, "dist", "Notas - instalador.zip"))
    ap.add_argument("--vocab", nargs="?", const=REPO, default=None, metavar="CARPETA",
                    help="incluir las listas <proyecto>/Vocabulario/*.txt de CARPETA (por defecto, la raíz del repo)")
    a = ap.parse_args(argv[1:])
    dest = os.path.abspath(a.dest)
    extra = vocab_entries(os.path.abspath(a.vocab)) if a.vocab else []
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    n = server.build_package(dest, extra)
    listas = f", {len(extra)} listas de vocabulario" if extra else ""
    print(f"{dest}: {n} archivos, {os.path.getsize(dest):,} bytes{listas}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
