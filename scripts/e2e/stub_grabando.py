# -*- coding: utf-8 -*-
"""Doble de Chuleta con una grabación en curso, para el E2E del desinstalador (lo arranca scripts/e2e/lookfeel_e2e.ps1).

    python scripts/e2e/stub_grabando.py 8765

Contesta como la app: GET /api/ping -> {"app": "audios"}, GET /api/rec/status -> {"recording": true} y
POST /api/apagar -> 409 (lo que hace server.App.apagar con la grabadora viva). Escucha solo en 127.0.0.1.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/ping":
            return self._json(200, {"app": "audios"})
        if self.path == "/api/rec/status":
            return self._json(200, {"recording": True})
        return self._json(404, {"error": "no"})

    def do_POST(self):
        if self.path == "/api/apagar" and self.headers.get("X-Audios") == "1":
            return self._json(409, {"error": "Hay una grabación en curso. Para la grabación antes de apagar Chuleta."})
        return self._json(403, {"error": "forbidden"})


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8765), Handler).serve_forever()
