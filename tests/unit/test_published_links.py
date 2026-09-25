"""Published-link library: per-platform JSON files, written only after confirmed publication."""

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.core.jobs import JobStore
from src.core.published_links import (
    LinkError,
    PublishedLinks,
    copy_to_clipboard,
    is_permanent_platform_url,
    video_label,
)
from src.platforms.base import PublishError
from src.platforms.instagram.publisher import InstagramPublisher
from src.platforms.tiktok.publisher import TikTokPublisher
from src.platforms.youtube.publisher import YouTubePublisher
from tests.unit.test_publishing_engine import (  # noqa: F401
    FakePublisher,
    engine,
    env,
    ok,
)

YT_URL = "https://www.youtube.com/watch?v=IgHvoyTmD4k"
IG_URL = "https://www.instagram.com/reel/DdttFw8CqnI/"


@pytest.fixture
def links(tmp_path):
    return PublishedLinks(tmp_path / "published_links")


# ---------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------

class TestStorage:
    def test_creates_directory_and_three_files(self, links):
        assert not links.root.exists()
        links.ensure_files()
        assert sorted(p.name for p in links.root.iterdir()) == ["instagram.json", "tiktok.json", "youtube.json"]
        assert json.loads(links.path("youtube").read_text(encoding="utf-8")) == []

    def test_add_and_read(self, links):
        when = datetime(2026, 9, 25, 7, 17, 32, tzinfo=timezone.utc)
        assert links.add("youtube", "BUDI 2", "AI.Nepal69", YT_URL, "IgHvoyTmD4k", when) is True
        (record,) = links.records("youtube")
        assert record == {"video": "BUDI 2", "account": "AI.Nepal69", "url": YT_URL, "provider_id": "IgHvoyTmD4k",
                          "published_at": "2026-09-25T07:17:32+00:00"}
        text = links.path("youtube").read_text(encoding="utf-8")
        assert text.startswith("[\n  {") and text.endswith("]\n")  # readable UTF-8 JSON

    def test_missing_file_is_created_on_first_add(self, links):
        links.add("instagram", "clip", "noxivra_01", IG_URL, "17981564450901718")
        assert links.path("instagram").exists() and not links.path("tiktok").exists()

    def test_unicode_preserved(self, links):
        links.add("youtube", "朽木ルキア 🌸", "AI.Nepal69", YT_URL, "IgHvoyTmD4k")
        assert "朽木ルキア 🌸" in links.path("youtube").read_text(encoding="utf-8")

    def test_duplicates_ignored(self, links):
        assert links.add("youtube", "v", "AI.Nepal69", YT_URL, "IgHvoyTmD4k")
        assert links.add("youtube", "v", "AI.Nepal69", YT_URL, "IgHvoyTmD4k") is False
        assert len(links.records("youtube")) == 1

    def test_same_video_two_accounts_two_records(self, links):
        links.add("youtube", "BUDI 2", "AI.Nepal69", YT_URL, "IgHvoyTmD4k")
        links.add("youtube", "BUDI 2", "YushaCyber", "https://www.youtube.com/watch?v=PWEGjXTLGOU", "PWEGjXTLGOU")
        assert [r["account"] for r in links.records("youtube")] == ["AI.Nepal69", "YushaCyber"]

    def test_malformed_json_is_kept_aside_not_lost(self, links):
        links.root.mkdir(parents=True)
        links.path("youtube").write_text("{not json", encoding="utf-8")
        assert links.records("youtube") == []
        backups = list(links.root.glob("youtube.corrupt-*.json"))
        assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "{not json"
        links.add("youtube", "v", "a", YT_URL, "IgHvoyTmD4k")
        assert len(links.records("youtube")) == 1

    def test_non_list_json_is_kept_aside(self, links):
        links.root.mkdir(parents=True)
        links.path("instagram").write_text('{"url": "x"}', encoding="utf-8")
        assert links.records("instagram") == [] and list(links.root.glob("instagram.corrupt-*.json"))

    def test_atomic_write_keeps_old_file_when_interrupted(self, links):
        links.add("youtube", "v", "a", YT_URL, "IgHvoyTmD4k")
        before = links.path("youtube").read_text(encoding="utf-8")
        with patch("src.core.published_links.os.replace", side_effect=KeyboardInterrupt), pytest.raises(KeyboardInterrupt):
            links.add("youtube", "v2", "b", "https://www.youtube.com/watch?v=PWEGjXTLGOU", "PWEGjXTLGOU")
        assert links.path("youtube").read_text(encoding="utf-8") == before  # untouched
        assert not list(links.root.glob(".youtube.*.tmp"))                   # temp file removed

    def test_unknown_platform(self, links):
        with pytest.raises(LinkError):
            links.path("facebook")


class TestTemporaryUrlProtection:
    @pytest.mark.parametrize("platform,url", [
        ("instagram", "https://tempfile.org/kN8mP2xQvR7/download"),
        ("instagram", "https://0x0.st/X9aB.mp4"),
        ("instagram", "https://bucket.s3.amazonaws.com/instagram/temp/k/video.mp4?X-Amz-Signature=abc"),
        ("instagram", "https://acct.r2.cloudflarestorage.com/b/k.mp4?X-Amz-Credential=x"),
        ("instagram", "https://www.instagram.com/reel/x/?X-Amz-Signature=abc"),
        ("youtube", "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=S"),
        ("youtube", "http://www.youtube.com/watch?v=IgHvoyTmD4k"),
        ("tiktok", "https://open-upload.tiktokapis.com/upload/?upload_token=x"),
        ("youtube", IG_URL),  # wrong platform's site
    ])
    def test_rejected(self, links, platform, url):
        assert not is_permanent_platform_url(platform, url)
        with pytest.raises(LinkError):
            links.add(platform, "v", "a", url, "id1")
        assert links.records(platform) == []

    def test_accepted(self):
        assert is_permanent_platform_url("youtube", YT_URL)
        assert is_permanent_platform_url("instagram", IG_URL)
        assert is_permanent_platform_url("tiktok", "https://www.tiktok.com/@a/video/1")


class TestHelpers:
    def test_video_label(self):
        assert video_label(r"D:\Soc_bot\videos\test_youtub.mp4") == "test_youtub"
        assert video_label(r"D:\Soc_bot\content\published\BUDI 2\video.mp4") == "BUDI 2"

    def test_clipboard_uses_clip_exe(self):
        runner = MagicMock()
        with patch("src.core.published_links.os.name", "nt"):
            copy_to_clipboard(YT_URL, runner=runner)
        runner.assert_called_once_with(["clip"], input=YT_URL.encode("ascii"), check=True)

    def test_clipboard_rejects_non_ascii_and_non_windows(self):
        with patch("src.core.published_links.os.name", "nt"), pytest.raises(ValueError):
            copy_to_clipboard("https://x/ü", runner=MagicMock())
        with patch("src.core.published_links.os.name", "posix"), pytest.raises(OSError):
            copy_to_clipboard(YT_URL, runner=MagicMock())

    def test_published_url_rules(self):
        yt, tt = YouTubePublisher(), TikTokPublisher()
        assert yt.published_url("IgHvoyTmD4k", {}) == YT_URL
        assert yt.published_url("not-an-id", {}) is None
        assert tt.published_url("v_pub_url~v2.123", {"publish_id": "v_pub_url~v2.123"}) is None  # no documented URL
        assert InstagramPublisher().published_url("17981564450901718", {"permalink": IG_URL}) == IG_URL
        assert InstagramPublisher().published_url("17981564450901718", {}) is None  # never a guessed URL


# ---------------------------------------------------------------------------------------------
# Engine integration (real adapters, mocked HTTP)
# ---------------------------------------------------------------------------------------------

IG_GRAPH = "https://graph.instagram.com/v25.0"


def mocked_platforms(instagram_ok=True, permalink=IG_URL):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "IG_MEDIA_1"})
        if url.endswith("/17841400000/media"):
            if not instagram_ok:
                return httpx.Response(400, json={"error": {"message": "Media download failed", "code": 9004}})
            return httpx.Response(200, json={"id": "C1"})
        if url.endswith("/C1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if url == f"{IG_GRAPH}/IG_MEDIA_1":
            assert request.url.params["fields"] == "permalink"
            return httpx.Response(200, json={"permalink": permalink, "id": "IG_MEDIA_1"})
        if "creator_info" in url:
            return httpx.Response(200, json={"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if url.endswith("/video/init/"):
            return httpx.Response(200, json={"data": {"publish_id": "v_pub_1", "upload_url": "https://open-upload.tiktokapis.com/upload/?t=1"},
                                             "error": {"code": "ok"}})
        if url.startswith("https://open-upload.tiktokapis.com"):
            return httpx.Response(201)
        if url.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}})
        if request.method == "POST" and "upload/youtube" in url:
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=S"})
        if request.method == "PUT" and "upload/youtube" in url:
            return httpx.Response(201, json={"id": "IgHvoyTmD4k"})
        if "youtube/v3/videos" in url:
            return httpx.Response(200, json={"items": [{"status": {"uploadStatus": "processed"}}]})
        raise AssertionError(url)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    kw = {"client": client, "sleep": lambda s: None, "poll_interval": 0}
    return {"instagram": InstagramPublisher(**kw), "tiktok": TikTokPublisher(**kw), "youtube": YouTubePublisher(**kw)}


def post(env, platforms=("instagram", "tiktok", "youtube")):  # noqa: F811
    from tests.unit.test_publishing_engine import OPTIONS

    db, _, ids, video = env
    return JobStore(db).create_post(video, "caption", [(ids[p], OPTIONS[p]) for p in platforms])


class TestEngineIntegration:
    def test_each_platform_goes_to_its_own_file(self, env, links):  # noqa: F811
        eng = engine(env, mocked_platforms(), links=links)
        eng.publish_post(post(env))
        (yt,) = links.records("youtube")
        (ig,) = links.records("instagram")
        assert yt["url"] == YT_URL and yt["provider_id"] == "IgHvoyTmD4k" and yt["account"] == "youtube_user"
        assert yt["video"] == "clip"
        assert ig["url"] == IG_URL and ig["provider_id"] == "IG_MEDIA_1"
        assert links.records("tiktok") == []  # TikTok publishes but documents no post URL
        for platform in ("youtube", "instagram"):
            text = links.path(platform).read_text(encoding="utf-8")
            assert "tempfile" not in text and "X-Amz" not in text and "googleapis" not in text

    def test_instagram_failure_saves_nothing_others_still_saved(self, env, links):  # noqa: F811
        eng = engine(env, mocked_platforms(instagram_ok=False), links=links)
        eng.publish_post(post(env))
        assert len(links.records("youtube")) == 1
        assert links.records("instagram") == []

    def test_missing_permalink_publishes_but_saves_no_link(self, env, links):  # noqa: F811
        eng = engine(env, mocked_platforms(permalink=None), links=links)
        result = eng.publish_post(post(env, ("instagram",)))
        assert result.count("published") == 1 and links.records("instagram") == []

    def test_failed_job_creates_no_link(self, env, links):  # noqa: F811
        fake = FakePublisher("youtube", PublishError("quota", code="quota_exceeded"))
        fake.published_url = lambda media_id, state: YT_URL  # would produce a link if (wrongly) called
        engine(env, {"youtube": fake}, links=links).publish_post(post(env, ("youtube",)))
        assert links.records("youtube") == []

    def test_retry_then_success_saves_once(self, env, links):  # noqa: F811
        fake = FakePublisher("youtube", PublishError("5xx", code="x", retryable=True), ok("IgHvoyTmD4k"))
        fake.published_url = lambda media_id, state: f"https://www.youtube.com/watch?v={media_id}"
        eng = engine(env, {"youtube": fake}, links=links)
        post_id = post(env, ("youtube",))
        eng.publish_post(post_id)
        eng.publish_post(post_id)          # published job is never republished or re-recorded
        eng.import_published_links()       # history import skips existing records
        assert len(links.records("youtube")) == 1

    def test_link_failure_never_fails_the_job(self, env, links):  # noqa: F811
        fake = FakePublisher("youtube", ok("IgHvoyTmD4k"))
        fake.published_url = lambda media_id, state: "https://tempfile.org/x/download"  # rejected by the library
        result = engine(env, {"youtube": fake}, links=links).publish_post(post(env, ("youtube",)))
        assert result.count("published") == 1 and links.records("youtube") == []

    def test_no_link_library_means_nothing_written(self, env, tmp_path):  # noqa: F811
        eng = engine(env, mocked_platforms())
        eng.publish_post(post(env, ("youtube",)))
        assert not (tmp_path / "published_links").exists()

    def test_import_history_backfills_youtube_and_instagram(self, env, links):  # noqa: F811
        eng = engine(env, mocked_platforms())       # published earlier WITHOUT the link library
        eng.publish_post(post(env))
        eng.links = links
        added = eng.import_published_links()
        assert added == {"youtube": 1, "instagram": 1, "tiktok": 0}
        assert links.records("instagram")[0]["url"] == IG_URL
        assert eng.import_published_links() == {"youtube": 0, "instagram": 0, "tiktok": 0}


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

class TestCli:
    def test_list_and_copy(self, links, capsys):
        from src.cli.links_menu import copy_link, list_links

        links.add("youtube", "BUDI 2", "AI.Nepal69", YT_URL, "IgHvoyTmD4k",
                  datetime(2026, 9, 25, tzinfo=timezone.utc))
        records = list_links(links, "youtube")
        out = capsys.readouterr().out
        assert "1. BUDI 2" in out and "Account: AI.Nepal69" in out and "Published: 2026-09-25" in out and YT_URL in out
        with patch("builtins.input", return_value="1"), patch("src.cli.links_menu.copy_to_clipboard") as clip:
            assert copy_link(records) is True
        clip.assert_called_once_with(YT_URL)
        assert "Link copied to clipboard." in capsys.readouterr().out

    def test_empty_lists(self, links, capsys):
        from src.cli.links_menu import list_links

        assert list_links(links, "youtube") == []
        assert "No YouTube links saved." in capsys.readouterr().out
        list_links(links, "tiktok")
        assert "No TikTok links saved." in capsys.readouterr().out

    def test_menu_navigation(self, links, capsys):
        from src.cli.links_menu import run_published_links

        links.add("instagram", "clip", "noxivra_01", IG_URL, "1")
        answers = iter(["2", "1", "", "3", "5"])  # Instagram -> List -> Enter -> Back -> Back
        with patch("builtins.input", lambda *_: next(answers)), patch("src.cli.links_menu.clear_screen"):
            run_published_links(links)
        assert IG_URL in capsys.readouterr().out


def test_main_creates_link_files(tmp_path, monkeypatch):
    import main as main_module

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("ENCRYPTION_KEY", "nZEJx1hxthoUa6wzoWYOVg0rNAsmhidhd9uASEPii5s=")
    _, _, eng, _ = main_module.initialize_services()
    assert eng.links is not None
    assert sorted(os.listdir(tmp_path / "content" / "published_links")) == ["instagram.json", "tiktok.json", "youtube.json"]
