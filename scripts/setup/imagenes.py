# -*- coding: utf-8 -*-
"""Genera la imagen lateral del asistente de Setup.exe (Inno Setup, WizardImageFile) a partir del icono de la app:
el pájaro de _app/static/tenca.png sobre el verde de la marca (--accent de app.css, #1e5a46), en las dos densidades
que admite Inno (proporción 164:314; 240×459 es la de por defecto desde la 6.6 y 480×918 para pantallas al 200 %).

    python scripts/setup/imagenes.py

Herramienta de desarrollo (Pillow), como make_icons.py: el resultado se commitea y no viaja en el zip."""
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
VERDE = (0x1E, 0x5A, 0x46, 255)  # --accent de app.css y fondo del azulejo de tenca.ico


def lateral(icono, ancho, alto, lado):
    img = Image.new("RGBA", (ancho, alto), VERDE)
    ic = icono.resize((lado, lado), Image.LANCZOS) if icono.width != lado else icono
    img.alpha_composite(ic, ((ancho - lado) // 2, int(alto * 0.30) - lado // 2))
    return img.convert("RGB")


def main():
    icono = Image.open(os.path.join(REPO, "_app", "static", "tenca.png")).convert("RGBA")
    for sufijo, (ancho, alto, lado) in {"": (240, 459, 128), "-2x": (480, 918, 256)}.items():
        destino = os.path.join(HERE, f"asistente{sufijo}.png")
        lateral(icono, ancho, alto, lado).save(destino, optimize=True)
        print(destino, os.path.getsize(destino))


if __name__ == "__main__":
    main()
