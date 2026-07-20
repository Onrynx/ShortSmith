"""Zero-dependency fallback server (stdlib http.server) mirroring the FastAPI API.

Lets the control app boot with no pip installs. Same routes, same store logic.
Basic Range support so <video> playback works.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import store

PWA_DIR = Path(__file__).resolve().parent / "pwa"
_CT = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json",
    ".webmanifest": "application/manifest+json", ".png": "image/png",
    ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".mp4": "video/mp4", ".txt": "text/plain; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "StoryForge/1.0"

    # -- helpers -------------------------------------------------------- #
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, q) -> bool:
        tok = store.app_token()
        header = self.headers.get("Authorization", "")
        given = header[7:] if header.lower().startswith("bearer ") else (q.get("token", [""])[0])
        return given == tok

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _send_file(self, path: Path, allow_range=False):
        if not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        ctype = _CT.get(path.suffix, "application/octet-stream")
        size = path.stat().st_size
        rng = self.headers.get("Range")
        if allow_range and rng and rng.startswith("bytes="):
            try:
                s, _, e = rng[6:].partition("-")
                start = int(s) if s else 0
                end = int(e) if e else size - 1
                end = min(end, size - 1)
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                with open(path, "rb") as fh:
                    fh.seek(start)
                    self.wfile.write(fh.read(length))
                return
            except Exception:
                pass
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(path.read_bytes())

    # -- routing -------------------------------------------------------- #
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if path.startswith("/api/"):
            if not self._authed(q):
                return self._json({"error": "unauthorized"}, 401)
            if path == "/api/config":
                return self._json(store.config_info())
            if path == "/api/status":
                return self._json(store.status())
            if path == "/api/pending":
                return self._json(store.list_pending())
            if path == "/api/history":
                return self._json(store.history())
            if path == "/api/assignment":
                return self._json(store.get_assignment_public())
            if path == "/api/odysseus":
                return self._json(store.odysseus_status())
            if path == "/api/events":
                return self._json(store.events(int(q.get("limit", ["100"])[0])))
            if path.startswith("/api/pending/"):
                return self._json(store.get_pending(path.split("/")[-1]) or {"error": "not found"})
            if path.startswith("/api/media/"):
                parts = path.split("/")
                p = store.media_path(parts[-2], parts[-1])
                return self._send_file(p, allow_range=True) if p else self._json({"error": "no media"}, 404)
            return self._json({"error": "unknown endpoint"}, 404)

        # static PWA
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (PWA_DIR / rel).resolve()
        if not str(target).startswith(str(PWA_DIR.resolve())):
            return self._json({"error": "forbidden"}, 403)
        return self._send_file(target)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)
        body = self._body()
        # Webhook trigger uses its own token (may differ from the app token).
        if path == "/api/trigger":
            tok = store.webhook_token()
            header = self.headers.get("Authorization", "")
            given = header[7:] if header.lower().startswith("bearer ") else (q.get("token", [""])[0])
            if given != tok:
                return self._json({"error": "unauthorized"}, 401)
            return self._json(store.trigger(body))
        if not path.startswith("/api/") or not self._authed(q):
            return self._json({"error": "unauthorized"}, 401)
        if path.endswith("/approve"):
            return self._json(store.approve(path.split("/")[-2]))
        if path.endswith("/reject"):
            return self._json(store.reject(path.split("/")[-2]))
        if path == "/api/assignment":
            return self._json(store.set_lanes(body.get("night"), body.get("day")))
        if path == "/api/run":
            return self._json(store.run_now(body.get("channel"), body.get("lane", "night"),
                                            bool(body.get("mock", False))))
        if path == "/api/push/subscribe":
            return self._json(store.subscribe_push(body.get("subscription") or body))
        return self._json({"error": "unknown endpoint"}, 404)

    def log_message(self, *args):  # quieter console
        pass


def serve(host: str, port: int):
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"[stdlib server] listening on http://{host}:{port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
