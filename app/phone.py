"""Φωτογραφίες από το κινητό με QR code.

Η εφαρμογή ανοίγει έναν μικρό web server στο τοπικό δίκτυο (Wi-Fi). Το κινητό σκανάρει το QR,
ανοίγει μια σελίδα «Φωτογράφισε / Ανέβασε» και κάθε φωτογραφία φτάνει αμέσως στην εφαρμογή,
εκεί που δείχνει ο τρέχων στόχος (ενότητα του Corpus ή θέμα του διαγωνίσματος).

Ασφάλεια:
- ο server ανοίγει μόνο όταν το ζητήσει ο χρήστης και κλείνει με «Διακοπή» ή με το κλείσιμο της εφαρμογής·
- κάθε διεύθυνση περιέχει τυχαίο κλειδί (token) που αλλάζει σε κάθε εκκίνηση· χωρίς αυτό απαντά 404·
- δέχεται μόνο εικόνες, έως MAX_BYTES ανά αποστολή.
"""

from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .paths import resource

DEFAULT_PORT = 8766
MAX_BYTES = 40 * 1024 * 1024


def _is_lan(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local


def lan_ips() -> list[str]:
    """Οι διευθύνσεις IPv4 του υπολογιστή στο τοπικό δίκτυο, η πιθανότερη πρώτη."""
    found: list[str] = []
    # η διεπαφή της προεπιλεγμένης διαδρομής (δεν στέλνεται κανένα πακέτο με UDP connect)
    for probe in ("10.255.255.255", "192.168.255.255", "8.8.8.8"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect((probe, 1))
                ip = s.getsockname()[0]
            finally:
                s.close()
            if _is_lan(ip) and ip not in found:
                found.append(ip)
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_lan(ip) and ip not in found:
                found.append(ip)
    except OSError:
        pass
    # προτίμηση στα συνηθισμένα οικιακά δίκτυα (192.168.x.x), μετά 10.x, μετά 172.16–31
    order = lambda ip: (0 if ip.startswith("192.168.") else 1 if ip.startswith("10.") else 2)  # noqa: E731
    return sorted(found, key=order)


def qr_svg(text: str, scale: int = 6) -> str:
    import segno

    return segno.make(text, error="m").svg_inline(scale=scale, border=2, dark="#000", light="#fff")


class PhoneServer:
    """Ο server για το κινητό. `on_image(data_url, name, join)` → dict (π.χ. {"ok": True})."""

    def __init__(self, on_image: Callable[[str, str, bool], dict]):
        self.on_image = on_image
        self.token = secrets.token_urlsafe(12)
        self.target = ""
        self.count = 0
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self, port: int = DEFAULT_PORT) -> int:
        if self._httpd is not None:
            return self.port
        handler = self._handler()
        last_exc: Exception | None = None
        for p in (port, 0):  # αν η προεπιλεγμένη θύρα είναι πιασμένη, οποιαδήποτε ελεύθερη
            try:
                self._httpd = ThreadingHTTPServer(("0.0.0.0", p), handler)
                break
            except OSError as exc:
                last_exc = exc
        if self._httpd is None:
            raise RuntimeError(f"Δεν άνοιξε η σύνδεση για το κινητό: {last_exc}")
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            httpd, self._httpd = self._httpd, None
            threading.Thread(target=lambda: (httpd.shutdown(), httpd.server_close()), daemon=True).start()
        self.token = secrets.token_urlsafe(12)  # το παλιό QR δεν ισχύει πια
        self.count = 0

    def urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}/m/{self.token}" for ip in lan_ips()]

    # ------------------------------------------------------------------ HTTP
    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):  # σιωπηλός
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, data: dict) -> None:
                self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

            def _route(self) -> str | None:
                parts = self.path.split("?", 1)[0].strip("/").split("/")
                if len(parts) >= 2 and parts[0] == "m" and secrets.compare_digest(parts[1], server.token):
                    return "/".join(parts[2:])
                return None

            def do_GET(self):  # noqa: N802
                r = self._route()
                if r is None:
                    self._send(404, b"Not found", "text/plain")
                elif r == "":
                    html = resource("ui", "phone.html").read_bytes()
                    self._send(200, html, "text/html; charset=utf-8")
                elif r == "status":
                    self._json(200, {"target": server.target, "count": server.count})
                else:
                    self._send(404, b"Not found", "text/plain")

            def do_POST(self):  # noqa: N802
                r = self._route()
                if r != "upload":
                    self._send(404, b"Not found", "text/plain")
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                if n <= 0 or n > MAX_BYTES:
                    self._json(413, {"ok": False, "error": "Η φωτογραφία είναι πολύ μεγάλη."})
                    return
                try:
                    data = json.loads(self.rfile.read(n).decode("utf-8"))
                    res = server.on_image(str(data.get("data", "")), str(data.get("name", ""))[:120], bool(data.get("join")))
                except Exception as exc:  # noqa: BLE001
                    res = {"ok": False, "error": f"Σφάλμα: {exc}"}
                if res.get("ok"):
                    server.count += 1
                self._json(200 if res.get("ok") else 400, {**res, "target": server.target, "count": server.count})

        return Handler
