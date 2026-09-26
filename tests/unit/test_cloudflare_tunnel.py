"""Cloudflare Quick Tunnel media provider. Real loopback HTTP server; cloudflared and the Internet are faked."""

import hashlib
import logging
import socket
import threading
from unittest.mock import patch

import httpx
import pytest

from src.media_storage import (
    MediaStorageError,
    StorageNotConfiguredError,
    StorageSettings,
    build_router,
    create_media_provider,
    delivery_provider,
    media_delivery_description,
)
from src.media_storage.cloudflare_tunnel import (
    CloudflareTunnelMediaProvider,
    _parse_range,
    find_cloudflared,
    parse_tunnel_url,
)
from src.media_storage.tempfile import TempFileMediaStorage
from src.platforms.instagram.publisher import InstagramPublisher
from tests.unit.test_publishers import IG, Progress, ctx, resp
from tests.unit.test_publishers import Provider as GraphProvider
from tests.unit.test_publishing_engine import engine, env  # noqa: F401

TUNNEL = "https://quiet-river-demo-1234.trycloudflare.com"
BANNER = [
    "2026-09-25T10:00:00Z INF Thank you for trying Cloudflare Tunnel.\n",
    "2026-09-25T10:00:00Z INF +--------------------------------------------------------------+\n",
    f"2026-09-25T10:00:01Z INF |  {TUNNEL}                     |\n",
    "2026-09-25T10:00:02Z INF Registered tunnel connection connIndex=0\n",
]


class FakeProcess:
    """Stands in for the cloudflared child: prints the banner, then runs until terminated."""

    def __init__(self, lines, exit_early=False):
        self._lines = lines
        self._done = threading.Event()
        self.returncode = None
        self.terminated = False
        if exit_early:
            self.returncode = 1
            self._done.set()
        self.stdout = self._stream()

    def _stream(self):
        yield from self._lines
        self._done.wait()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0
        self._done.set()

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode


class FakePopen:
    def __init__(self, lines=BANNER, exit_early=False):
        self.lines, self.exit_early = lines, exit_early
        self.calls, self.processes = [], []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        process = FakeProcess(self.lines, self.exit_early)
        self.processes.append(process)
        return process


@pytest.fixture
def exe(tmp_path, monkeypatch):
    path = tmp_path / "cloudflared.exe"
    path.write_bytes(b"")
    monkeypatch.setenv("CLOUDFLARED_PATH", str(path))
    return str(path)


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "secret_folder" / "My Holiday.mp4"
    p.parent.mkdir()
    p.write_bytes(bytes(range(256)) * 12_000 + b"end")  # ~3 MB: several 1 MiB chunks
    return p


def provider(exe, popen=None, **kw):
    return CloudflareTunnelMediaProvider(exe, startup_timeout=kw.pop("startup_timeout", 5),
                                         popen=popen or FakePopen(), verify_public=kw.pop("verify_public", False), **kw)


def local_url(handle):
    """The same route, straight to the loopback origin (what cloudflared forwards to)."""
    host, port = handle.session.server.server_address[:2]
    return f"http://{host}:{port}" + handle.public_url.removeprefix(TUNNEL)


@pytest.fixture
def served(exe, video):
    p = provider(exe)
    handle = p.prepare(video, "video/mp4")
    yield p, handle
    p.cleanup(handle)


# ---------------------------------------------------------------------------------------------
# Provider set-up
# ---------------------------------------------------------------------------------------------

class TestSetup:
    def test_missing_cloudflared_is_actionable(self, tmp_path, video):
        p = CloudflareTunnelMediaProvider(str(tmp_path / "nope.exe"), popen=FakePopen())
        with pytest.raises(StorageNotConfiguredError, match="cloudflared not found.*winget install"):
            p.prepare(video, "video/mp4")
        with pytest.raises(StorageNotConfiguredError, match="cloudflared not found"):
            p.health_check()

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "localhost", "::"])
    def test_only_loopback_bind(self, exe, host):
        with pytest.raises(StorageNotConfiguredError, match="127.0.0.1"):
            CloudflareTunnelMediaProvider(exe, host=host)

    def test_find_cloudflared(self, exe, tmp_path):
        assert find_cloudflared(exe) == exe
        assert find_cloudflared(str(tmp_path / "missing.exe")) is None

    def test_starts_cloudflared_for_the_loopback_origin_only(self, exe, video):
        popen = FakePopen()
        p = provider(exe, popen)
        handle = p.prepare(video, "video/mp4")
        try:
            args, kwargs = popen.calls[0]
            port = handle.session.server.server_address[1]
            assert args == [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
            assert kwargs["stdout"] is not None and kwargs["stdin"] is not None  # piped, never inherits the console
            assert handle.session.server.server_address[0] == "127.0.0.1"
        finally:
            p.cleanup(handle)

    def test_random_unguessable_path_without_file_name(self, exe, video):
        p = provider(exe)
        a, b = p.prepare(video, "video/mp4"), p.prepare(video, "video/mp4")
        try:
            for h in (a, b):
                path = h.public_url.removeprefix(TUNNEL)
                assert path.startswith("/media/") and path.endswith(".mp4")
                assert len(path.removeprefix("/media/").removesuffix(".mp4")) == 32  # 16 bytes hex
                assert "Holiday" not in h.public_url and "secret_folder" not in h.public_url
            assert a.public_url != b.public_url
            assert a.public_url not in repr(a)
        finally:
            p.cleanup(a)
            p.cleanup(b)

    def test_token_size_configurable(self, exe, video):
        p = provider(exe, token_bytes=32)
        h = p.prepare(video, "video/mp4")
        try:
            assert len(h.public_url.rsplit("/", 1)[1]) == 64 + len(".mp4")
        finally:
            p.cleanup(h)

    def test_rejects_bad_input(self, exe, tmp_path):
        p = provider(exe)
        with pytest.raises(MediaStorageError, match="not found"):
            p.prepare(tmp_path / "missing.mp4", "video/mp4")
        empty = tmp_path / "empty.mp4"
        empty.write_bytes(b"")
        with pytest.raises(MediaStorageError, match="empty"):
            p.prepare(empty, "video/mp4")


# ---------------------------------------------------------------------------------------------
# Local single-file server (real HTTP on 127.0.0.1)
# ---------------------------------------------------------------------------------------------

class TestServer:
    def test_get_streams_the_exact_file(self, served, video):
        _, handle = served
        r = httpx.get(local_url(handle))
        assert r.status_code == 200
        assert r.headers["content-type"] == "video/mp4"
        assert r.headers["content-length"] == str(video.stat().st_size)
        assert hashlib.sha256(r.content).hexdigest() == hashlib.sha256(video.read_bytes()).hexdigest()

    def test_head(self, served, video):
        _, handle = served
        r = httpx.head(local_url(handle))
        assert r.status_code == 200 and r.content == b""
        assert r.headers["content-length"] == str(video.stat().st_size)
        assert r.headers["accept-ranges"] == "bytes" and r.headers["content-type"] == "video/mp4"

    def test_range_requests(self, served, video):
        _, handle = served
        data, size = video.read_bytes(), video.stat().st_size
        r = httpx.get(local_url(handle), headers={"Range": "bytes=10-19"})
        assert r.status_code == 206 and r.content == data[10:20]
        assert r.headers["content-range"] == f"bytes 10-19/{size}"
        assert httpx.get(local_url(handle), headers={"Range": "bytes=-3"}).content == b"end"
        assert httpx.get(local_url(handle), headers={"Range": f"bytes={size - 5}-"}).content == data[-5:]
        bad = httpx.get(local_url(handle), headers={"Range": f"bytes={size + 10}-"})
        assert bad.status_code == 416 and bad.headers["content-range"] == f"bytes */{size}"

    @pytest.mark.parametrize("path", ["/", "/media/", "/media", "/media/deadbeef.mp4", "/.env", "/secrets/x",
                                      "/media/../.env", "/%2e%2e/%2e%2e/.env", "/My%20Holiday.mp4",
                                      "/data/publisher.db"])
    def test_everything_else_is_404(self, served, path):
        _, handle = served
        host, port = handle.session.server.server_address[:2]
        r = httpx.get(f"http://{host}:{port}{path}")
        assert r.status_code == 404
        assert "secret_folder" not in r.text and "Holiday" not in r.text

    def test_traversal_appended_to_the_token_route_is_404(self, served):
        _, handle = served
        assert httpx.get(local_url(handle) + "/../../.env").status_code == 404
        assert httpx.get(local_url(handle) + "x").status_code == 404

    def test_other_methods_rejected(self, served):
        _, handle = served
        assert httpx.post(local_url(handle)).status_code == 501  # no body: an unread body can reset the socket on Windows
        assert httpx.delete(local_url(handle)).status_code == 501

    def test_request_paths_are_never_logged(self, exe, video, capfd, caplog):
        p = provider(exe)
        with caplog.at_level(logging.DEBUG):
            handle = p.prepare(video, "video/mp4")
            httpx.get(local_url(handle))
            httpx.get(local_url(handle).replace("/media/", "/other/"))
            token = handle.public_url.rsplit("/", 1)[1]
            p.cleanup(handle)
        out = capfd.readouterr()
        assert token not in out.err + out.out + caplog.text
        assert TUNNEL not in caplog.text and str(video) not in caplog.text


# ---------------------------------------------------------------------------------------------
# cloudflared process and tunnel URL
# ---------------------------------------------------------------------------------------------

class TestTunnel:
    @pytest.mark.parametrize("line,expected", [
        (BANNER[2], TUNNEL),
        ("INF Requesting new quick Tunnel on trycloudflare.com...\n", None),
        ("see https://developers.cloudflare.com/ for docs\n", None),
        ("|  http://plain-http.trycloudflare.com  |\n", None),
        ("|  https://evil.com/?x=.trycloudflare.com  |\n", None),
        ('ERR failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": EOF\n', None),
        ("INF see https://www.trycloudflare.com for details\n", None),
        ("|  https://quiet-river.trycloudflare.com.evil.net  |\n", None),
    ])
    def test_parse_tunnel_url(self, line, expected):
        assert parse_tunnel_url(line) == expected

    def test_public_url_is_tunnel_host_plus_route(self, served):
        _, handle = served
        assert handle.public_url.startswith(TUNNEL + "/media/")

    def test_startup_timeout_cleans_everything(self, exe, video):
        popen = FakePopen(lines=["INF Requesting new quick Tunnel...\n"])
        p = provider(exe, popen, startup_timeout=0.3)
        with pytest.raises(MediaStorageError, match="did not start within") as e:
            p.prepare(video, "video/mp4")
        assert e.value.retryable
        assert popen.processes[0].terminated

    def test_cloudflared_exits_early(self, exe, video):
        popen = FakePopen(lines=["2026 ERR failed to request quick Tunnel: https://api.trycloudflare.com/tunnel 429\n"],
                          exit_early=True)
        with pytest.raises(MediaStorageError, match="exited before the tunnel was ready.*Tunnel: <url> 429"):
            provider(exe, popen).prepare(video, "video/mp4")

    def test_start_failure_is_actionable(self, exe, video):
        def broken(*a, **k):
            raise FileNotFoundError("x")
        with pytest.raises(StorageNotConfiguredError, match="could not be started"):
            provider(exe, broken).prepare(video, "video/mp4")

    def test_waits_for_public_dns_then_heads_the_ip_with_sni(self, exe, video):
        doh = iter([httpx.Response(200, json={"Status": 3}),                                   # NXDOMAIN
                    httpx.Response(200, json={"Answer": [{"type": 5, "data": "cname."},
                                                         {"type": 1, "data": "10.0.0.1"}]}),  # private: ignored
                    httpx.Response(200, json={"Answer": [{"type": 1, "data": "104.16.230.132"}]}),
                    httpx.Response(200, json={"Answer": [{"type": 1, "data": "104.16.230.132"}]})])
        heads = iter([httpx.Response(530), httpx.Response(200, headers={"content-length": str(video.stat().st_size)})])
        seen, resolvers = [], []

        def handler(request):
            if request.url.host in ("cloudflare-dns.com", "dns.google"):
                assert request.url.params["name"] == TUNNEL.removeprefix("https://")
                resolvers.append(request.url.host)
                return next(doh)
            seen.append(request)
            assert request.method == "HEAD" and request.url.host == "104.16.230.132"
            assert request.headers["host"] == TUNNEL.removeprefix("https://")
            assert request.extensions["sni_hostname"] == TUNNEL.removeprefix("https://")
            return next(heads)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        p = provider(exe, client=client, verify_public=True)
        with patch("src.media_storage.cloudflare_tunnel.time.sleep"):
            handle = p.prepare(video, "video/mp4")
        assert len(seen) == 2 and seen[0].url.path.startswith("/media/")
        assert resolvers[:2] == ["cloudflare-dns.com", "dns.google"]  # alternated: independent caches
        p.cleanup(handle)

    def test_unreachable_public_url_fails_and_cleans(self, exe, video):
        popen = FakePopen()

        def handler(request):
            if request.url.host in ("cloudflare-dns.com", "dns.google"):
                return httpx.Response(200, json={"Answer": [{"type": 1, "data": "104.16.230.132"}]})
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        p = provider(exe, popen, client=client, verify_public=True, startup_timeout=0.2)
        with patch("src.media_storage.cloudflare_tunnel.time.sleep"),                 pytest.raises(MediaStorageError, match="not reachable.*HTTP 404"):
            p.prepare(video, "video/mp4")
        assert popen.processes[0].terminated

    def test_never_in_public_dns(self, exe, video):
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"Status": 3})))
        p = provider(exe, client=client, verify_public=True, startup_timeout=0.2)
        with patch("src.media_storage.cloudflare_tunnel.time.sleep"),                 pytest.raises(MediaStorageError, match="not in public DNS yet"):
            p.prepare(video, "video/mp4")

    def test_health_check_is_local(self, exe):
        with patch("src.media_storage.cloudflare_tunnel.subprocess.run") as run, \
                patch("src.media_storage.cloudflare_tunnel.subprocess.Popen", side_effect=AssertionError("tunnel")):
            run.return_value.stdout = "cloudflared version 2026.9.0 (built 2026-09-01)\n"
            message = CloudflareTunnelMediaProvider(exe).health_check()
        assert "available (cloudflared version 2026.9.0" in message and "testing/development" in message
        assert run.call_args[0][0] == [exe, "--version"]


def test_range_parser():
    assert _parse_range("bytes=0-0", 10) == (0, 0)
    assert _parse_range("bytes=5-100", 10) == (5, 9)
    assert _parse_range("bytes=-4", 10) == (6, 9)
    for bad in ("bytes=-", "bytes=9-2", "bytes=10-", "items=0-1", "bytes=0-1,4-5", ""):
        assert _parse_range(bad, 10) is None


# ---------------------------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------------------------

class TestCleanup:
    def test_stops_cloudflared_and_server_and_forgets_url(self, exe, video):
        popen = FakePopen()
        p = provider(exe, popen)
        handle = p.prepare(video, "video/mp4")
        origin = local_url(handle)
        host, port = handle.session.server.server_address[:2]
        p.cleanup(handle)
        assert popen.processes[0].terminated
        assert handle.cleaned and handle.public_url is None and handle.session is None
        with pytest.raises(httpx.TransportError):  # refused (or timed out on Windows)
            httpx.get(origin, timeout=2)
        with socket.socket() as s:  # the port is free again
            s.bind((host, port))
        with pytest.raises(MediaStorageError, match="already cleaned up"):
            p.get_public_url(handle)
        p.cleanup(handle)  # idempotent
        p.cleanup(None)

    def test_source_video_untouched(self, exe, video):
        before = (video.read_bytes(), video.stat().st_mtime)
        p = provider(exe)
        handle = p.prepare(video, "video/mp4")
        httpx.get(local_url(handle))
        p.cleanup(handle)
        assert (video.read_bytes(), video.stat().st_mtime) == before

    def test_cleanup_failure_is_a_warning_not_an_error(self, exe, video, caplog):
        p = provider(exe)
        handle = p.prepare(video, "video/mp4")
        session = handle.session
        with patch.object(session.process, "terminate", side_effect=OSError("denied")), caplog.at_level(logging.WARNING):
            p.cleanup(handle)  # must not raise
        assert "Tunnel cleanup warning" in caplog.text
        session.server.server_close()

    def test_two_jobs_are_isolated(self, exe, video):
        p = provider(exe)
        a, b = p.prepare(video, "video/mp4"), p.prepare(video, "video/mp4")
        url_b = local_url(b)
        p.cleanup(a)  # job A done (or failed)
        assert httpx.get(url_b).status_code == 200  # job B's URL still works
        assert httpx.get(url_b.replace(b.public_url.rsplit("/", 1)[1], "x.mp4")).status_code == 404
        p.cleanup(b)


# ---------------------------------------------------------------------------------------------
# Settings, factory, AUTO routing, planning
# ---------------------------------------------------------------------------------------------

class TestRouting:
    def test_settings(self, exe, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "cloudflare_tunnel")
        assert StorageSettings.from_env().problems() == []
        assert isinstance(create_media_provider(), CloudflareTunnelMediaProvider)
        for key, value, needle in (("CLOUDFLARE_MEDIA_HOST", "0.0.0.0", "127.0.0.1"),
                                   ("CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS", "1", "5..300"),
                                   ("CLOUDFLARE_MEDIA_TOKEN_BYTES", "8", "16..64"),
                                   ("CLOUDFLARED_PATH", "Z:/missing/cloudflared.exe", "cloudflared not found")):
            with monkeypatch.context() as m:
                m.setenv(key, value)
                assert needle in " ".join(StorageSettings.from_env().problems())
                with pytest.raises(StorageNotConfiguredError):
                    create_media_provider()

    @pytest.mark.parametrize("size,expected", [(10_060_970, "tempfile"), (98_999_999, "tempfile"),
                                               (131_925_281, "cloudflare_tunnel"), (290_000_000, "cloudflare_tunnel")])
    def test_auto_prefers_tempfile_then_tunnel(self, exe, monkeypatch, size, expected):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        assert build_router(StorageSettings.from_env()).select(size).name == expected
        assert delivery_provider(size) == expected

    def test_auto_without_cloudflared_uses_s3_or_blocks(self, monkeypatch):
        from tests.unit.test_media_router import S3_ENV

        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")  # conftest: cloudflared missing
        with pytest.raises(StorageNotConfiguredError, match="cloudflared not found.*install cloudflared"):
            build_router(StorageSettings.from_env()).select(131_925_281)
        for k, v in S3_ENV.items():
            monkeypatch.setenv(k, v)
        assert build_router(StorageSettings.from_env()).select(131_925_281).name == "s3"

    def test_0x0_never_in_auto(self, exe, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        assert "0x0" not in [t.name for t in build_router(StorageSettings.from_env()).tiers]

    def test_description_explains_the_tunnel(self, exe, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        text = media_delivery_description(131_925_281)
        assert text.startswith("131.9 MB video -> Cloudflare Quick Tunnel")
        assert "nothing is uploaded" in text and "too large for TempFile.org" in text
        assert "trycloudflare.com" not in text

    def test_dry_run_plan_starts_nothing(self, env, exe, monkeypatch, tmp_path):  # noqa: F811
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        _db, _accounts, ids, _video = env
        big = tmp_path / "big.mp4"
        with open(big, "wb") as f:
            f.truncate(131_925_281)
        no_network = httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network")))
        with patch("src.media_storage.cloudflare_tunnel.subprocess.Popen", side_effect=AssertionError("cloudflared")), \
                patch("src.media_storage.cloudflare_tunnel.http.server.ThreadingHTTPServer",
                      side_effect=AssertionError("server")), \
                patch.object(TempFileMediaStorage, "prepare", side_effect=AssertionError("upload")):
            plan = engine(env, {"instagram": InstagramPublisher(client=no_network)}).plan_destinations(
                str(big), "c", [(ids["instagram"], {})])
        assert plan[0].ready
        notes = " ".join(plan[0].notes)
        assert "Cloudflare Quick Tunnel" in notes and "tunnel started only after you confirm" in notes

    def test_create_post_notice(self, exe, monkeypatch, capsys):
        from src.cli.publish_menu import _ask_options, _instagram_notice

        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        assert _ask_options("instagram", "instagram / noxivra_01", "c", size=131_925_281) == {}
        _instagram_notice(131_925_281, False)
        out = capsys.readouterr().out
        assert "stays on this computer" in out and "Quick Tunnel" in out and "PUBLIC third-party" not in out


# ---------------------------------------------------------------------------------------------
# Instagram through the real tunnel provider (mocked Graph API)
# ---------------------------------------------------------------------------------------------

def graph_ok():
    return (GraphProvider()  # media_publish first: routes match by prefix
            .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "REEL_1"}))
            .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C1"}))
            .add("GET", f"{IG}/C1", resp(200, {"status_code": "FINISHED"})))


class TestInstagram:
    def test_tunnel_alive_until_publish_then_closed(self, exe, video):
        popen = FakePopen()
        p = provider(exe, popen)
        graph = graph_ok()
        alive_at_publish = []
        original = graph.handler

        def watch(request):
            if request.url.path.endswith("/media_publish"):
                alive_at_publish.append(not popen.processes[0].terminated)
            return original(request)

        graph.handler = watch
        pub = InstagramPublisher(client=graph.client(), sleep=lambda s: None, poll_interval=0, media_provider=lambda: p)
        outcome = pub.publish(ctx(video), Progress())
        assert outcome.status == "published" and alive_at_publish == [True]
        assert popen.processes[0].terminated
        body = graph.calls("POST", f"{IG}/17841400000/media")[0].content.decode()
        assert "trycloudflare.com%2Fmedia%2F" in body
        assert "trycloudflare" not in str(outcome.state)  # never persisted

    def test_container_error_still_closes_tunnel(self, exe, video):
        popen = FakePopen()
        graph = (GraphProvider()
                 .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C1"}))
                 .add("GET", f"{IG}/C1", resp(200, {"status_code": "ERROR"})))
        pub = InstagramPublisher(client=graph.client(), sleep=lambda s: None, poll_interval=0,
                                 media_provider=lambda: provider(exe, popen))
        with pytest.raises(Exception):  # noqa: B017 - any failure
            pub.publish(ctx(video), Progress())
        assert popen.processes[0].terminated

    def test_ctrl_c_closes_tunnel(self, exe, video):
        popen = FakePopen()
        graph = GraphProvider()
        graph.handler = lambda request: (_ for _ in ()).throw(KeyboardInterrupt())
        pub = InstagramPublisher(client=graph.client(), sleep=lambda s: None, poll_interval=0,
                                 media_provider=lambda: provider(exe, popen))
        with pytest.raises(KeyboardInterrupt):
            pub.publish(ctx(video), Progress())
        assert popen.processes[0].terminated

    def test_cleanup_failure_after_publish_keeps_success(self, exe, video, caplog):
        p = provider(exe)
        pub = InstagramPublisher(client=graph_ok().client(), sleep=lambda s: None, poll_interval=0,
                                 media_provider=lambda: p)
        with patch("src.media_storage.cloudflare_tunnel.TunnelSession.close", side_effect=OSError("stuck")), \
                caplog.at_level(logging.WARNING):
            outcome = pub.publish(ctx(video), Progress())
        assert outcome.status == "published" and outcome.platform_media_id == "REEL_1"
        assert "Tunnel cleanup warning" in caplog.text

    def test_polling_window_grows_with_size(self, monkeypatch):
        pub = InstagramPublisher()
        assert pub.poll_attempts(10_000_000) == 5
        assert pub.poll_attempts(131_925_281) == 9          # 5 + ceil(81.9 / 25)
        assert pub.poll_attempts(290_000_000) == 15         # capped
        monkeypatch.setenv("INSTAGRAM_MAX_POLL_MINUTES", "7")
        assert pub.poll_attempts(290_000_000) == 7
        monkeypatch.setenv("INSTAGRAM_MAX_POLL_MINUTES", "1")
        assert pub.poll_attempts(290_000_000) == 5          # never below Meta's documented 5



def test_rate_limited_quick_tunnel_reports_the_real_error(exe, video):
    """Real case (2026-09-26): cloudflared prints an untagged 429 line and exits; its exit code arrives
    after the output closes. The error must say 429, not "did not start within 90s"."""

    class LateExit(FakeProcess):
        def __init__(self):
            super().__init__(["INF Requesting new quick Tunnel on trycloudflare.com...\n",
                              "quick tunnel provisioning failed with status 429\n"])
            self._done.set()  # output ends right away

        def wait(self, timeout=None):
            self.returncode = 1  # exit code only visible once waited for
            return 1

    popen = FakePopen()
    popen_call = popen.__call__

    def make(args, **kwargs):
        popen_call(args, **kwargs)
        process = LateExit()
        popen.processes[-1] = process
        return process

    with pytest.raises(MediaStorageError, match="exited before the tunnel was ready.*status 429") as e:
        provider(exe, make, startup_timeout=5).prepare(video, "video/mp4")
    assert e.value.retryable and "did not start within" not in str(e.value)
