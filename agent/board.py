"""Serve the scoreboard on the local network, so it can be opened in a browser without being published.

Binds to every interface on the mini (default port 8787) and serves one page: the scoreboard, rebuilt
on request if the copy on disk is older than ten minutes. Nothing else is served and nothing is
written. Reachable only from the network the mini is on.

    python -m agent.board [--port 8787]
"""
from __future__ import annotations
import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import scoreboard, settings

MAX_AGE = 600


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_response(404); self.end_headers(); return
        out = scoreboard.OUT
        if not out.exists() or time.time() - out.stat().st_mtime > MAX_AGE:
            try:
                out.parent.mkdir(exist_ok=True)
                out.write_text(scoreboard.build())
            except Exception:
                pass
        body = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<style>body{margin:0}</style></head><body>" + out.read_text() + "</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # keep the service log quiet
        return


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8787)
    port = ap.parse_args().port
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
