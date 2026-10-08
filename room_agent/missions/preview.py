"""Local previews of demo sites: http://127.0.0.1:<MISSION_PREVIEW_PORT>/<mission folder>/sites/<site>/

    - bound to 127.0.0.1 only (not reachable from the network); requests with another Host header are refused
      (DNS-rebinding protection, same rule as the dashboard)
    - serves only files inside a mission's sites/ folder, never .versions/, databases, outreach drafts or anything else
    - started on demand (the first time a preview is opened), in a daemon thread
"""

import functools
import http.server
import logging
import threading
import urllib.parse
from pathlib import Path

from room_agent import config

log = logging.getLogger("room-agent")
_server = {"httpd": None}
_lock = threading.Lock()
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json",
         ".svg": "image/svg+xml", ".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
         ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".ico": "image/x-icon"}


class _Handler(http.server.SimpleHTTPRequestHandler):
    def _allowed(self):
        port = config.MISSION_PREVIEW_PORT
        if (self.headers.get("Host") or "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False, 403
        parts = [p for p in urllib.parse.unquote(urllib.parse.urlparse(self.path).path).split("/") if p]
        if len(parts) < 3 or parts[1] != "sites" or any(p.startswith(".") or p == ".." for p in parts):
            return False, 404
        root = Path(config.MISSIONS_DIR).resolve()
        target = (root / Path(*parts)).resolve()
        if root not in target.parents:
            return False, 404
        if target.is_dir():
            if not urllib.parse.urlparse(self.path).path.endswith("/"):
                return False, "slash"  # (relative links in the page need the trailing slash)
            target = target / "index.html"
        if not target.is_file() or target.suffix.lower() not in TYPES:
            return False, 404
        return True, target

    def do_GET(self):
        ok, what = self._allowed()
        if what == "slash":
            self.send_response(301)
            self.send_header("Location", urllib.parse.urlparse(self.path).path + "/")
            self.end_headers()
            return
        if not ok:
            self.send_error(what)
            return
        body = what.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES[what.suffix.lower()])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                                    "script-src 'none'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_error(405)

    def do_POST(self):
        self.send_error(405)

    def log_message(self, fmt, *args):
        log.debug("preview: " + fmt, *args)


def start():
    """Start the preview server if it isn't running. -> True if it's serving."""
    with _lock:
        if _server["httpd"] is not None:
            return True
        try:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", config.MISSION_PREVIEW_PORT),
                                                    functools.partial(_Handler, directory=str(config.MISSIONS_DIR)))
        except OSError as e:
            log.warning("mission preview server not started on port %s: %s", config.MISSION_PREVIEW_PORT, e)
            return False
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, name="mission-preview", daemon=True).start()
        _server["httpd"] = httpd
        log.info("mission previews on http://127.0.0.1:%s/", config.MISSION_PREVIEW_PORT)
        return True


def url_for(folder):
    """The preview address of a demo-site folder (inside MISSIONS_DIR)."""
    rel = Path(folder).resolve().relative_to(Path(config.MISSIONS_DIR).resolve())
    return f"http://127.0.0.1:{config.MISSION_PREVIEW_PORT}/" + "/".join(urllib.parse.quote(p) for p in rel.parts) + "/"
