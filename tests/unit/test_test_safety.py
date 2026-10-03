"""The autouse guards in tests/conftest.py: no external network, never the real database."""

import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from src.storage.database import Database
from tests.conftest import REAL_DB, ExternalNetworkBlocked


@pytest.mark.parametrize("host", ["graph.instagram.com", "www.googleapis.com", "open.tiktokapis.com"])
def test_external_name_lookup_is_blocked(host):
    with pytest.raises(ExternalNetworkBlocked):
        socket.getaddrinfo(host, 443)


def test_external_connect_is_blocked_even_by_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(ExternalNetworkBlocked):
        sock.connect(("203.0.113.10", 443))  # TEST-NET-3: never routable anyway


def test_external_http_client_call_fails_loudly():
    with pytest.raises(ExternalNetworkBlocked):
        httpx.get("https://graph.instagram.com/me", timeout=2)


def test_loopback_http_still_works():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    try:
        assert httpx.get(f"http://127.0.0.1:{server.server_port}/", timeout=5).text == "ok"
        assert socket.getaddrinfo("localhost", 80)
    finally:
        server.server_close()


def test_mocked_http_transport_is_unaffected():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="mocked")))
    assert client.get("https://graph.instagram.com/me").text == "mocked"


def test_default_database_url_is_temporary(tmp_path):
    assert os.environ["DATABASE_URL"].endswith("default_test.db")
    assert tmp_path.as_posix() in os.environ["DATABASE_URL"]


@pytest.mark.parametrize("url", ["sqlite:///data/publisher.db", f"sqlite:///{REAL_DB.as_posix()}"])
def test_opening_the_real_database_is_refused(url):
    with pytest.raises(AssertionError, match="real data/publisher.db"):
        Database(url)
