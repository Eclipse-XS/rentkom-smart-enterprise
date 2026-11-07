import http.client
import threading
from http.server import ThreadingHTTPServer

import pytest

from tools.restricted_ingress import RestrictedIngressHandler, WEBHOOK_PATH


@pytest.fixture
def ingress(monkeypatch):
    monkeypatch.setenv("TELEGRAM_SECRET_TOKEN", "expected-secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), RestrictedIngressHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _request(address, method, path, *, secret=None, body=b"{}"):
    connection = http.client.HTTPConnection(*address, timeout=3)
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    if secret:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    status = response.status
    response.read()
    connection.close()
    return status


def test_editor_admin_and_unknown_routes_are_blocked(ingress):
    for path in ("/", "/rest/workflows", "/signin", "/webhook/unknown"):
        assert _request(ingress, "GET", path) == 404


def test_webhook_requires_exact_telegram_secret(ingress):
    assert _request(ingress, "POST", WEBHOOK_PATH) == 403
    assert _request(ingress, "POST", WEBHOOK_PATH, secret="forged") == 403


def test_valid_secret_reaches_only_fixed_upstream(ingress, monkeypatch):
    class Upstream:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b'{"ok":true}'
    seen = []
    def fake_urlopen(request, timeout):
        seen.append((request.full_url, request.headers, timeout))
        return Upstream()
    monkeypatch.setattr("tools.restricted_ingress.urllib.request.urlopen", fake_urlopen)
    assert _request(ingress, "POST", WEBHOOK_PATH, secret="expected-secret") == 200
    assert seen[0][0].endswith(WEBHOOK_PATH)
