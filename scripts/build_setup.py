# -*- coding: utf-8 -*-
"""Genera el Setup.exe de Tenca (fase 1: Inno Setup que envuelve instalar.ps1; ver scripts/setup/tenca.iss).

    python scripts/build_setup.py                    -> dist/Tenca - Setup.exe (necesita ISCC.exe: Windows con
                                                        Inno Setup 6)
    python scripts/build_setup.py /ruta/Setup.exe
    python scripts/build_setup.py --preparar CARPETA  -> solo la carpeta con el contenido del zip y la orden de ISCC
                                                        (sin Inno Setup: macOS, tests)

Lleva exactamente lo mismo que el zip: la carpeta se saca de un zip hecho con server.build_package (el mismo que usa
scripts/build_zip.py y «Crear instalador para otra persona»), con la misma versión. Sin listas de vocabulario: el
Setup.exe se compila en la CI y es público. ISCC se busca en el PATH y en «Inno Setup 6» de Program Files (está en los
runners windows-2022/2025 de GitHub); --iscc lo fija. Necesita numpy, como build_zip.py.
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ISS = os.path.join(HERE, "setup", "tenca.iss")
sys.path.insert(0, HERE)

import build_zip  # noqa: E402
from build_zip import server  # noqa: E402

ISCC_SITIOS = (r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe")


def version_num(version):
    """«2026-11-09 · abc1234» -> «2026.11.9.0» (VersionInfoVersion de Windows: cuatro números); sin fecha, 0.0.0.0."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", version or "")
    return f"{int(m[1])}.{int(m[2])}.{int(m[3])}.0" if m else "0.0.0.0"


def buscar_iscc(explicito=None):
    if explicito:
        return explicito if os.path.isfile(explicito) else None
    return shutil.which("ISCC.exe") or shutil.which("iscc") or next((p for p in ISCC_SITIOS if os.path.isfile(p)), None)


def preparar(carpeta, version):
    """Deja en `carpeta` el contenido del zip (carpeta/Tenca/INSTALAR.bat, carpeta/Tenca/_app/...) y devuelve cuántos
    ficheros. La carpeta tiene que estar vacía o no existir: así no se cuela nada de una compilación anterior."""
    if os.path.isdir(carpeta) and os.listdir(carpeta):
        raise SystemExit(f"{carpeta} no está vacía")
    os.makedirs(carpeta, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tenca-setup-") as tmp:
        z = os.path.join(tmp, "paquete.zip")
        server.build_package(z, [], version=version)
        with zipfile.ZipFile(z) as zf:
            nombres = zf.namelist()
            for n in nombres:  # build_package solo escribe rutas relativas bajo Tenca/; aun así, nada fuera
                if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/"):
                    raise SystemExit(f"ruta inesperada en el paquete: {n}")
            zf.extractall(carpeta)
    return sum(1 for n in nombres if not n.endswith("/"))


def orden_iscc(iscc, fuente, version, salida, nombre):
    return [iscc, "/Q", f"/DFuente={fuente}", f"/DVersion={version}", f"/DVersionNum={version_num(version)}",
            f"/DSalida={salida}", f"/DNombre={nombre}", ISS]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Genera el Setup.exe (Inno Setup).")
    ap.add_argument("dest", nargs="?", default=os.path.join(REPO, "dist", f"{server.APP_NAME} - Setup.exe"))
    ap.add_argument("--version", default=None, metavar="TEXTO", help="por defecto, fecha del commit · sha (build_zip)")
    ap.add_argument("--iscc", default=None, help="ruta de ISCC.exe")
    ap.add_argument("--preparar", default=None, metavar="CARPETA",
                    help="solo deja el contenido del zip en CARPETA y enseña la orden de ISCC")
    a = ap.parse_args(argv[1:])
    version = a.version or build_zip.version_string()
    dest = os.path.abspath(a.dest)
    salida, nombre = os.path.dirname(dest), os.path.splitext(os.path.basename(dest))[0]
    if a.preparar:
        fuente = os.path.abspath(a.preparar)
        n = preparar(fuente, version)
        print(f"{fuente}: {n} archivos, versión {version}")
        print(" ".join(f'"{x}"' if " " in x else x for x in orden_iscc("ISCC.exe", fuente, version, salida, nombre)))
        return 0
    iscc = buscar_iscc(a.iscc)
    if not iscc:
        print("No encuentro ISCC.exe (Inno Setup 6): instálalo o pásalo con --iscc. Con --preparar se deja solo la"
              " carpeta.", file=sys.stderr)
        return 2
    build_zip.write_version(version)  # como build_zip: la app desde el repo enseña la misma versión que el Setup
    os.makedirs(salida, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tenca-setup-fuente-") as fuente:
        n = preparar(os.path.join(fuente, "p"), version)
        r = subprocess.run(orden_iscc(iscc, os.path.join(fuente, "p"), version, salida, nombre))
        if r.returncode != 0 or not os.path.isfile(dest):
            print(f"ISCC ha fallado (código {r.returncode})", file=sys.stderr)
            return r.returncode or 1
    print(f"{dest}: {n} archivos dentro, {os.path.getsize(dest):,} bytes, sha256 {sha256(dest)}, versión {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
