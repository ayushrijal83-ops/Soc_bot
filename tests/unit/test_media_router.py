"""Size-based media routing (MEDIA_STORAGE_PROVIDER=auto). No network: fake providers + mocked HTTP."""

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.core.jobs import JobStore
from src.core.validation import MediaInfo
from src.media_storage import (
    MediaStorageError,
    StorageNotConfiguredError,
    StorageSettings,
    build_router,
    create_media_provider,
    media_delivery_description,
)
from src.media_storage.provider import (
    MediaHandle,
    MediaSourceProvider,
    ObjectStorageMediaProvider,
)
from src.media_storage.router import MediaStorageRouter, Tier
from src.media_storage.tempfile import TempFileMediaStorage
from src.media_storage.zerox0 import ZeroX0MediaStorage
from src.platforms.instagram.publisher import InstagramPublisher
from tests.unit.test_publishing_engine import (  # noqa: F401
    FakePublisher,
    engine,
    env,
    job_by_platform,
    ok,
)

LIMIT = TempFileMediaStorage.max_file_size          # 100_000_000 (decimal "100MB")
MARGIN = 1_000_000
S3_ENV = {"MEDIA_STORAGE_BUCKET": "b", "MEDIA_STORAGE_ACCESS_KEY": "AKIDSECRET", "MEDIA_STORAGE_SECRET_KEY": "SECRETKEY",
          "MEDIA_STORAGE_REGION": "us-east-1", "MEDIA_STORAGE_ENDPOINT": ""}


class RecordingProvider(MediaSourceProvider):
    def __init__(self, name, events, fail=None):
        self.name, self.events, self.fail = name, events, fail
        self.count = 0

    def prepare(self, video_path, content_type):
        self.count += 1
        self.events.append(f"{self.name}:prepare")
        if self.fail:
            raise self.fail
        return MediaHandle(Path(video_path), content_type, public_url=f"https://{self.name}.example/{self.count}.mp4",
                           token=f"{self.name}-{self.count}")

    def get_public_url(self, handle):
        self.events.append(f"{self.name}:url")
        return handle.public_url

    def cleanup(self, handle):
        if handle is None or handle.cleaned:
            return
        handle.cleaned = True
        self.events.append(f"{self.name}:cleanup:{handle.token}")

    def health_check(self):
        return f"{self.name} ok"


def router(events, small_problems=(), large_problems=(), small_fail=None):
    small = RecordingProvider("small", events, fail=small_fail)
    large = RecordingProvider("large", events)
    built = {"small": 0, "large": 0}

    def make(name, provider):
        def factory():
            built[name] += 1
            return provider
        return factory

    r = MediaStorageRouter([
        Tier("tempfile", "TempFile.org (temporary PUBLIC upload)", LIMIT, lambda: list(small_problems), make("small", small)),
        Tier("s3", "S3 (PRIVATE temporary object + presigned HTTPS URL)", None, lambda: list(large_problems), make("large", large)),
    ], margin_bytes=MARGIN)
    return r, small, large, built


def sized(tmp_path, size, name="clip.mp4"):
    p = tmp_path / name
    with open(p, "wb") as f:
        f.truncate(size)
    return p


# ---------------------------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------------------------

class TestSelection:
    @pytest.mark.parametrize("size,expected", [
        (10_060_970, "tempfile"),                  # the real test video
        (LIMIT - MARGIN, "tempfile"),              # exactly at the usable limit -> still TempFile
        (LIMIT - MARGIN + 1, "s3"),                # one byte over the usable limit -> S3
        (LIMIT, "s3"),                             # the provider's hard limit leaves no margin -> S3
        (150_000_000, "s3"),
        (5_000_000_000, "s3"),                     # very large
    ])
    def test_boundaries(self, size, expected):
        r, *_ = router([])
        assert r.select(size).name == expected

    def test_large_file_without_s3_is_a_clear_error(self):
        r, *_ = router([], large_problems=["Missing MEDIA_STORAGE_BUCKET"])
        with pytest.raises(StorageNotConfiguredError) as exc:
            r.select(150_000_000)
        text = str(exc.value)
        assert "150.0 MB" in text and "TempFile.org" in text and "99.0 MB" in text and "S3" in text
        assert "Missing MEDIA_STORAGE_BUCKET" in text

    def test_small_file_prefers_tempfile_even_when_s3_configured(self):
        r, *_ = router([])
        assert r.select(1_000).name == "tempfile"

    def test_small_file_uses_s3_only_if_tempfile_unusable(self):
        r, *_ = router([], small_problems=["bad expiry"])
        assert r.select(1_000).name == "s3"


class TestCapabilities:
    def test_declared_limits(self):
        assert TempFileMediaStorage.max_file_size == 100_000_000        # documented "100MB", decimal (safe side)
        assert ZeroX0MediaStorage.max_file_size == 512 * 1024 * 1024    # documented "512.0 MiB"
        assert ObjectStorageMediaProvider.max_file_size is None         # multipart S3: no app limit

    def test_router_uses_provider_capabilities_not_constants(self, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        r = build_router(StorageSettings.from_env())
        assert [t.name for t in r.tiers] == ["tempfile", "cloudflare_tunnel", "s3"]
        assert r.tiers[0].max_file_size == TempFileMediaStorage.max_file_size
        assert r.tiers[1].max_file_size is None and r.tiers[2].max_file_size is None
        monkeypatch.setattr(TempFileMediaStorage, "max_file_size", 5_000_000)
        assert build_router(StorageSettings.from_env()).tiers[0].max_file_size == 5_000_000

    def test_margin_configurable(self, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        monkeypatch.setenv("MEDIA_STORAGE_AUTO_MARGIN_MB", "5")
        assert build_router(StorageSettings.from_env()).margin_bytes == 5_000_000
        monkeypatch.setenv("MEDIA_STORAGE_AUTO_MARGIN_MB", "500")
        assert any("MARGIN" in p for p in StorageSettings.from_env().problems())


# ---------------------------------------------------------------------------------------------
# Lifecycle through the router
# ---------------------------------------------------------------------------------------------

class TestLifecycle:
    def test_small_video_goes_to_tempfile_and_back(self, tmp_path):
        events = []
        r, _small, _large, built = router(events)
        h = r.prepare(sized(tmp_path, 10_000), "video/mp4")
        assert r.get_public_url(h) == "https://small.example/1.mp4"
        r.cleanup(h)
        r.cleanup(h)  # idempotent
        assert events == ["small:prepare", "small:url", "small:cleanup:small-1"]
        assert built == {"small": 1, "large": 0}  # S3 never even built for a small file

    def test_large_video_goes_to_s3(self, tmp_path):
        events = []
        r, _small, _large, built = router(events)
        h = r.prepare(sized(tmp_path, 150_000_000), "video/mp4")
        r.get_public_url(h)
        r.cleanup(h)
        assert events == ["large:prepare", "large:url", "large:cleanup:large-1"]
        assert built == {"small": 0, "large": 1}

    def test_no_fallback_when_tempfile_fails(self, tmp_path):
        events = []
        r, _small, _large, built = router(events, small_fail=MediaStorageError("TempFile 503", retryable=True))
        with pytest.raises(MediaStorageError, match="TempFile 503") as exc:
            r.prepare(sized(tmp_path, 10_000), "video/mp4")
        assert exc.value.retryable
        assert events == ["small:prepare"] and built["large"] == 0  # media never moved to S3

    def test_handle_repr_hides_owner_and_secrets(self, tmp_path):
        r, *_ = router([])
        h = r.prepare(sized(tmp_path, 10_000), "video/mp4")
        assert "small-1" not in repr(h) and "RecordingProvider" not in repr(h)

    def test_missing_file(self, tmp_path):
        r, *_ = router([])
        with pytest.raises(MediaStorageError, match="not found"):
            r.prepare(tmp_path / "nope.mp4", "video/mp4")


# ---------------------------------------------------------------------------------------------
# Factory / config / descriptions / settings
# ---------------------------------------------------------------------------------------------

class TestConfig:
    def test_auto_needs_no_s3_for_small_files(self, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        for k in S3_ENV:
            monkeypatch.delenv(k, raising=False)
        assert StorageSettings.from_env().problems() == []
        assert isinstance(create_media_provider(), MediaStorageRouter)
        from src.media_storage import media_provider_problems

        assert media_provider_problems(10_000_000) == []
        assert "No media provider can take this 150.0 MB video" in media_provider_problems(150_000_000)[0]

    def test_existing_single_provider_modes_unchanged(self, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "tempfile")
        assert isinstance(create_media_provider(), TempFileMediaStorage)
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "unknown")
        with pytest.raises(StorageNotConfiguredError, match="auto, tempfile, cloudflare_tunnel, s3 or 0x0"):
            create_media_provider()

    def test_descriptions_name_the_provider_by_size(self, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        for k, v in S3_ENV.items():
            monkeypatch.setenv(k, v)
        small = media_delivery_description(48_200_000)
        large = media_delivery_description(184_700_000)
        assert small.startswith("48.2 MB video -> temporary PUBLIC upload to TempFile.org")
        assert large.startswith("184.7 MB video -> temporary PRIVATE object storage (S3) + presigned HTTPS URL")
        assert "AKIDSECRET" not in small + large

    def test_settings_check_shows_both_tiers_without_uploading(self, monkeypatch, capsys):
        from src.cli.content_menu import check_media_storage

        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        for k in S3_ENV:
            monkeypatch.delenv(k, raising=False)
        events = []
        r, *_ = router(events, large_problems=["Missing MEDIA_STORAGE_BUCKET"])
        with patch("src.media_storage.create_media_provider", lambda: r):
            assert check_media_storage() is True
        out = capsys.readouterr().out
        assert "Media storage mode: AUTO" in out and "PUBLIC temporary hosting" in out and "PRIVATE temporary object" in out
        assert "TempFile.org (temporary PUBLIC upload) (up to 99.0 MB): small ok" in out
        assert "S3 (PRIVATE temporary object + presigned HTTPS URL) (no size limit): NOT configured" in out
        assert not any("prepare" in e for e in events)

    def test_create_post_warning_depends_on_size(self, monkeypatch, capsys):
        from src.cli.publish_menu import _instagram_notice

        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        for k, v in S3_ENV.items():
            monkeypatch.setenv(k, v)
        _instagram_notice(10_000_000, False)
        assert "TempFile.org, a PUBLIC" in capsys.readouterr().out
        _instagram_notice(150_000_000, False)
        assert "temporary private storage" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------
# Instagram + engine: dry-run, multiple accounts, other platforms
# ---------------------------------------------------------------------------------------------

def test_dry_run_plan_shows_selected_provider_without_network(env, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
    for k, v in S3_ENV.items():
        monkeypatch.setenv(k, v)
    _db, _accounts, ids, _video = env
    no_network = httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network")))
    pubs = {"instagram": InstagramPublisher(client=no_network)}
    large = sized(tmp_path, 150_400_000, "large_clip.mp4")
    with patch("src.media_storage.s3.S3ObjectStorage.__init__", side_effect=AssertionError("S3 client built")), \
            patch("src.media_storage.tempfile.TempFileMediaStorage.prepare", side_effect=AssertionError("upload")):
        plan = engine(env, pubs).plan_destinations(str(large), "c", [(ids["instagram"], {})])
    assert plan[0].ready
    assert any("150.4 MB video -> temporary PRIVATE object storage (S3)" in n for n in plan[0].notes)
    assert any("uploaded only when publishing" in n for n in plan[0].notes)


def test_instagram_blocked_in_plan_when_large_and_no_s3(env, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
    for k in S3_ENV:
        monkeypatch.delenv(k, raising=False)
    _db, _accounts, ids, _video = env
    plan = engine(env, {"instagram": InstagramPublisher()}).plan_destinations(
        str(sized(tmp_path, 150_000_000)), "c", [(ids["instagram"], {})])
    assert not plan[0].ready and "No media provider can take this 150.0 MB video" in plan[0].errors[0]


def two_instagram_accounts(services):
    db, accounts, ids, video = services
    from datetime import datetime, timedelta, timezone

    second = accounts.create_account(platform="instagram", platform_account_id="17841499999", username="ig_two",
                                     access_token="TOKEN_TWO", expires_at=datetime.now(timezone.utc) + timedelta(days=30)).id
    post_id = JobStore(db).create_post(video, "caption", [(ids["instagram"], {}), (second, {})])
    return post_id, second


class CountingInstagram(FakePublisher):
    """Instagram-like fake that really goes through a media provider per attempt."""

    def __init__(self, provider_factory, outcomes):
        super().__init__("instagram", *outcomes)
        self.provider_factory = provider_factory
        self.seen_urls = []

    def publish(self, ctx, on_progress):
        provider = self.provider_factory()
        handle = provider.prepare(ctx.video_path, ctx.media.mime_type)
        try:
            self.seen_urls.append(provider.get_public_url(handle))
            return super().publish(ctx, on_progress)
        finally:
            provider.cleanup(handle)


def test_one_video_two_instagram_accounts_each_get_their_own_media(env):  # noqa: F811
    events = []
    r, *_ = router(events)
    ig = CountingInstagram(lambda: r, [ok("REEL_A"), ok("REEL_B")])
    post_id, _second = two_instagram_accounts(env)
    engine(env, {"instagram": ig}).publish_post(post_id)
    jobs = JobStore(env[0]).jobs_for_post(post_id)
    assert [j.status for j in jobs] == ["published", "published"]
    assert ig.seen_urls[0] != ig.seen_urls[1]  # independent temporary objects
    # Jobs may run concurrently: each has its own object, and each object is cleaned exactly once.
    assert events.count("small:prepare") == 2 and events.count("small:url") == 2
    assert sorted(e for e in events if "cleanup" in e) == ["small:cleanup:small-1", "small:cleanup:small-2"]


def test_failure_of_one_instagram_account_does_not_affect_the_other(env):  # noqa: F811
    from src.platforms.base import PublishError

    events = []
    r, *_ = router(events)
    ig = CountingInstagram(lambda: r, [PublishError("permission", code="permission_denied"), ok("REEL_B")])
    post_id, _second = two_instagram_accounts(env)
    engine(env, {"instagram": ig}).publish_post(post_id)
    statuses = sorted(j.status for j in JobStore(env[0]).jobs_for_post(post_id))
    assert statuses == ["failed", "published"]
    assert events.count("small:prepare") == 2 and len([e for e in events if "cleanup" in e]) == 2


def test_youtube_and_tiktok_never_touch_media_routing(env):  # noqa: F811
    with patch("src.media_storage.provider.build_router", side_effect=AssertionError("routing used")), \
            patch("src.media_storage.create_media_provider", side_effect=AssertionError("provider used")):
        pubs = {"youtube": FakePublisher("youtube", ok("YT")), "tiktok": FakePublisher("tiktok", ok("TT"))}
        from tests.unit.test_publishing_engine import make_post

        post_id = make_post(env, ("youtube", "tiktok"))
        engine(env, pubs).publish_post(post_id)
    jobs = job_by_platform(env, post_id)
    assert jobs["youtube"].status == jobs["tiktok"].status == "published"


def test_media_info_size_drives_instagram_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
    for k in S3_ENV:
        monkeypatch.delenv(k, raising=False)
    pub = InstagramPublisher()
    assert pub.validate("c", {}, MediaInfo("v.mp4", 10_000_000, "video/mp4")) == []
    assert "150.0 MB" in pub.validate("c", {}, MediaInfo("v.mp4", 150_000_000, "video/mp4"))[0]
