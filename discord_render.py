"""Entrée Render : bot Discord et endpoint HTTP de santé minimal."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from threading import Thread

from dotenv import load_dotenv

from discord_bot import client, configured_token


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return
        body = b'{"status":"ok","service":"luxe-radar-discord"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def start_health_server() -> ThreadingHTTPServer:
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    Thread(target=server.serve_forever, name="render-health", daemon=True).start()
    return server


def main() -> None:
    load_dotenv()
    token = configured_token()
    if not token:
        raise SystemExit("LUXE_RADAR_DISCORD_BOT_TOKEN manquant")
    start_health_server()
    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
