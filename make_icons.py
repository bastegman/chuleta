# -*- coding: utf-8 -*-
"""
Genera los iconos de la app a partir de los SVG:

    design/icono.svg         maestro (el azulejo de la app; 40 px y más)
    design/logo-small.svg    variante simplificada para 16 px (sin ojo, dos renglones en pixeles enteros)
    design/icono-20.svg, icono-24.svg, icono-32.svg   el mismo dibujo en la rejilla de 20, 24 y 32 px

(_app/static/logo.svg es otra cosa: la tenca sola, sin azulejo, para la cabecera de la app y el asistente.)

y escribe _app/static/chuleta.ico (9 tamaños, todos en BMP) y _app/static/chuleta.png (256 px).
Todos los tamaños van en BMP porque Windows no siempre pinta bien un .ico cuyas entradas pequeñas son PNG
(la recomendación de Microsoft es PNG solo para 256 px). Rasteriza con Chrome o Edge headless (fiel al SVG,
incluidos degradados) y monta el .ico con Pillow. Herramienta de desarrollo: no viaja en el zip.

    python make_icons.py                     # rutas por defecto
    python make_icons.py maestro.svg pequeño.svg carpeta_salida
"""
import os
import struct
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
BROWSERS = [p for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                        r"C:\Program Files\Google\Chrome\Application\chrome.exe") if os.path.exists(p)]
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SMALL = {16, 20}  # tamaños que usan la variante simplificada (a 20 px manda icono-20.svg, en PIXEL)
# versiones dibujadas en la rejilla de su tamaño (bordes en pixeles enteros: a 20, 24 y 32 px el maestro escalado
# deja los renglones en medio pixel y se ven borrosos en la barra de tareas); si existen junto al SVG que toca,
# mandan sobre él
PIXEL = {20: "icono-20.svg", 24: "icono-24.svg", 32: "icono-32.svg"}


def render(svg, size, work):
    """Captura el SVG a `size` px con el navegador headless (ventana grande + recorte: la ventana mínima
    de Chrome es mayor que los iconos pequeños). Si junto a `svg` está la versión dibujada para ese tamaño (PIXEL), se
    usa esa."""
    pixel = os.path.join(os.path.dirname(os.path.abspath(svg)), PIXEL.get(size, ""))
    if size in PIXEL and os.path.isfile(pixel):
        svg = pixel
    html = os.path.join(work, f"r{size}.html")
    out = os.path.join(work, f"r{size}.png")
    with open(html, "w", encoding="utf-8") as f:
        f.write('<!doctype html><html><body style="margin:0;background:transparent">'
                f'<img src="file://{os.path.abspath(svg)}" width="{size}" height="{size}" style="display:block">'
                '</body></html>')
    subprocess.run(BROWSERS[:1] + ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                                   f"--user-data-dir={os.path.join(work, 'profile')}",
                                   "--default-background-color=00000000", "--window-size=600,600",
                                   f"--screenshot={out}", f"file://{html}"],
                   check=True, capture_output=True, timeout=60)
    im = Image.open(out).convert("RGBA").crop((0, 0, size, size))
    assert im.getpixel((0, 0))[3] < 128, f"{size}: la esquina deberia ser (casi) transparente"
    assert im.getpixel((size // 2, 2))[3] == 255, f"{size}: el borde superior deberia ser opaco"
    return im


def check(ico):
    """Lee la cabecera del .ico y devuelve [(lado, bpp, 'bmp'|'png'), ...]."""
    b = open(ico, "rb").read()
    n = struct.unpack("<H", b[4:6])[0]
    got = []
    for i in range(n):
        w, h, cc, r, planes, bpp, size, off = struct.unpack("<BBBBHHII", b[6 + 16 * i:6 + 16 * i + 16])
        got.append((w or 256, bpp, "png" if b[off:off + 8] == b"\x89PNG\r\n\x1a\n" else "bmp"))
    return got


def main(master, small, out_dir):
    if not BROWSERS:
        sys.exit("Hace falta Chrome o Edge para rasterizar el SVG.")
    work = tempfile.mkdtemp(prefix="icons-")
    frames = {s: render(small if s in SMALL else master, s, work) for s in SIZES}
    ico = os.path.join(out_dir, "chuleta.ico")
    frames[256].save(ico, format="ICO", bitmap_format="bmp", sizes=[(s, s) for s in SIZES],
                     append_images=[frames[s] for s in SIZES if s != 256])
    frames[256].save(os.path.join(out_dir, "chuleta.png"), format="PNG", optimize=True)
    got = check(ico)
    assert sorted(g[0] for g in got) == sorted(SIZES) and all(g[1] == 32 and g[2] == "bmp" for g in got), got
    print(f"{ico}: {os.path.getsize(ico)} bytes, {len(got)} tamaños en BMP: {[g[0] for g in got]}")


if __name__ == "__main__":
    args = sys.argv[1:4] or (os.path.join(ROOT, "design", "icono.svg"),
                             os.path.join(ROOT, "design", "logo-small.svg"),
                             os.path.join(ROOT, "_app", "static"))
    main(*args)
