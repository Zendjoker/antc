"""Serve a production Next export on one fixed local port and proxy /api to the dashboard.

The launcher starts this with `python -m launcher.staticfront`. It binds 127.0.0.1:3010
and exits if that port is taken. It does not choose another port.
"""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PORT = 3010
LOCAL = {"127.0.0.1", "localhost", "::1"}


def local_host(host: str) -> bool:
    name = (host or "").strip().lower()
    if name.startswith("["):
        name = name[1:].split("]", 1)[0]
    elif name.count(":") == 1:
        name = name.split(":", 1)[0]
    return name in LOCAL


def safe_file(root: Path, url_path: str) -> Path | None:
    path = urlsplit(url_path).path or "/"
    if path.endswith("/"):
        path += "index.html"
    candidate = (root / path.lstrip("/")).resolve()
    base = root.resolve()
    if candidate != base and base not in candidate.parents:
        return None
    if candidate.is_file():
        return candidate
    return None


class StaticHandler(BaseHTTPRequestHandler):
    root = Path(".")
    backend = "http://127.0.0.1:8765"

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def log_message(self, fmt, *args):
        return

    def _handle(self) -> None:
        if not local_host(self.headers.get("Host", "")):
            self.send_error(403, "not a local request")
            return
        path = urlsplit(self.path).path
        if path.startswith("/api/") or path.startswith("/signin"):
            self._proxy()
            return
        file = safe_file(self.root, self.path)
        if file is None:
            self.send_error(404)
            return
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        target = self.backend.rstrip("/") + self.path
        headers = {key: value for key, value in self.headers.items() if key.lower() != "host"}
        parsed = urlsplit(self.backend)
        headers["Host"] = parsed.netloc
        request = Request(target, data=body, headers=headers, method=self.command)
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read()
                self.send_response(response.status)
                for key, value in response.headers.items():
                    if key.lower() in ("transfer-encoding", "connection", "content-length"):
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, URLError, TimeoutError):
            self.send_error(502)


def serve(root: Path, port: int, backend: str) -> None:
    handler = type("BoundStatic", (StaticHandler,), {"root": Path(root), "backend": backend})
    try:
        server = ThreadingHTTPServer(("127.0.0.1", int(port)), handler)
    except OSError:
        raise SystemExit(f"port {port} is already in use")
    server.serve_forever()


def main() -> None:
    root = Path(os.environ.get("ZEND_STATIC_ROOT", "")).resolve()
    port = int(os.environ.get("ZEND_STATIC_PORT", str(PORT)))
    backend = os.environ.get("ZEND_BACKEND_URL", "http://127.0.0.1:8765")
    if port != PORT:
        raise SystemExit("the production UI only listens on port 3010")
    if not (root / "index.html").is_file():
        raise SystemExit("frontend/out/index.html is missing")
    serve(root, port, backend)


if __name__ == "__main__":
    main()
