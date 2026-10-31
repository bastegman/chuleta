# -*- coding: utf-8 -*-
"""Ayudante del E2E en Windows (lo llama scripts/e2e/lookfeel_e2e.ps1): localiza la ventana de la app por titulo y
vuelca su estado, sus iconos (PNG) y las propiedades System.AppUserModel.* del HWND, con las funciones del spike.

    python scripts/e2e/ventana.py find TITULO
    python scripts/e2e/ventana.py icons TITULO CARPETA_SALIDA ETIQUETA

Imprime JSON por stdout. Codigo 0 aunque no encuentre la ventana (lo dice el JSON: hwnd null).
"""
import ctypes
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lookfeel_spike as spike  # noqa: E402


def find(title):
    user32 = ctypes.windll.user32
    for hwnd, t, cls, pid in spike.windows():
        if t.strip() == title and not cls.startswith("Chrome_WidgetWin"):
            return {"hwnd": hwnd, "title": t, "class": cls, "pid": pid, "visible": bool(user32.IsWindowVisible(hwnd)),
                    "iconic": bool(user32.IsIconic(hwnd))}
    # las ocultas (ShowWindow SW_HIDE) no salen en windows(): se buscan sin el filtro de visibilidad
    found = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value.strip() == title:
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if not cls.value.startswith("Chrome_WidgetWin"):
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append({"hwnd": hwnd, "title": buf.value, "class": cls.value, "pid": pid.value,
                              "visible": bool(user32.IsWindowVisible(hwnd)), "iconic": bool(user32.IsIconic(hwnd))})
        return True
    user32.EnumWindows(enum_proc(cb), 0)
    return found[0] if found else {"hwnd": None, "title": title}


def main(argv):
    spike.ole32.CoInitialize(None)
    cmd = argv[1] if len(argv) > 1 else "find"
    title = argv[2] if len(argv) > 2 else "Notas"
    info = find(title)
    if cmd == "icons" and info.get("hwnd"):
        out_dir, tag = argv[3], (argv[4] if len(argv) > 4 else "app")
        os.makedirs(out_dir, exist_ok=True)
        info["icons"] = spike.window_icons(info["hwnd"], out_dir, tag)
        info["appusermodel"] = spike.appusermodel_props(info["hwnd"])
    print(json.dumps(info, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
