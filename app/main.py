"""Εκκίνηση της εφαρμογής.

Κανονικά ανοίγει παράθυρο Windows (pywebview + WebView2). Με `--browser` ανοίγει
την ίδια οθόνη στον browser μέσω τοπικού server — χρήσιμο για δοκιμές ή αν λείπει το WebView2.
"""

from __future__ import annotations

import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in (None, ""):
    # εκκίνηση ως script (π.χ. από το PyInstaller) — κάνε το «app» εισαγώγιμο πακέτο
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "app"  # noqa: A001

from app import APP_TITLE  # noqa: E402
from app.bridge import Api  # noqa: E402
from app.paths import resource  # noqa: E402


def run_window(api: Api) -> None:
    import webview

    window = webview.create_window(
        APP_TITLE,
        url=str(resource("ui", "index.html")),
        js_api=api,
        width=1320,
        height=900,
        min_size=(1000, 700),
        text_select=True,
    )
    api.attach_window(window)
    import os

    gui = "edgechromium" if os.name == "nt" else None  # χωρίς σιωπηλή υποβάθμιση σε παλιό μηχανισμό
    webview.start(gui=gui, debug="--debug" in sys.argv, private_mode=False)


class _Handler(BaseHTTPRequestHandler):
    api: Api = None  # type: ignore[assignment]
    root: Path = resource("ui")

    def log_message(self, *_):  # σιωπηλός server
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        rel = self.path.split("?")[0].lstrip("/") or "index.html"
        target = (self.root / rel).resolve()
        if not str(target).startswith(str(self.root.resolve())) or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".woff2": "font/woff2", ".svg": "image/svg+xml",
                 ".png": "image/png"}
        self._send(200, target.read_bytes(), types.get(target.suffix, "application/octet-stream"))

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/api/"):
            return self._send(404, b"not found", "text/plain")
        name = self.path[len("/api/"):]
        length = int(self.headers.get("Content-Length") or 0)
        args = json.loads(self.rfile.read(length) or b"[]")
        fn = getattr(self.api, name, None)
        if name.startswith("__") or not callable(fn):
            return self._send(404, b"no such method", "text/plain")
        try:
            result = fn(*args)
            body = json.dumps({"ok": True, "result": result}, ensure_ascii=False).encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")


def run_browser(api: Api, port: int = 8765, open_browser: bool = True) -> None:
    _Handler.api = api
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}/index.html"
    print(f"{APP_TITLE}: {url}", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main() -> None:
    for a in sys.argv:
        if a.startswith("--selftest"):
            from app.selftest import run

            report = a.split("=", 1)[1] if "=" in a else None
            sys.exit(run(report))
    api = Api()
    if "--browser" in sys.argv or "--serve" in sys.argv:
        port = 8765
        for a in sys.argv:
            if a.startswith("--port="):
                port = int(a.split("=", 1)[1])
        run_browser(api, port, open_browser="--serve" not in sys.argv)
        return
    try:
        run_window(api)
    except Exception as exc:  # noqa: BLE001 — π.χ. λείπει το WebView2
        print(f"Αποτυχία παραθύρου ({exc}). Άνοιγμα στον browser.", flush=True)
        run_browser(api)


if __name__ == "__main__":
    main()
