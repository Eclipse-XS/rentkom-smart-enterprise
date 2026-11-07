#!/usr/bin/env python3
"""Loopback-only least-privilege ingress for the public Telegram tunnel."""

import hmac
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEBHOOK_PATH = "/webhook/rentkom-telegram-ingress-webhook"
UPSTREAM_URL = f"http://127.0.0.1:5678{WEBHOOK_PATH}"
MAX_BODY_BYTES = 1024 * 1024


class RestrictedIngressHandler(BaseHTTPRequestHandler):
    server_version = "RentkomIngress/1.0"

    def _reply(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._reply(404, b'{"detail":"not found"}')

    def do_POST(self) -> None:
        if self.path != WEBHOOK_PATH:
            self._reply(404, b'{"detail":"not found"}')
            return
        expected = os.getenv("TELEGRAM_SECRET_TOKEN", "").strip()
        supplied = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not expected or not hmac.compare_digest(supplied, expected):
            self._reply(403, b'{"detail":"invalid Telegram webhook secret"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, b'{"detail":"invalid content length"}')
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._reply(413, b'{"detail":"invalid request size"}')
            return
        payload = self.rfile.read(length)
        request = urllib.request.Request(
            UPSTREAM_URL, data=payload, method="POST",
            headers={"Content-Type": self.headers.get("Content-Type", "application/json"),
                     "X-Telegram-Bot-Api-Secret-Token": supplied},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                self._reply(response.status, response.read())
        except urllib.error.HTTPError as exc:
            self._reply(exc.code, exc.read())
        except Exception:
            self._reply(502, b'{"detail":"upstream unavailable"}')

    def log_message(self, _format: str, *_args) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    ThreadingHTTPServer((host, port), RestrictedIngressHandler).serve_forever()


if __name__ == "__main__":
    serve(port=int(os.getenv("RENTKOM_INGRESS_PORT", "8765")))
