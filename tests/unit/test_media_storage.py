"""Temporary media delivery for Instagram: storage abstraction, S3 backend (fake client), provider
lifecycle, Instagram integration, retries, dry-run, CLI. No network, no real uploads."""

import logging
import os
from unittest.mock import patch

import httpx
import pytest

from src.core.jobs import JobStore
from src.core.validation import MediaInfo
from src.media_storage import (
    MediaStorageError,
    ObjectStorageMediaProvider,
    StorageNotConfiguredError,
    StorageSettings,
    create_media_provider,
    create_media_storage,
)
from src.media_storage.s3 import S3ObjectStorage
from src.media_storage.service import new_object_key
from src.platforms.base import PublishError, redact
from src.platforms.instagram.publisher import InstagramPublisher
from tests.unit.test_publishers import IG, Progress, Provider, ctx, resp

SIGNED = "https://bucket.s3.example.com/instagram/temp/k/video.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIDSECRET%2F20260925&X-Amz-Signature=deadbeef"
STORAGE_ENV = {
    "MEDIA_STORAGE_PROVIDER": "s3", "MEDIA_STORAGE_BUCKET": "soc-bot-temp", "MEDIA_STORAGE_REGION": "us-east-1",
    "MEDIA_STORAGE_ENDPOINT": "", "MEDIA_STORAGE_ACCESS_KEY": "AKIDSECRET", "MEDIA_STORAGE_SECRET_KEY": "SUPERSECRETKEY",
    "MEDIA_STORAGE_PRESIGNED_URL_TTL": "900", "MEDIA_STORAGE_DELETE_AFTER_PUBLISH": "true",
}


class FakeS3Client:
    """Records boto3-style calls; stores uploaded bytes in memory."""

    def __init__(self, url=SIGNED, fail=None):
        self.url, self.fail = url, fail or {}
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple] = []

    def _maybe_fail(self, op):
        if op in self.fail:
            raise self.fail[op]

    def upload_file(self, filename, bucket, key, ExtraArgs=None, Config=None):
        self.calls.append(("upload_file", bucket, key, ExtraArgs))
        self._maybe_fail("upload_file")
        with open(filename, "rb") as f:
            self.objects[key] = f.read()

    def generate_presigned_url(self, op, Params=None, ExpiresIn=None):
        self.calls.append(("presign", op, Params, ExpiresIn))
        self._maybe_fail("presign")
        return self.url

    def delete_object(self, Bucket=None, Key=None):
        self.calls.append(("delete_object", Bucket, Key))
        self._maybe_fail("delete_object")
        self.objects.pop(Key, None)

    def head_bucket(self, Bucket=None):
        self.calls.append(("head_bucket", Bucket))
        self._maybe_fail("head_bucket")


class ClientError(Exception):
    def __init__(self, code, status):
        super().__init__(f"An error occurred ({code}) https://bucket.s3.amazonaws.com/?X-Amz-Signature=LEAK")
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 2000)
    return path


def s3(client=None):
    return S3ObjectStorage("soc-bot-temp", "AKIDSECRET", "SUPERSECRETKEY", region="us-east-1", client=client or FakeS3Client())


# ---------------------------------------------------------------------------------------------
# Settings / factory
# ---------------------------------------------------------------------------------------------

class TestSettings:
    def test_missing_configuration_is_reported_without_secrets(self, monkeypatch):
        for k in STORAGE_ENV:
            monkeypatch.delenv(k, raising=False)
        problems = StorageSettings.from_env().problems()
        assert any("MEDIA_STORAGE_BUCKET" in p and "MEDIA_STORAGE_ACCESS_KEY" in p for p in problems)
        with pytest.raises(StorageNotConfiguredError, match="not configured"):
            create_media_storage()

    def test_valid_aws_and_r2_configs(self, monkeypatch):
        for k, v in STORAGE_ENV.items():
            monkeypatch.setenv(k, v)
        assert StorageSettings.from_env().configured
        monkeypatch.setenv("MEDIA_STORAGE_REGION", "")
        monkeypatch.setenv("MEDIA_STORAGE_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
        settings = StorageSettings.from_env()
        assert settings.configured and settings.ttl == 900 and settings.delete_after_publish

    @pytest.mark.parametrize("endpoint,message", [
        ("http://minio.local:9000", "https"),
        ("https://user:pw@r2.example.com", "credentials"),
    ])
    def test_bad_endpoint(self, monkeypatch, endpoint, message):
        for k, v in STORAGE_ENV.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("MEDIA_STORAGE_ENDPOINT", endpoint)
        assert any(message in p for p in StorageSettings.from_env().problems())

    def test_ttl_bounds_and_unsupported_provider(self, monkeypatch):
        for k, v in STORAGE_ENV.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("MEDIA_STORAGE_PRESIGNED_URL_TTL", "10")
        problems = " ".join(StorageSettings.from_env().problems())
        assert "TTL" in problems
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "gdrive")
        problems = " ".join(StorageSettings.from_env().problems())
        assert "not supported" in problems and "auto, tempfile, cloudflare_tunnel, s3 or 0x0" in problems
        assert "SUPERSECRETKEY" not in problems and "AKIDSECRET" not in problems

    def test_factory_builds_provider_from_env(self, monkeypatch):
        for k, v in STORAGE_ENV.items():
            monkeypatch.setenv(k, v)
        with patch("src.media_storage.s3.S3ObjectStorage.__init__", return_value=None):
            provider = create_media_provider()
        assert isinstance(provider, ObjectStorageMediaProvider) and provider.ttl == 900


# ---------------------------------------------------------------------------------------------
# S3 backend + keys
# ---------------------------------------------------------------------------------------------

class TestS3Backend:
    def test_upload_presign_delete(self, video):
        client = FakeS3Client()
        store = s3(client)
        obj = store.upload_file(video, "instagram/temp/abc/video.mp4", "video/mp4")
        assert obj.size == video.stat().st_size and client.objects["instagram/temp/abc/video.mp4"] == video.read_bytes()
        assert client.calls[0][3] == {"ContentType": "video/mp4"}  # no ACL: bucket stays private
        url = store.create_presigned_url("instagram/temp/abc/video.mp4", 900)
        assert url.startswith("https://")
        assert client.calls[1] == ("presign", "get_object", {"Bucket": "soc-bot-temp", "Key": "instagram/temp/abc/video.mp4"}, 900)
        store.delete_object("instagram/temp/abc/video.mp4")
        assert client.objects == {} and video.exists()  # local source untouched

    def test_non_https_presigned_url_rejected(self):
        with pytest.raises(MediaStorageError, match="HTTPS"):
            s3(FakeS3Client(url="http://minio.local/x?X-Amz-Signature=1")).create_presigned_url("k", 900)

    def test_ttl_is_clamped_to_s3_maximum(self):
        client = FakeS3Client()
        s3(client).create_presigned_url("k", 10**9)
        assert client.calls[-1][3] == 7 * 24 * 3600

    @pytest.mark.parametrize("op,method,args", [
        ("upload_file", "upload_file", None),
        ("presign", "create_presigned_url", ("k", 900)),
        ("delete_object", "delete_object", ("k",)),
        ("head_bucket", "health_check", ()),
    ])
    def test_errors_are_safe(self, video, op, method, args):
        store = s3(FakeS3Client(fail={op: ClientError("AccessDenied", 403)}))
        with pytest.raises(MediaStorageError) as exc:
            getattr(store, method)(*(args if args is not None else (video, "k", "video/mp4")))
        text = str(exc.value)
        assert "AccessDenied" in text and "403" in text and exc.value.retryable is False
        for secret in ("LEAK", "X-Amz", "AKIDSECRET", "SUPERSECRETKEY"):
            assert secret not in text

    def test_unreadable_local_file(self, tmp_path):
        with pytest.raises(MediaStorageError, match="unreadable"):
            s3().upload_file(tmp_path / "missing.mp4", "k", "video/mp4")

    def test_object_keys_are_unique_and_leak_nothing(self):
        keys = {new_object_key("video/mp4") for _ in range(200)}
        assert len(keys) == 200
        key = keys.pop()
        assert key.startswith("instagram/temp/") and key.endswith("/video.mp4")
        assert new_object_key("video/quicktime").endswith(".mov")


# ---------------------------------------------------------------------------------------------
# Provider lifecycle
# ---------------------------------------------------------------------------------------------

class TestProvider:
    def test_prepare_url_cleanup_idempotent(self, video):
        client = FakeS3Client()
        provider = ObjectStorageMediaProvider(s3(client), ttl=900)
        handle = provider.prepare(video, "video/mp4")
        assert handle.object_key in client.objects
        assert provider.get_public_url(handle) == SIGNED
        provider.cleanup(handle)
        provider.cleanup(handle)
        assert [c[0] for c in client.calls].count("delete_object") == 1
        assert video.exists()
        with pytest.raises(MediaStorageError, match="already cleaned"):
            provider.get_public_url(handle)  # expired/cleaned media is never re-used

    def test_missing_file(self, tmp_path):
        with pytest.raises(MediaStorageError, match="not found"):
            ObjectStorageMediaProvider(s3(), 900).prepare(tmp_path / "nope.mp4", "video/mp4")

    def test_keep_objects_when_configured(self, video):
        client = FakeS3Client()
        provider = ObjectStorageMediaProvider(s3(client), 900, delete_after=False)
        provider.cleanup(provider.prepare(video, "video/mp4"))
        assert len(client.objects) == 1

    def test_delete_failure_is_logged_not_raised(self, video, caplog):
        client = FakeS3Client(fail={"delete_object": ClientError("InternalError", 500)})
        provider = ObjectStorageMediaProvider(s3(client), 900)
        with caplog.at_level(logging.INFO, logger="soc_bot"):
            provider.cleanup(provider.prepare(video, "video/mp4"))
            provider.get_public_url  # noqa: B018
        assert "Could not delete temporary media" in caplog.text

    def test_signed_urls_and_keys_never_logged(self, video, caplog):
        provider = ObjectStorageMediaProvider(s3(), 900)
        with caplog.at_level(logging.DEBUG):
            handle = provider.prepare(video, "video/mp4")
            provider.get_public_url(handle)
            provider.cleanup(handle)
        for secret in ("X-Amz", SIGNED, "AKIDSECRET", "SUPERSECRETKEY", str(video)):
            assert secret not in caplog.text


def test_redact_removes_presigned_urls():
    text = redact(f"Instagram error: could not fetch {SIGNED} (code 9004)")
    assert "X-Amz" not in text and "deadbeef" not in text and "code 9004" in text


# ---------------------------------------------------------------------------------------------
# Instagram publisher integration
# ---------------------------------------------------------------------------------------------

class RecordingProvider:
    """Fake MediaSourceProvider recording the lifecycle."""

    name = "fake"

    def __init__(self, fail_prepare=None):
        self.events: list[str] = []
        self.fail_prepare = fail_prepare
        self.count = 0

    def prepare(self, path, content_type):
        self.count += 1
        self.events.append(f"prepare:{os.path.basename(str(path))}:{content_type}")
        if self.fail_prepare:
            raise self.fail_prepare
        return f"handle{self.count}"

    def get_public_url(self, handle):
        self.events.append(f"url:{handle}")
        return f"https://temp.example.com/{handle}.mp4?X-Amz-Signature=SIG{handle}"

    def cleanup(self, handle):
        self.events.append(f"cleanup:{handle}")


def ig_pub(provider_http, media, **kw):
    return InstagramPublisher(client=provider_http.client(), sleep=lambda s: None, poll_interval=0,
                              media_provider=lambda: media, **kw)


def ig_ctx(video, state=None):
    return ctx(video, {}, state=state)


class TestInstagramMediaDelivery:
    def happy(self):
        return (Provider()
                .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "MEDIA_1"}))
                .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C1"}))
                .add("GET", f"{IG}/C1", resp(200, {"status_code": "IN_PROGRESS"}), resp(200, {"status_code": "FINISHED"})))

    def test_local_file_goes_through_temporary_media(self, video):
        http, media = self.happy(), RecordingProvider()
        progress = Progress()
        outcome = ig_pub(http, media).publish(ig_ctx(video), progress)
        assert outcome.status == "published" and outcome.platform_media_id == "MEDIA_1"
        create = http.calls("POST", f"{IG}/17841400000/media")[0]
        assert "temp.example.com%2Fhandle1.mp4" in create.content.decode()  # generated URL sent to Meta
        assert media.events == ["prepare:clip.mp4:video/mp4", "url:handle1", "cleanup:handle1"]
        # the object stayed alive through polling + publish: cleanup is after the last Graph call
        assert len(http.calls("GET", f"{IG}/C1")) == 2 and http.calls("POST", f"{IG}/17841400000/media_publish")
        # URL never persisted in provider state
        assert all("temp.example.com" not in str(state) for _, state in progress.events)
        assert "temp.example.com" not in str(outcome.state)
        assert video.exists()

    @pytest.mark.parametrize("setup", ["container_fail", "processing_error", "publish_fail", "timeout"])
    def test_cleanup_on_every_failure(self, video, setup):
        http = Provider()
        if setup == "container_fail":
            http.add("POST", f"{IG}/17841400000/media", resp(400, {"error": {"message": "bad", "code": 100}}))
        else:
            http.add("POST", f"{IG}/17841400000/media_publish",
                     httpx.ReadTimeout("slow") if setup == "timeout" else resp(500, {"error": {"message": "x", "code": 2}}))
            http.add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C1"}))
            http.add("GET", f"{IG}/C1", resp(200, {"status_code": "ERROR" if setup == "processing_error" else "FINISHED"}))
        media = RecordingProvider()
        with pytest.raises(PublishError) as exc:
            ig_pub(http, media).publish(ig_ctx(video), Progress())
        assert media.events[-1] == "cleanup:handle1"
        assert "SIG" not in str(exc.value) and "temp.example.com" not in str(exc.value)
        assert video.exists()

    def test_cleanup_on_keyboard_interrupt(self, video):
        def interrupted(request):
            raise KeyboardInterrupt  # Ctrl+C while Meta is being called

        http = Provider().add("POST", f"{IG}/17841400000/media", interrupted)
        media = RecordingProvider()
        with pytest.raises(KeyboardInterrupt):
            ig_pub(http, media).publish(ig_ctx(video), Progress())
        assert media.events[-1] == "cleanup:handle1"

    def test_retry_uploads_fresh_media_never_reuses_url(self, video):
        media = RecordingProvider()
        first = (Provider()
                 .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C1"}))
                 .add("GET", f"{IG}/C1", resp(200, {"status_code": "ERROR"})))
        with pytest.raises(PublishError):
            ig_pub(first, media).publish(ig_ctx(video), Progress())
        # Next attempt: the saved container is ERROR -> new container with NEW temporary media
        second = (Provider()
                  .add("GET", f"{IG}/C1", resp(200, {"status_code": "ERROR"}))
                  .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "M2"}))
                  .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C2"}))
                  .add("GET", f"{IG}/C2", resp(200, {"status_code": "FINISHED"})))
        outcome = ig_pub(second, media).publish(ig_ctx(video, state={"container_id": "C1"}), Progress())
        assert outcome.platform_media_id == "M2"
        assert media.events == ["prepare:clip.mp4:video/mp4", "url:handle1", "cleanup:handle1",
                                "prepare:clip.mp4:video/mp4", "url:handle2", "cleanup:handle2"]

    def test_resume_of_finished_container_needs_no_media(self, video):
        http = (Provider()
                .add("GET", f"{IG}/C1", resp(200, {"status_code": "FINISHED"}))
                .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "M1"})))
        media = RecordingProvider()
        ig_pub(http, media).publish(ig_ctx(video, state={"container_id": "C1"}), Progress())
        assert media.events == []

    def test_storage_not_configured_fails_cleanly_and_not_retryable(self, video):
        media = RecordingProvider(fail_prepare=StorageNotConfiguredError("Instagram temporary media storage is not configured"))
        with pytest.raises(PublishError) as exc:
            ig_pub(Provider(), media).publish(ig_ctx(video), Progress())
        assert exc.value.code == "media_storage" and not exc.value.retryable

    def test_storage_outage_is_retryable(self, video):
        media = RecordingProvider(fail_prepare=MediaStorageError("Temporary media upload failed: SlowDown (HTTP 503)",
                                                                   retryable=True))
        with pytest.raises(PublishError) as exc:
            ig_pub(Provider(), media).publish(ig_ctx(video), Progress())
        assert exc.value.retryable

    def test_explicit_url_override_skips_storage(self, video):
        http, media = self.happy(), RecordingProvider()
        ig_pub(http, media).publish(ctx(video, {"video_url": "https://cdn.example.com/v.mp4"}), Progress())
        assert media.events == []

    def test_validate_no_network_and_delivery_notes(self, video):
        pub = InstagramPublisher(media_provider=lambda: pytest.fail("validate must not build the provider"))
        m = MediaInfo(str(video), 1000, "video/mp4")
        assert pub.validate("ok", {}, m) == []
        assert "object storage" in pub.delivery_notes({})[0]


# ---------------------------------------------------------------------------------------------
# Engine / CLI / regression
# ---------------------------------------------------------------------------------------------

from tests.unit.test_publishing_engine import (
    FakePublisher,
    engine,
    env,  # noqa: F401 - fixture
    job_by_platform,
    ok,
)


def test_dry_run_plan_touches_no_storage(env):  # noqa: F811
    media = RecordingProvider()
    pubs = {"instagram": InstagramPublisher(media_provider=lambda: media), "youtube": FakePublisher("youtube")}
    _db, _accounts, ids, video_path = env
    plan = engine(env, pubs).plan_destinations(video_path, "c", [(ids["instagram"], {})])
    assert plan[0].ready and any("object storage" in n for n in plan[0].notes)
    assert media.events == []


def test_youtube_and_instagram_jobs_stay_independent(env):  # noqa: F811
    """One local source, two independent jobs: an Instagram media failure never affects YouTube."""
    db, _, ids, video_path = env
    media = RecordingProvider(fail_prepare=MediaStorageError("Temporary media upload failed: AccessDenied (HTTP 403)"))
    pubs = {"instagram": ig_pub(Provider(), media), "youtube": FakePublisher("youtube", ok("YT1"))}
    post_id = JobStore(db).create_post(video_path, "c", [
        (ids["instagram"], {}),  # local file only, no URL
        (ids["youtube"], {"title": "T", "privacy_status": "private"}),
    ])
    engine(env, pubs).publish_post(post_id)
    jobs = job_by_platform(env, post_id)
    assert jobs["youtube"].status == "published"
    assert jobs["instagram"].status == "failed" and "AccessDenied" in jobs["instagram"].error_message
    assert media.events == ["prepare:clip.mp4:video/mp4"]
    assert os.path.exists(video_path)


def test_create_post_uses_current_path_for_known_video(env, tmp_path):  # noqa: F811
    """Regression: a re-used video row kept a stale path, so publishing failed with 'Video file not found'."""
    import shutil

    db, _, ids, original = env
    store = JobStore(db)
    first = store.create_post(original, "c", [(ids["youtube"], {"title": "T", "privacy_status": "private"})])
    moved = tmp_path / "elsewhere" / "same.mp4"
    moved.parent.mkdir()
    shutil.move(original, moved)
    second = store.create_post(str(moved), "c", [(ids["youtube"], {"title": "T", "privacy_status": "private"})])
    _, video = store.get_post(second)
    assert os.path.normcase(video.path) == os.path.normcase(str(moved.resolve()))
    assert store.get_post(first)[1].id == video.id


def test_create_post_cli_has_no_url_prompt(env, capsys):  # noqa: F811
    from src.cli.publish_menu import run_create_post
    from src.core.publisher import PublisherEngine

    db, accounts, ids, video_path = env
    media = RecordingProvider()
    ig = InstagramPublisher(media_provider=lambda: media)
    eng = PublisherEngine(db, accounts, publishers={"instagram": ig}, sleep=lambda s: None, probe_media=False)
    ig_number = [a.id for a in accounts.get_active_accounts()].index(ids["instagram"]) + 1
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return {0: video_path, 1: "", 2: "caption", 3: str(ig_number), 4: ""}.get(len(prompts) - 1, "n")

    with patch("builtins.input", fake_input), patch("src.cli.publish_menu.clear_screen"):
        run_create_post(accounts, eng)
    assert not any("URL" in p or "url" in p for p in prompts)
    assert "delivered automatically" in capsys.readouterr().out


def test_storage_error_retry_classification():
    from src.media_storage.s3 import _safe_error

    assert _safe_error("x", ClientError("InternalError", 500)).retryable
    assert _safe_error("x", ClientError("SlowDown", 503)).retryable
    assert _safe_error("x", ConnectionError("endpoint unreachable")).retryable
    assert not _safe_error("x", ClientError("AccessDenied", 403)).retryable
    assert not _safe_error("x", ClientError("NoSuchBucket", 404)).retryable


def test_settings_storage_check(monkeypatch, capsys):
    from src.cli.content_menu import check_media_storage

    for k in STORAGE_ENV:
        monkeypatch.delenv(k, raising=False)
    assert check_media_storage() is False
    assert "not configured" in capsys.readouterr().out
    for k, v in STORAGE_ENV.items():
        monkeypatch.setenv(k, v)
    client = FakeS3Client()
    with patch("src.media_storage.create_media_provider", lambda: ObjectStorageMediaProvider(s3(client), 900)):
        assert check_media_storage() is True
    out = capsys.readouterr().out
    assert client.calls == [("head_bucket", "soc-bot-temp")]  # nothing uploaded
    assert "AKIDSECRET" not in out and "SUPERSECRETKEY" not in out
