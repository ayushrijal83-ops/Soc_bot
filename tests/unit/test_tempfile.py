"""TempFile.org temporary media provider. All HTTP mocked (httpx.MockTransport); nothing leaves the machine."""

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
from src.media_storage.tempfile import MAX_BYTES, USER_AGENT, TempFileMediaStorage
from src.platforms.base import PublishError
from src.platforms.instagram.publisher import InstagramPublisher
from tests.unit.test_publishers import IG, Progress, ctx, resp
from tests.unit.test_publishers import Provider as GraphProvider

FID = "kN8mP2xQvR7"          # valid 11-char Base58 id (the id is also the delete capability)
MEDIA_URL = f"https://tempfile.org/{FID}/download"


def upload_ok(fid=FID, url=None, **extra):
    return httpx.Response(200, json={"success": True, "files": [
        {"id": fid, "name": "video.mp4", "size": 4000, "url": url or f"https://tempfile.org/{fid}/",
         "expiryTime": 1790350780000, **extra}], "message": "1 file(s) uploaded successfully"})


class FakeTempFile:
    def __init__(self, upload=None, head=None, delete=200, page=200, ids=None):
        self.upload, self.head, self.delete, self.page = upload, head, delete, page
        self.ids = list(ids or [FID])
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/api/upload/local":
            if isinstance(self.upload, Exception):
                raise self.upload
            if isinstance(self.upload, httpx.Response):
                return self.upload
            fid = self.ids.pop(0) if len(self.ids) > 1 else self.ids[0]
            return upload_ok(fid)
        if request.method == "HEAD":
            if isinstance(self.head, Exception):
                raise self.head
            return self.head or httpx.Response(200, headers={"content-type": "video/mp4", "content-length": "4000"})
        if request.method == "DELETE":
            if isinstance(self.delete, Exception):
                raise self.delete
            return httpx.Response(self.delete, json={"success": self.delete == 200})
        if request.method == "GET" and path == "/openapi.json":
            return httpx.Response(self.page, text='{"paths": {"/upload/local": {}}}')
        raise AssertionError(f"unexpected {request.method} {request.url}")

    def provider(self, **kw):
        return TempFileMediaStorage(client=httpx.Client(transport=httpx.MockTransport(self.handler)), **kw)

    def of(self, method):
        return [r for r in self.requests if r.method == method]


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "test_youtub.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 3988)
    assert p.stat().st_size == 4000
    return p


# ---------------------------------------------------------------------------------------------
# Upload + response parsing
# ---------------------------------------------------------------------------------------------

class TestUpload:
    def test_successful_upload_and_parsing(self, video):
        fake = FakeTempFile()
        handle = fake.provider(expiry_hours=6).prepare(video, "video/mp4")
        (up,) = fake.of("POST")
        assert str(up.url) == "https://tempfile.org/api/upload/local"
        assert up.headers["User-Agent"] == USER_AGENT
        assert up.headers["Content-Type"].startswith("multipart/form-data")
        assert b'name="files"; filename="video.mp4"' in up.content
        assert b"test_youtub" not in up.content  # filename privacy
        assert b'name="expiryHours"\r\n\r\n6\r\n' in up.content
        assert video.read_bytes() in up.content
        # the media URL is the direct download endpoint built from the id, not the HTML landing url
        assert handle.public_url == MEDIA_URL and handle.token == FID
        assert FID not in repr(handle) and MEDIA_URL not in repr(handle) and handle.object_key == ""
        (head,) = fake.of("HEAD")
        assert str(head.url) == MEDIA_URL

    def test_streamed_upload_not_read_whole(self, video):
        """The file object handed to httpx is read in chunks by its multipart encoder, never .read() whole."""
        fake = FakeTempFile()
        real_open = open
        reads = []

        class Spy:
            def __init__(self, f):
                self.f = f

            def read(self, n=-1):
                reads.append(n)
                return self.f.read(n)

            def __getattr__(self, name):
                return getattr(self.f, name)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

        def spy_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            return Spy(f) if "b" in mode and str(path) == str(video) else f

        with patch("builtins.open", spy_open):
            fake.provider().prepare(video, "video/mp4")
        assert reads and all(n is not None and n > 0 for n in reads)  # chunked reads only

    @pytest.mark.parametrize("response,message", [
        (httpx.Response(200, text="<html>oops</html>"), "HTTP 200"),
        (httpx.Response(200, json={"success": True, "files": []}), "no valid file id"),
        (httpx.Response(200, json={"success": True, "files": [{"url": "https://tempfile.org/x/"}]}), "no valid file id"),
        (httpx.Response(200, json={"success": True, "files": [{"id": "../../etc/passwd"}]}), "no valid file id"),
        (httpx.Response(200, json={"success": True, "files": [{"id": "0OIl0OIl0OI"}]}), "no valid file id"),  # not Base58
        (httpx.Response(200, json={"success": False, "error": "nope"}), "rejected: nope"),
        (httpx.Response(200, json=["not", "an", "object"]), "HTTP 200"),
    ])
    def test_malformed_or_missing(self, video, response, message):
        with pytest.raises(MediaStorageError, match=message):
            FakeTempFile(upload=response).provider().prepare(video, "video/mp4")

    @pytest.mark.parametrize("landing", ["http://tempfile.org/x/", "https://evil.example.com/x/", "https://127.0.0.1/x/",
                                         "https://localhost/x/"])
    def test_foreign_or_insecure_url_rejected(self, video, landing):
        fake = FakeTempFile(upload=upload_ok(url=landing))
        with pytest.raises(MediaStorageError, match="different host"):
            fake.provider().prepare(video, "video/mp4")
        assert fake.of("HEAD") == []  # nothing handed on

    @pytest.mark.parametrize("status,retryable,text", [
        (429, True, "rate limited"), (500, True, "HTTP 500"), (503, True, "HTTP 503"),
        (403, False, "denied from this IP"), (413, False, "too large"), (400, False, "HTTP 400"),
    ])
    def test_http_errors(self, video, status, retryable, text):
        body = {"success": False, "error": f"provider says {FID} Bearer abcdefghijklmnop"}
        headers = {"X-RateLimit-Reset": "1790350780"} if status == 429 else {}
        with pytest.raises(MediaStorageError) as exc:
            FakeTempFile(upload=httpx.Response(status, json=body, headers=headers)).provider().prepare(video, "video/mp4")
        assert exc.value.retryable is retryable and text in str(exc.value)
        assert "abcdefghijklmnop" not in str(exc.value)  # provider text is redacted
        if status == 429:
            assert "1790350780" in str(exc.value)

    @pytest.mark.parametrize("error,message", [(httpx.ReadTimeout("slow"), "timed out"),
                                               (httpx.ConnectError("refused"), "network error")])
    def test_timeout_and_connection_errors(self, video, error, message):
        with pytest.raises(MediaStorageError, match=message) as exc:
            FakeTempFile(upload=error).provider().prepare(video, "video/mp4")
        assert exc.value.retryable

    def test_oversized_file_rejected_before_network(self, video):
        fake = FakeTempFile()
        with patch("src.media_storage.tempfile.MAX_BYTES", 100), pytest.raises(MediaStorageError, match="accepts files up to"):
            fake.provider().prepare(video, "video/mp4")
        assert fake.requests == [] and MAX_BYTES == 100_000_000

    @pytest.mark.parametrize("name,content,ctype,message", [
        ("missing.mp4", None, "video/mp4", "not found"),
        ("empty.mp4", b"", "video/mp4", "empty"),
        ("clip.avi", b"x", "video/x-msvideo", "Unsupported"),
    ])
    def test_local_validation(self, tmp_path, name, content, ctype, message):
        path = tmp_path / name
        if content is not None:
            path.write_bytes(content)
        fake = FakeTempFile()
        with pytest.raises(MediaStorageError, match=message):
            fake.provider().prepare(path, ctype)
        assert fake.requests == []

    @pytest.mark.parametrize("head", [httpx.Response(404), httpx.Response(200, headers={"content-length": "12"}),
                                      httpx.ConnectError("x")])
    def test_unreachable_or_wrong_size_upload_is_deleted(self, video, head):
        fake = FakeTempFile(head=head)
        with pytest.raises(MediaStorageError, match="not reachable") as exc:
            fake.provider().prepare(video, "video/mp4")
        assert exc.value.retryable and len(fake.of("DELETE")) == 1

    def test_non_video_content_type_is_warned(self, video, caplog):
        fake = FakeTempFile(head=httpx.Response(200, headers={"content-type": "application/octet-stream",
                                                                 "content-length": "4000"}))
        with caplog.at_level(logging.WARNING, logger="soc_bot"):
            fake.provider().prepare(video, "video/mp4")
        assert "application/octet-stream" in caplog.text


# ---------------------------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------------------------

class TestCleanup:
    def test_delete_and_idempotent(self, video):
        fake = FakeTempFile()
        provider = fake.provider()
        handle = provider.prepare(video, "video/mp4")
        provider.cleanup(handle)
        provider.cleanup(handle)
        (d,) = fake.of("DELETE")
        assert str(d.url) == f"https://tempfile.org/api/file/{FID}"
        assert handle.token is None and video.exists()
        with pytest.raises(MediaStorageError, match="cleaned up"):
            provider.get_public_url(handle)

    def test_already_expired_counts_as_deleted(self, video, caplog):
        provider = FakeTempFile(delete=404).provider()
        with caplog.at_level(logging.INFO, logger="soc_bot"):
            provider.cleanup(provider.prepare(video, "video/mp4"))
        assert "Temporary media deleted" in caplog.text

    @pytest.mark.parametrize("failure", [500, httpx.ConnectError("down")])
    def test_cleanup_failure_logged_not_raised(self, video, caplog, failure):
        provider = FakeTempFile(delete=failure).provider()
        with caplog.at_level(logging.INFO, logger="soc_bot"):
            provider.cleanup(provider.prepare(video, "video/mp4"))
        assert "Could not delete temporary media" in caplog.text

    def test_nothing_secret_logged(self, video, caplog):
        provider = FakeTempFile().provider()
        with caplog.at_level(logging.DEBUG):
            provider.cleanup(provider.prepare(video, "video/mp4"))
        for secret in (FID, MEDIA_URL, str(video), "test_youtub"):
            assert secret not in caplog.text


# ---------------------------------------------------------------------------------------------
# Factory / settings / health / dry-run / CLI
# ---------------------------------------------------------------------------------------------

S3_VARS = ("MEDIA_STORAGE_BUCKET", "MEDIA_STORAGE_ACCESS_KEY", "MEDIA_STORAGE_SECRET_KEY",
           "MEDIA_STORAGE_REGION", "MEDIA_STORAGE_ENDPOINT")


class TestConfig:
    def test_factory_selects_tempfile_without_credentials(self, monkeypatch):
        for v in S3_VARS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "tempfile")
        monkeypatch.delenv("MEDIA_STORAGE_TEMPFILE_EXPIRY_HOURS", raising=False)
        assert StorageSettings.from_env().problems() == []
        provider = create_media_provider()
        assert isinstance(provider, TempFileMediaStorage) and provider.expiry_hours == 1

    @pytest.mark.parametrize("hours,ok", [("1", True), ("6", True), ("24", True), ("48", True),
                                          ("2", False), ("0", False), ("72", False), ("x", False)])
    def test_expiry_must_be_an_allowed_value(self, monkeypatch, hours, ok):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "tempfile")
        monkeypatch.setenv("MEDIA_STORAGE_TEMPFILE_EXPIRY_HOURS", hours)
        assert (StorageSettings.from_env().problems() == []) is ok
        if not ok:
            with pytest.raises(StorageNotConfiguredError):
                create_media_provider()

    def test_health_check_uploads_nothing(self):
        fake = FakeTempFile()
        message = fake.provider().health_check()
        assert "service reachable" in message and "uploads nothing" in message
        assert [r.method for r in fake.requests] == ["GET"]

    @pytest.mark.parametrize("page,text", [(403, "denies access"), (500, "unavailable")])
    def test_health_check_failures(self, page, text):
        with pytest.raises(MediaStorageError, match=text):
            FakeTempFile(page=page).provider().health_check()

    def test_settings_screen(self, monkeypatch, capsys):
        from src.cli.content_menu import check_media_storage

        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "tempfile")
        monkeypatch.delenv("MEDIA_STORAGE_TEMPFILE_EXPIRY_HOURS", raising=False)
        fake = FakeTempFile()
        with patch("src.media_storage.create_media_provider", lambda: fake.provider()):
            assert check_media_storage() is True
        out = capsys.readouterr().out
        assert "TempFile.org" in out and "PUBLIC" in out and "no credentials required" in out
        assert "service reachable" in out and fake.of("POST") == []

    def test_dry_run_and_validation_make_no_calls(self, monkeypatch, video):
        from src.core.validation import MediaInfo

        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "tempfile")
        pub = InstagramPublisher(client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network"))))
        assert pub.validate("caption", {}, MediaInfo(str(video), 4000, "video/mp4")) == []
        note = pub.delivery_notes({})[0]
        assert note == ("media delivery: temporary PUBLIC upload to TempFile.org (third-party host; the video "
                        "leaves this computer) (uploaded only when publishing)")

    def test_create_post_warns_and_never_asks_for_url(self, monkeypatch, capsys):
        from src.cli.publish_menu import _ask_options

        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "tempfile")
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            assert _ask_options("instagram", "instagram / noxivra_01", "c") == {}
        out = capsys.readouterr().out
        assert "TempFile.org" in out and "PUBLIC" in out and "may be able to download" in out


# ---------------------------------------------------------------------------------------------
# Instagram integration (real TempFile provider + mocked Graph API)
# ---------------------------------------------------------------------------------------------

def ig(graph, fake):
    provider = fake.provider()
    return InstagramPublisher(client=graph.client(), sleep=lambda s: None, poll_interval=0,
                              media_provider=lambda: provider)


class TestInstagramWithTempFile:
    def test_publish_sends_download_url_then_deletes(self, video, caplog):
        graph = (GraphProvider()
                 .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "REEL_9"}))
                 .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C1"}))
                 .add("GET", f"{IG}/C1", resp(200, {"status_code": "FINISHED"})))
        fake = FakeTempFile()
        with caplog.at_level(logging.DEBUG):
            outcome = ig(graph, fake).publish(ctx(video, {}), Progress())
        assert outcome.platform_media_id == "REEL_9"
        body = graph.calls("POST", f"{IG}/17841400000/media")[0].content.decode()
        assert "tempfile.org%2FkN8mP2xQvR7%2Fdownload" in body
        assert len(fake.of("DELETE")) == 1
        assert FID not in caplog.text and FID not in str(outcome.state)

    def test_failure_still_cleans_up_and_retry_uploads_fresh(self, video):
        fake = FakeTempFile(ids=["AAAAAAAAAAA", "BBBBBBBBBBB"])
        provider = fake.provider()
        first = (GraphProvider().add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C1"}))
                 .add("GET", f"{IG}/C1", resp(200, {"status_code": "ERROR"})))
        with pytest.raises(PublishError):
            InstagramPublisher(client=first.client(), sleep=lambda s: None, poll_interval=0,
                               media_provider=lambda: provider).publish(ctx(video, {}), Progress())
        assert len(fake.of("DELETE")) == 1
        second = (GraphProvider().add("GET", f"{IG}/C1", resp(200, {"status_code": "ERROR"}))
                  .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "REEL_2"}))
                  .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C2"}))
                  .add("GET", f"{IG}/C2", resp(200, {"status_code": "FINISHED"})))
        InstagramPublisher(client=second.client(), sleep=lambda s: None, poll_interval=0,
                           media_provider=lambda: provider).publish(ctx(video, {}, state={"container_id": "C1"}), Progress())
        body = second.calls("POST", f"{IG}/17841400000/media")[0].content.decode()
        assert "BBBBBBBBBBB" in body and "AAAAAAAAAAA" not in body  # fresh upload, stale URL never reused
        assert len(fake.of("POST")) == 2 and len(fake.of("DELETE")) == 2

    def test_resume_of_finished_container_uploads_nothing(self, video):
        graph = (GraphProvider().add("GET", f"{IG}/C1", resp(200, {"status_code": "FINISHED"}))
                 .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "REEL_1"})))
        fake = FakeTempFile()
        ig(graph, fake).publish(ctx(video, {}, state={"container_id": "C1"}), Progress())
        assert fake.requests == []
