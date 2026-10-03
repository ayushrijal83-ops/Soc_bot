import ipaddress
import socket
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
REAL_DB = PROJECT / "data" / "publisher.db"
REAL_LOG = PROJECT / "logs" / "soc_bot.log"


class ExternalNetworkBlocked(RuntimeError):
    """Not an OSError on purpose: production code that tolerates network errors must not swallow it."""


def _local(host) -> bool:
    if host is None or host in ("", "localhost"):
        return True
    if isinstance(host, bytes):
        host = host.decode()
    try:
        address = ipaddress.ip_address(str(host).split("%", 1)[0])
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


@pytest.fixture(autouse=True)
def _no_external_network(monkeypatch):
    """Tests never reach the Internet: name lookups and connections are allowed only for loopback.
    Mocked HTTP clients (httpx.MockTransport, patched requests) never touch sockets and are unaffected.
    ponytail: covers socket connect/connect_ex/getaddrinfo; a raw asyncio Proactor connect to a literal
    public IP would bypass it (none exists in the code base)."""
    real_getaddrinfo, real_connect, real_connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex

    def getaddrinfo(host, *args, **kwargs):
        if not _local(host):
            raise ExternalNetworkBlocked(f"test tried to resolve external host {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def check(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _local(address[0]):
            raise ExternalNetworkBlocked(f"test tried to connect to external address {address!r}")

    def connect(self, address):
        check(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(self, address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture(autouse=True)
def _isolated_database(monkeypatch, tmp_path):
    """Default database for every test is a temporary file, and nothing may ever open the real
    data/publisher.db (tests that build their own Database keep doing so)."""
    from sqlalchemy.engine import make_url

    from src.storage import database

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'default_test.db').as_posix()}")
    real_create_engine = database.create_engine

    def guarded_create_engine(url, *args, **kwargs):
        parsed = make_url(str(url))
        if parsed.get_backend_name() == "sqlite" and parsed.database and parsed.database != ":memory:":
            path = Path(parsed.database)
            if path.anchor and path.resolve() == REAL_DB.resolve():
                raise AssertionError("a test tried to open the real data/publisher.db")
        return real_create_engine(url, *args, **kwargs)

    monkeypatch.setattr(database, "create_engine", guarded_create_engine)


@pytest.fixture(autouse=True, scope="session")
def _real_files_untouched():
    """Regression check: the whole test session (subprocesses included) leaves the real database and the real
    log file exactly as they were."""
    def snapshot():
        return {p.name: (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in (REAL_DB, REAL_LOG)}

    before = snapshot()
    yield
    assert snapshot() == before, "the test session modified data/publisher.db or logs/soc_bot.log"


@pytest.fixture(autouse=True)
def _no_real_cloudflared(monkeypatch, tmp_path):
    """Tests never find (or start) a real cloudflared; tunnel tests opt in with their own fake."""
    monkeypatch.setenv("CLOUDFLARED_PATH", str(tmp_path / "no-cloudflared.exe"))


@pytest.fixture(autouse=True)
def _private_publish_lock(monkeypatch, tmp_path):
    """Each test gets its own publishing lock file: never the project's data/publishing.lock, so a running
    Soc_bot window can't make tests busy (and tests never touch the real lock)."""
    from src.core import publish_lock

    monkeypatch.setattr(publish_lock, "LOCK_PATH", tmp_path / "publishing.lock")
