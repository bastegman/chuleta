# -*- coding: utf-8 -*-
"""Medición (solo Windows, pensada para un runner de GitHub Actions): qué icono y qué AppUserModelID pone Edge en la
ventana `--app=` de Notas según lo que sirva la página como favicon.

    python scripts/lookfeel_spike.py SALIDA

Para cada variante copia `_app` a una carpeta temporal, ajusta `static/index.html`, arranca `server.py --no-browser`
en el puerto 8765, abre `msedge --app=http://127.0.0.1:8765 --window-size=1200,800` con un perfil nuevo, espera la
ventana titulada «Notas» y vuelca a SALIDA/report.json y SALIDA/report.md:
  (a) el AppUserModelID de la ventana (SHGetPropertyStoreForWindow + PKEY_AppUserModel_ID);
  (b) el tamaño en píxeles de los iconos WM_GETICON (ICON_SMALL, ICON_BIG, ICON_SMALL2) y de los de la clase
      (GCLP_HICON, GCLP_HICONSM), que guarda además como PNG para verlos.
Variantes: (ii) .ico + PNG 256 (como está en main) con el perfil por defecto de Edge, (ii-udd) lo mismo con un
--user-data-dir propio (¿cambia el AUMID?), (i) solo el favicon SVG (como antes del 31/10/2026), (iii) además un
manifest.webmanifest con iconos 192/512 enlazado desde index.html.
No instala nada ni toca el repo. Solo la librería estándar (y Pillow, si está, para los PNG de 192/512).
"""
import ctypes
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib
from ctypes import wintypes as wt

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
TITLE = "Notas"

SVG_HEAD = '<link rel="icon" href="/logo.svg" type="image/svg+xml">\n'
ICO_HEAD = ('<link rel="icon" href="/notas.ico" sizes="16x16 20x20 24x24 32x32 40x40 48x48 64x64 128x128 256x256">\n'
            '<link rel="icon" href="/notas.png" type="image/png" sizes="256x256">\n'
            '<link rel="apple-touch-icon" href="/notas.png">\n')
MANIFEST_LINK = '<link rel="manifest" href="/manifest.webmanifest">\n'

user32, gdi32, shell32, ole32, kernel32 = (ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.shell32,
                                           ctypes.windll.ole32, ctypes.windll.kernel32)


# ----------------------------------------------------------------------------- Win32

class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG), ("bmWidthBytes", wt.LONG),
                ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD), ("bmBits", wt.LPVOID)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def guid(s):
    g = GUID()
    if ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g)) != 0:
        raise OSError("CLSIDFromString " + s)
    return g


user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                                       ctypes.POINTER(ctypes.c_size_t)]
user32.GetClassLongPtrW.restype = ctypes.c_size_t
user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetIconInfo.argtypes = [wt.HICON, ctypes.POINTER(ICONINFO)]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
gdi32.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [wt.HANDLE]
shell32.SHGetPropertyStoreForWindow.argtypes = [wt.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def windows():
    """[(hwnd, título, clase, pid)] de las ventanas visibles de nivel superior."""
    out = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append((hwnd, buf.value, cls.value, pid.value))
        return True
    user32.EnumWindows(EnumProc(cb), 0)
    return out


PKEY_APPUSERMODEL = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
# pid 2 RelaunchCommand, 3 RelaunchIconResource, 4 RelaunchDisplayNameResource, 5 ID (System.AppUserModel.*)
APPUSERMODEL_PIDS = {"RelaunchCommand": 2, "RelaunchIconResource": 3, "RelaunchDisplayNameResource": 4, "ID": 5}


def window_prop(hwnd, pid):
    """Propiedad System.AppUserModel.<pid> de la ventana: None (sin valor) o "error: ...". """
    ps = ctypes.c_void_p()
    hr = shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")),
                                             ctypes.byref(ps))
    if hr != 0 or not ps.value:
        return f"error: SHGetPropertyStoreForWindow hr=0x{hr & 0xFFFFFFFF:08x}"
    vtbl = ctypes.cast(ps, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    get_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                   ctypes.POINTER(PROPVARIANT))(vtbl[5])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
    key = PROPERTYKEY(guid(PKEY_APPUSERMODEL), pid)
    pv = PROPVARIANT()
    try:
        hr = get_value(ps, ctypes.byref(key), ctypes.byref(pv))
        if hr != 0:
            return f"error: GetValue hr=0x{hr & 0xFFFFFFFF:08x}"
        if pv.vt == 0:  # VT_EMPTY
            return None
        if pv.vt == 31:  # VT_LPWSTR
            return pv.pwszVal
        return f"error: vt={pv.vt}"
    finally:
        ole32.PropVariantClear(ctypes.byref(pv))
        release(ps)


def aumid(hwnd):
    """System.AppUserModel.ID de la ventana, o None (sin propiedad) o "error: ...". """
    return window_prop(hwnd, 5)


def appusermodel_props(hwnd):
    return {name: window_prop(hwnd, pid) for name, pid in APPUSERMODEL_PIDS.items()}


def png_bytes(w, h, rgba):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def bitmap_rgba(hbm, w, h):
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    hdc = user32.GetDC(None)
    try:
        if gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bmi), 0) != h:
            return None
    finally:
        user32.ReleaseDC(None, hdc)
    b = bytearray(buf.raw)
    b[0::4], b[2::4] = buf.raw[2::4], buf.raw[0::4]  # BGRA -> RGBA
    if not any(b[3::4]):  # 32 bpp sin canal alfa (todo 0): opaco, para poder verlo
        b[3::4] = b"\xff" * (w * h)
    return bytes(b)


def icon_info(hicon, out_png=None):
    """{"px": "WxH", "bpp": n} del HICON (GetIconInfo -> hbmColor -> GetObject) y, si se pide, su PNG."""
    if not hicon:
        return None
    ii = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(ii)):
        return {"error": f"GetIconInfo {ctypes.GetLastError()}"}
    try:
        bm = BITMAP()
        hbm = ii.hbmColor or ii.hbmMask
        if not gdi32.GetObjectW(hbm, ctypes.sizeof(BITMAP), ctypes.byref(bm)):
            return {"error": "GetObject"}
        h = bm.bmHeight if ii.hbmColor else bm.bmHeight // 2
        info = {"px": f"{bm.bmWidth}x{h}", "bpp": bm.bmBitsPixel, "mask_only": not ii.hbmColor}
        if out_png and ii.hbmColor:
            rgba = bitmap_rgba(ii.hbmColor, bm.bmWidth, h)
            if rgba:
                with open(out_png, "wb") as f:
                    f.write(png_bytes(bm.bmWidth, h, rgba))
                info["png"] = os.path.basename(out_png)
        return info
    finally:
        for hb in (ii.hbmColor, ii.hbmMask):
            if hb:
                gdi32.DeleteObject(hb)


def window_icons(hwnd, out_dir, tag):
    res = {}
    for name, w in (("ICON_SMALL", 0), ("ICON_BIG", 1), ("ICON_SMALL2", 2)):
        got = ctypes.c_size_t()
        ok = user32.SendMessageTimeoutW(hwnd, 0x7F, w, 0, 0x2, 3000, ctypes.byref(got))
        res[name] = icon_info(got.value, os.path.join(out_dir, f"{tag}-{name}.png")) if ok else {"error": "timeout"}
    for name, idx in (("GCLP_HICON", -14), ("GCLP_HICONSM", -34)):
        res[name] = icon_info(user32.GetClassLongPtrW(hwnd, idx), os.path.join(out_dir, f"{tag}-{name}.png"))
    return res


def system_info():
    try:
        dpi = user32.GetDpiForSystem()
    except AttributeError:
        dpi = None
    return {"dpi": dpi, "SM_CXICON": user32.GetSystemMetrics(11), "SM_CXSMICON": user32.GetSystemMetrics(49),
            "python": sys.version.split()[0]}


# ----------------------------------------------------------------------------- variantes

def edge_exe():
    for p in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"):
        p = os.path.expandvars(p)
        if os.path.exists(p):
            return p
    return None


def make_variant(kind, base):
    """Copia de _app con el index.html de la variante. Devuelve la ruta de su server.py."""
    app = os.path.join(base, kind, "_app")
    kind = kind.split("-")[0]  # "ico-udd" sirve lo mismo que "ico"
    shutil.copytree(os.path.join(REPO, "_app"), app, ignore=shutil.ignore_patterns("__pycache__", "python.txt"))
    index = os.path.join(app, "static", "index.html")
    with open(index, encoding="utf-8") as f:
        html = f.read()
    assert ICO_HEAD in html, "index.html no lleva la cabecera de iconos esperada"
    if kind == "svg":
        html = html.replace(ICO_HEAD, SVG_HEAD)
    elif kind == "manifest":
        html = html.replace(ICO_HEAD, ICO_HEAD + MANIFEST_LINK)
        static = os.path.join(app, "static")
        icons = []
        for size in (192, 512):
            name = f"notas-{size}.png"
            try:
                from PIL import Image
                Image.open(os.path.join(static, "notas.png")).convert("RGBA").resize((size, size),
                                                                                     Image.LANCZOS).save(
                    os.path.join(static, name))
                icons.append({"src": "/" + name, "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"})
            except Exception as e:  # sin Pillow: se declara el PNG de 256 con ese tamaño
                print(f"   sin PNG de {size} ({e}); se usa notas.png")
                icons.append({"src": "/notas.png", "sizes": "256x256", "type": "image/png", "purpose": "any"})
        manifest = {"name": TITLE, "short_name": TITLE, "start_url": "/", "display": "standalone",
                    "background_color": "#ffffff", "theme_color": "#ffffff", "icons": icons}
        with open(os.path.join(static, "manifest.webmanifest"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        server = os.path.join(app, "server.py")
        with open(server, encoding="utf-8") as f:
            src = f.read()
        src = src.replace('".ico": "image/x-icon"}',
                          '".ico": "image/x-icon", ".webmanifest": "application/manifest+json"}')
        with open(server, "w", encoding="utf-8") as f:
            f.write(src)
    with open(index, "w", encoding="utf-8") as f:
        f.write(html)
    return os.path.join(app, "server.py")


def wait_ping(timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(URL + "api/ping", timeout=2) as r:
                if json.load(r).get("app") == "audios":
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def kill_edge():
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe", "/T"], capture_output=True)
    time.sleep(1.5)


def measure_variant(kind, base, out_dir, edge):
    rec = {"variant": kind, "windows": [], "notes": []}
    data = os.path.join(base, kind, "data")
    server_py = make_variant(kind, base)
    log = open(os.path.join(out_dir, f"{kind}-server.log"), "wb")
    srv = subprocess.Popen([sys.executable, server_py, "--no-browser", "--port", str(PORT), "--data", data],
                           stdout=log, stderr=subprocess.STDOUT)
    try:
        if not wait_ping():
            rec["notes"].append("el servidor no respondió al ping en 40 s")
            return rec
        with urllib.request.urlopen(URL, timeout=5) as r:
            head = r.read().decode("utf-8", "replace").split("</head>")[0]
        rec["head_links"] = [l.strip() for l in head.splitlines() if "<link" in l]
        # "ico": el perfil por defecto de Edge (lo que ve un usuario normal: el AUMID de Chromium solo omite el
        # sufijo de perfil con el perfil Default del user-data-dir por defecto); las demas, un perfil propio
        udd = [] if kind == "ico" else [f"--user-data-dir={os.path.join(base, kind, 'edge-profile')}"]
        rec["user_data_dir"] = udd[0] if udd else "(por defecto)"
        edge_p = subprocess.Popen([edge, f"--app={URL.rstrip('/')}", "--window-size=1200,800", *udd,
                                   "--no-first-run", "--no-default-browser-check", "--disable-sync",
                                   "--disable-features=msEdgeFirstRunExperience,msImplicitSignin"])
        rec["edge_pid"] = edge_p.pid
        hwnd, end = None, time.time() + 45
        while time.time() < end and not hwnd:
            for h, title, cls, pid in windows():
                if cls.startswith("Chrome_WidgetWin") and title.strip() == TITLE:
                    hwnd = h
                    break
            if not hwnd:
                time.sleep(0.5)
        if not hwnd:
            rec["notes"].append("no apareció ninguna ventana Chrome_WidgetWin titulada «Notas» en 45 s")
            rec["windows"] = [{"hwnd": h, "title": t, "class": c, "pid": p} for h, t, c, p in windows()
                              if c.startswith("Chrome_WidgetWin") or "Edge" in t]
            return rec
        time.sleep(6)  # que cargue el favicon y Chromium vuelva a poner el icono de la ventana
        rec["hwnd"] = hwnd
        rec["aumid_first"] = aumid(hwnd)
        rec["icons"] = window_icons(hwnd, out_dir, kind)
        time.sleep(4)  # segunda lectura: por si el icono cambia al terminar de cargar
        rec["icons_after_10s"] = window_icons(hwnd, out_dir, kind + "-t10")
        rec["aumid"] = aumid(hwnd)
        rec["appusermodel"] = appusermodel_props(hwnd)  # relanzado: icono y comando que la barra puede preferir
        rec["windows"] = [{"hwnd": h, "title": t, "class": c, "pid": p, "aumid": aumid(h)}
                          for h, t, c, p in windows() if c.startswith("Chrome_WidgetWin")]
        return rec
    finally:
        kill_edge()
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()


def main(argv):
    out_dir = os.path.abspath(argv[1] if len(argv) > 1 else "spike-out")
    os.makedirs(out_dir, exist_ok=True)
    ole32.CoInitialize(None)
    edge = edge_exe()
    report = {"system": system_info(), "edge": edge, "variants": []}
    if edge:
        try:
            import win32api  # noqa: F401  (pywin32 no hace falta; solo se anota si está)
            report["pywin32"] = True
        except ImportError:
            report["pywin32"] = False
        base = tempfile.mkdtemp(prefix="notas-spike-")
        kill_edge()
        for kind in ("ico", "ico-udd", "svg", "manifest"):
            print(f"== variante {kind}", flush=True)
            try:
                report["variants"].append(measure_variant(kind, base, out_dir, edge))
            except Exception as e:
                report["variants"].append({"variant": kind, "error": repr(e)})
            print(json.dumps(report["variants"][-1], ensure_ascii=False, indent=1, default=str), flush=True)
    else:
        report["error"] = "no hay msedge.exe"
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
    lines = [f"# Medición de iconos/AUMID · DPI {report['system']['dpi']} · SM_CXICON {report['system']['SM_CXICON']}"
             f" · SM_CXSMICON {report['system']['SM_CXSMICON']}", ""]
    for v in report["variants"]:
        lines.append(f"## {v.get('variant')}")
        lines.append(f"- perfil: {v.get('user_data_dir')}")
        lines.append(f"- AUMID: {v.get('aumid')!r} (primera lectura {v.get('aumid_first')!r})")
        lines.append(f"- System.AppUserModel.*: {v.get('appusermodel')}")
        for w in v.get("windows") or []:
            lines.append(f"- ventana {w.get('hwnd')} «{w.get('title')}» {w.get('class')} pid {w.get('pid')} "
                         f"AUMID {w.get('aumid')!r}")
        for when in ("icons", "icons_after_10s"):
            for name, info in (v.get(when) or {}).items():
                lines.append(f"- {when} {name}: {info}")
        for n in v.get("notes", []):
            lines.append(f"- NOTA: {n}")
        if v.get("error"):
            lines.append(f"- ERROR: {v['error']}")
        lines.append("")
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
