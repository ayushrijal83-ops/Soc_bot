"""0x0.st temporary media provider: config, upload, URL validation, errors, cleanup, Instagram
integration, retries, dry-run, CLI. All HTTP mocked (httpx.MockTransport); nothing leaves the machine."""

import logging
from unittest.mock import patch

import httpx
import pytest

from src.media_storage import (
    MediaStorageError,
    StorageNotConfiguredError,
    StorageSettings,
    create_media_provider,
)
from src.media_storage.provider import MediaHandle, ObjectStorageMediaProvider
from src.media_storage.zerox0 import (
    USER_AGENT,
    ZeroX0MediaStorage,
    is_public_https_host,
)
from src.platforms.base import PublishError
from src.platforms.instagram.publisher import InstagramPublisher
from tests.unit.test_publishers import IG, Progress, ctx, resp
from tests.unit.test_publishers import Provider as GraphProvider

FILE_URL = "https://0x0.st/X9aBcDeFgHiJkL.mp4"
TOKEN = "MGMT_TOKEN_SECRET_123"


class FakeZeroX0:
    """Stands in for 0x0.st: upload (POST /), HEAD file, management POST <file URL>, GET / page."""

    def __init__(self, upload=None, head=200, delete=200, page=200, urls=None):
        self.upload = upload
        self.head, self.delete, self.page = head, delete, page
        self.urls = list(urls or [FILE_URL])
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "POST" and url.rstrip("/") == "https://0x0.st":
            if isinstance(self.upload, Exception):
                raise self.upload
            if isinstance(self.upload, httpx.Response):
                return self.upload
            body = self.urls.pop(0) if len(self.urls) > 1 else self.urls[0]
            return httpx.Response(200, text=body + "\n", headers={"X-Token": TOKEN})
        if request.method == "HEAD":
            if isinstance(self.head, Exception):
                raise self.head
            return httpx.Response(self.head)
        if request.method == "POST":  # management request on the file URL
            if isinstance(self.delete, Exception):
                raise self.delete
            return httpx.Response(self.delete)
        if request.method == "GET":
            return httpx.Response(self.page, text="THE NULL POINTER")
        raise AssertionError(f"unexpected {request.method} {url}")

    def provider(self, **kw):
        return ZeroX0MediaStorage(client=httpx.Client(transport=httpx.MockTransport(self.handler)), **kw)

    def uploads(self):
        return [r for r in self.requests if r.method == "POST" and str(r.url).rstrip("/") == "https://0x0.st"]

    def deletes(self):
        return [r for r in self.requests if r.method == "POST" and str(r.url) != "https://0x0.st"
                and str(r.url).rstrip("/") != "https://0x0.st"]


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "my private clip name.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 4000)
    return p


# ---------------------------------------------------------------------------------------------
# Configuration / selection
# ---------------------------------------------------------------------------------------------

S3_VARS = ("MEDIA_STORAGE_BUCKET", "MEDIA_STORAGE_ACCESS_KEY", "MEDIA_STORAGE_SECRET_KEY",
           "MEDIA_STORAGE_REGION", "MEDIA_STORAGE_ENDPOINT")


class TestConfig:
    def test_0x0_needs_no_s3_credentials(self, monkeypatch):
        for v in S3_VARS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "0x0")
        assert StorageSettings.from_env().problems() == []
        provider = create_media_provider()
        assert isinstance(provider, ZeroX0MediaStorage)
        assert provider.base_url == "https://0x0.st" and provider.expires_hours == 1

    def test_default_provider_is_s3_not_0x0(self, monkeypatch):
        monkeypatch.delenv("MEDIA_STORAGE_PROVIDER", raising=False)
        for v in S3_VARS:
            monkeypatch.delenv(v, raising=False)
        assert StorageSettings.from_env().provider == "s3"
        with pytest.raises(StorageNotConfiguredError):
            create_media_provider()  # never silently falls back to a public host

    def test_s3_selection_unchanged(self, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "s3")
        for k, v in {"MEDIA_STORAGE_BUCKET": "b", "MEDIA_STORAGE_ACCESS_KEY": "a", "MEDIA_STORAGE_SECRET_KEY": "s",
                     "MEDIA_STORAGE_REGION": "us-east-1", "MEDIA_STORAGE_ENDPOINT": ""}.items():
            monkeypatch.setenv(k, v)
        with patch("src.media_storage.s3.S3ObjectStorage.__init__", return_value=None):
            assert isinstance(create_media_provider(), ObjectStorageMediaProvider)

    @pytest.mark.parametrize("url", ["http://0x0.st", "https://localhost", "https://127.0.0.1", "https://10.0.0.5",
                                     "https://user:pw@0x0.st", "https://0x0.st/?x=1", "not a url"])
    def test_invalid_instance_url(self, monkeypatch, url):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "0x0")
        monkeypatch.setenv("MEDIA_STORAGE_0X0_URL", url)
        assert any("MEDIA_STORAGE_0X0_URL" in p for p in StorageSettings.from_env().problems())
        with pytest.raises(StorageNotConfiguredError):
            create_media_provider()

    @pytest.mark.parametrize("hours,ok", [("1", True), ("24", True), ("0", False), ("25", False), ("x", False)])
    def test_expiry_bounds(self, monkeypatch, hours, ok):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "0x0")
        monkeypatch.delenv("MEDIA_STORAGE_0X0_URL", raising=False)
        monkeypatch.setenv("MEDIA_STORAGE_0X0_EXPIRES_HOURS", hours)
        assert (StorageSettings.from_env().problems() == []) is ok

    def test_public_host_helper(self):
        assert is_public_https_host("https://0x0.st/abc.mp4")
        assert is_public_https_host("https://8.8.8.8/x")
        for bad in ("https://192.168.1.2/x", "https://[::1]/x", "https://printer.local/x", "ftp://0x0.st/x", "https:///x"):
            assert not is_public_https_host(bad)


# ---------------------------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------------------------

class TestUpload:
    def test_upload_request_and_handle(self, video):
        fake = FakeZeroX0()
        handle = fake.provider(expires_hours=2).prepare(video, "video/mp4")
        (upload,) = fake.uploads()
        assert upload.headers["User-Agent"] == USER_AGENT and "Mozilla" not in USER_AGENT
        assert upload.headers["Content-Type"].startswith("multipart/form-data")
        body = upload.content
        assert b'name="file"; filename="video.mp4"' in body
        assert b"my private clip name" not in body  # local filename never sent
        assert b'name="secret"' in body and b'name="expires"\r\n\r\n2\r\n' in body
        assert video.read_bytes() in body
        assert handle.public_url == FILE_URL and handle.token == TOKEN and handle.object_key == "X9aBcDeFgHiJkL.mp4"
        assert TOKEN not in repr(handle) and FILE_URL not in repr(handle)
        assert [r.method for r in fake.requests] == ["POST", "HEAD"]  # reachability confirmed

    @pytest.mark.parametrize("name,content,ctype,message", [
        ("missing.mp4", None, "video/mp4", "not found"),
        ("empty.mp4", b"", "video/mp4", "empty"),
        ("clip.avi", b"x", "video/x-msvideo", "Unsupported"),
    ])
    def test_local_validation(self, tmp_path, name, content, ctype, message):
        path = tmp_path / name
        if content is not None:
            path.write_bytes(content)
        fake = FakeZeroX0()
        with pytest.raises(MediaStorageError, match=message):
            fake.provider().prepare(path, ctype)
        assert fake.requests == []

    def test_size_limit(self, video):
        fake = FakeZeroX0()
        with patch("src.media_storage.zerox0.MAX_BYTES", 10), pytest.raises(MediaStorageError, match="512 MiB"):
            fake.provider().prepare(video, "video/mp4")
        assert fake.requests == []

    @pytest.mark.parametrize("body", ["", "error: something", "http://0x0.st/abc.mp4", "https://evil.example.com/a.mp4",
                                      "https://127.0.0.1/a.mp4", "https://0x0.st/"])
    def test_malformed_or_foreign_url_rejected(self, video, body):
        fake = FakeZeroX0(upload=httpx.Response(200, text=body, headers={"X-Token": TOKEN}))
        with pytest.raises(MediaStorageError, match="unexpected response"):
            fake.provider().prepare(video, "video/mp4")

    @pytest.mark.parametrize("status,retryable,text", [
        (418, False, "blocked"), (403, False, "blocked"), (413, False, "too large"),
        (500, True, "HTTP 500"), (502, True, "HTTP 502"), (429, True, "HTTP 429"), (400, False, "HTTP 400"),
    ])
    def test_http_errors(self, video, status, retryable, text):
        fake = FakeZeroX0(upload=httpx.Response(status, text=f"secret-ish body {TOKEN}"))
        with pytest.raises(MediaStorageError) as exc:
            fake.provider().prepare(video, "video/mp4")
        assert exc.value.retryable is retryable and text in str(exc.value)
        assert TOKEN not in str(exc.value)  # response bodies are not echoed

    @pytest.mark.parametrize("error,message", [(httpx.ReadTimeout("slow"), "timed out"),
                                               (httpx.ConnectError("refused"), "network error")])
    def test_timeout_and_connection_errors_retryable(self, video, error, message):
        with pytest.raises(MediaStorageError, match=message) as exc:
            FakeZeroX0(upload=error).provider().prepare(video, "video/mp4")
        assert exc.value.retryable

    def test_unreachable_upload_is_deleted_and_retryable(self, video):
        fake = FakeZeroX0(head=404)
        with pytest.raises(MediaStorageError, match="not reachable") as exc:
            fake.provider().prepare(video, "video/mp4")
        assert exc.value.retryable and len(fake.deletes()) == 1

    def test_missing_token_means_no_early_delete(self, video, caplog):
        fake = FakeZeroX0(upload=httpx.Response(200, text=FILE_URL))
        provider = fake.provider()
        with caplog.at_level(logging.INFO, logger="soc_bot"):
            handle = provider.prepare(video, "video/mp4")
            provider.cleanup(handle)
        assert fake.deletes() == [] and "no management token" in caplog.text


# ---------------------------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------------------------

class TestCleanup:
    def test_delete_request_and_idempotent(self, video):
        fake = FakeZeroX0()
        provider = fake.provider()
        handle = provider.prepare(video, "video/mp4")
        provider.cleanup(handle)
        provider.cleanup(handle)
        (delete,) = fake.deletes()
        assert str(delete.url) == FILE_URL and delete.headers["User-Agent"] == USER_AGENT
        assert f"token={TOKEN}".encode() in delete.content and b"delete=" in delete.content
        assert handle.token is None and video.exists()
        with pytest.raises(MediaStorageError, match="cleaned up"):
            provider.get_public_url(handle)

    @pytest.mark.parametrize("failure", [500, httpx.ConnectError("down")])
    def test_cleanup_failure_is_logged_not_raised(self, video, caplog, failure):
        fake = FakeZeroX0(delete=failure)
        provider = fake.provider()
        with caplog.at_level(logging.INFO, logger="soc_bot"):
            provider.cleanup(provider.prepare(video, "video/mp4"))
        assert "Could not delete temporary media" in caplog.text
        assert TOKEN not in caplog.text and FILE_URL not in caplog.text

    def test_nothing_secret_logged(self, video, caplog):
        provider = FakeZeroX0().provider()
        with caplog.at_level(logging.DEBUG):
            provider.cleanup(provider.prepare(video, "video/mp4"))
        for secret in (TOKEN, FILE_URL, "X9aBcDeFgHiJkL", str(video), "my private clip name"):
            assert secret not in caplog.text


# ---------------------------------------------------------------------------------------------
# Instagram integration (real ZeroX0 provider + mocked Graph API)
# ---------------------------------------------------------------------------------------------

def ig(graph, zerox0):
    provider = zerox0.provider()
    return InstagramPublisher(client=graph.client(), sleep=lambda s: None, poll_interval=0,
                              media_provider=lambda: provider)


def graph_happy(container="C1"):
    return (GraphProvider()
            .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "REEL_1"}))
            .add("POST", f"{IG}/17841400000/media", resp(200, {"id": container}))
            .add("GET", f"{IG}/{container}", resp(200, {"status_code": "IN_PROGRESS"}), resp(200, {"status_code": "FINISHED"})))


class TestInstagramWith0x0:
    def test_full_flow_and_cleanup_after_publish(self, video, caplog):
        graph, zx = graph_happy(), FakeZeroX0()
        with caplog.at_level(logging.DEBUG):
            outcome = ig(graph, zx).publish(ctx(video, {}), Progress())
        assert outcome.status == "published" and outcome.platform_media_id == "REEL_1"
        create = graph.calls("POST", f"{IG}/17841400000/media")[0]
        assert "0x0.st%2FX9aBcDeFgHiJkL.mp4" in create.content.decode()  # generated URL sent to Meta
        assert len(zx.deletes()) == 1  # deleted after media_publish
        assert FILE_URL not in caplog.text and TOKEN not in caplog.text
        assert "0x0.st" not in str(outcome.state) and video.exists()

    @pytest.mark.parametrize("case", ["container", "processing", "publish"])
    def test_cleanup_after_failures(self, video, case):
        graph = GraphProvider()
        if case == "container":
            graph.add("POST", f"{IG}/17841400000/media", resp(400, {"error": {"message": "Media download failed", "code": 9004}}))
        else:
            graph.add("POST", f"{IG}/17841400000/media_publish", resp(500, {"error": {"message": "x", "code": 2}}))
            graph.add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C1"}))
            graph.add("GET", f"{IG}/C1", resp(200, {"status_code": "ERROR" if case == "processing" else "FINISHED"}))
        zx = FakeZeroX0()
        with pytest.raises(PublishError) as exc:
            ig(graph, zx).publish(ctx(video, {}), Progress())
        assert len(zx.deletes()) == 1
        assert "0x0.st/X9a" not in str(exc.value) and TOKEN not in str(exc.value)

    def test_cleanup_after_ctrl_c(self, video):
        def interrupt(request):
            raise KeyboardInterrupt

        graph = GraphProvider().add("POST", f"{IG}/17841400000/media", interrupt)
        zx = FakeZeroX0()
        with pytest.raises(KeyboardInterrupt):
            ig(graph, zx).publish(ctx(video, {}), Progress())
        assert len(zx.deletes()) == 1

    def test_cleanup_failure_does_not_hide_successful_publish(self, video):
        graph, zx = graph_happy(), FakeZeroX0(delete=500)
        outcome = ig(graph, zx).publish(ctx(video, {}), Progress())
        assert outcome.status == "published"

    def test_cleanup_failure_does_not_replace_original_error(self, video):
        graph = GraphProvider().add("POST", f"{IG}/17841400000/media", resp(400, {"error": {"message": "bad caption", "code": 100}}))
        zx = FakeZeroX0(delete=httpx.ConnectError("down"))
        with pytest.raises(PublishError, match="bad caption"):
            ig(graph, zx).publish(ctx(video, {}), Progress())

    def test_retry_uploads_fresh_media_with_new_url(self, video):
        zx = FakeZeroX0(urls=["https://0x0.st/First1.mp4", "https://0x0.st/Second2.mp4"])
        provider = zx.provider()
        failing = (GraphProvider().add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C1"}))
                   .add("GET", f"{IG}/C1", resp(200, {"status_code": "ERROR"})))
        pub = InstagramPublisher(client=failing.client(), sleep=lambda s: None, poll_interval=0, media_provider=lambda: provider)
        with pytest.raises(PublishError):
            pub.publish(ctx(video, {}), Progress())
        retry = (GraphProvider().add("GET", f"{IG}/C1", resp(200, {"status_code": "ERROR"}))
                 .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "REEL_2"}))
                 .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C2"}))
                 .add("GET", f"{IG}/C2", resp(200, {"status_code": "FINISHED"})))
        pub2 = InstagramPublisher(client=retry.client(), sleep=lambda s: None, poll_interval=0, media_provider=lambda: provider)
        assert pub2.publish(ctx(video, {}, state={"container_id": "C1"}), Progress()).platform_media_id == "REEL_2"
        assert len(zx.uploads()) == 2
        second = retry.calls("POST", f"{IG}/17841400000/media")[0].content.decode()
        assert "Second2" in second and "First1" not in second  # old URL never reused
        assert len(zx.deletes()) == 2

    def test_storage_config_error_is_not_retried(self, video, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "0x0")
        monkeypatch.setenv("MEDIA_STORAGE_0X0_URL", "http://insecure.example.com")
        pub = InstagramPublisher(client=GraphProvider().client(), sleep=lambda s: None)
        with pytest.raises(PublishError) as exc:
            pub.publish(ctx(video, {}), Progress())
        assert not exc.value.retryable and "MEDIA_STORAGE_0X0_URL" in str(exc.value)


# ---------------------------------------------------------------------------------------------
# Dry run, settings check, CLI
# ---------------------------------------------------------------------------------------------

def test_dry_run_plan_mentions_public_host_and_calls_nothing(monkeypatch, video):
    from src.core.validation import MediaInfo

    monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "0x0")
    pub = InstagramPublisher(client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network"))))
    media = MediaInfo(str(video), 4000, "video/mp4")
    assert pub.validate("caption", {}, media) == []
    note = pub.delivery_notes({})[0]
    assert "0x0.st" in note and "PUBLIC" in note and "uploaded only when publishing" in note


class TestSettingsCheck:
    def run(self, monkeypatch, capsys, fake):
        from src.cli.content_menu import check_media_storage

        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "0x0")
        monkeypatch.delenv("MEDIA_STORAGE_0X0_URL", raising=False)
        monkeypatch.delenv("MEDIA_STORAGE_0X0_EXPIRES_HOURS", raising=False)
        with patch("src.media_storage.create_media_provider", lambda: fake.provider()):
            ok = check_media_storage()
        return ok, capsys.readouterr().out

    def test_reachable_but_honest(self, monkeypatch, capsys):
        fake = FakeZeroX0()
        ok, out = self.run(monkeypatch, capsys, fake)
        assert ok and "PUBLIC third-party" in out and "cannot confirm that uploads will be accepted" in out
        assert fake.uploads() == [] and [r.method for r in fake.requests] == ["GET"]

    def test_blocked_client(self, monkeypatch, capsys):
        ok, out = self.run(monkeypatch, capsys, FakeZeroX0(page=418))
        assert not ok and "refuses this client" in out


def test_create_post_warns_about_public_host(monkeypatch, capsys):
    from src.cli.publish_menu import _ask_options, _instagram_notice

    monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "0x0")
    with patch("builtins.input", side_effect=AssertionError("must not prompt for a URL")):
        assert _ask_options("instagram", "instagram / noxivra_01", "caption") == {}
    _instagram_notice(10_000_000, False)
    out = capsys.readouterr().out
    assert "0x0.st" in out and "PUBLIC" in out


def test_handle_repr_hides_secrets():
    h = MediaHandle(source_path=__import__("pathlib").Path("x.mp4"), content_type="video/mp4",
                    public_url=FILE_URL, token=TOKEN)
    assert TOKEN not in repr(h) and FILE_URL not in repr(h)
