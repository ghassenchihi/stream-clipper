"""Serve Videos\\clipper\\clips to THIS PC's browser only (127.0.0.1), so Claude can put a clip
into YouTube Studio's upload box without the Windows file picker. Read-only, no directory listing."""
import http.server
import os
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "clips"
PORT = 8766


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def end_headers(self):
        origin = self.headers.get("Origin", "")
        if origin in ("https://studio.youtube.com", "https://www.youtube.com"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def list_directory(self, path):
        self.send_error(403)

    def do_GET(self):
        if self.path.split("?")[0] == "/list":  # names of clips waiting (for Claude)
            names = sorted(str(p.relative_to(ROOT)).replace(os.sep, "/") for p in ROOT.rglob("*.mp4"))
            body = "\n".join(names).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?")[0] == "/bridge.html":  # hands one clip to the Studio tab that opened it
            body = BRIDGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self.path.split("?")[0].endswith(".mp4"):
            self.send_error(403)
            return
        super().do_GET()

    def log_message(self, *a):
        pass


BRIDGE = """<!doctype html><title>clip bridge</title><body>sending...<script>
(async () => {
  const name = new URLSearchParams(location.search).get("f");
  try {
    const buf = await (await fetch("/" + name)).arrayBuffer();
    (window.opener || window.parent).postMessage({clipBridge: true, name: name.split("/").pop(), buf}, "https://studio.youtube.com", [buf]);
    document.body.textContent = "sent " + name;
  } catch (e) { document.body.textContent = "error " + e; }
  if (window.opener) setTimeout(() => window.close(), 1500);
})();
</script></body>"""


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    import socket
    try:  # already running -> don't start a second copy
        socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
        raise SystemExit(0)
    except OSError:
        pass
    S.allow_reuse_address = False
    with S(("127.0.0.1", PORT), H) as s:
        s.serve_forever()
