"""Phase 5A: content intake, publishing profile, lifecycle, resume, covers, CLI. No network."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.accounts.manager import AccountManager
from src.cli.content_menu import (
    edit_profile,
    run_content_inbox,
    run_history,
    verify_and_publish,
)
from src.content.detector import ContentDetector, is_safe_name
from src.content.intake import ContentIntake, content_key
from src.content.manager import ContentManager, ContentPathError
from src.content.models import STAGES, content_root
from src.content.profile import Profile, ProfileError, ProfileStore
from src.content.validator import ContentValidator, image_problem, is_stable
from src.core.publisher import PublisherEngine
from src.platforms.base import PublishError, PublishOutcome
from src.platforms.instagram.publisher import InstagramPublisher
from src.platforms.tiktok.publisher import TikTokPublisher
from src.platforms.youtube.publisher import YouTubePublisher
from src.storage.database import ContentItem, Database
from src.storage.tokens import TokenEncryption, generate_key
from tests.unit.test_publishing_engine import FakePublisher

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def make_package(root: Path, name="post_001", video="video.mp4", caption="Hello 🌍 #tag\nline two",
                 cover="cover.jpg", cover_bytes=JPEG, stage="incoming", extra=None) -> Path:
    d = root / stage / name
    d.mkdir(parents=True, exist_ok=True)
    if video:
        (d / video).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 5000)
    if caption is not None:
        (d / "caption.txt").write_text(caption, encoding="utf-8")
    if cover:
        (d / cover).write_bytes(cover_bytes)
    for fname, content in (extra or {}).items():
        (d / fname).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    old = datetime.now(timezone.utc).timestamp() - 3600  # "copied an hour ago" -> stable without waiting
    for f in d.iterdir():
        os.utime(f, (old, old))
    return d


def detect(root, name="post_001"):
    pkg = ContentDetector(root).detect(root / "incoming" / name, "incoming")
    return ContentValidator(probe_media=False).validate(pkg)


# ------------------------------------------------------------------------------------------
# Detector / validator
# ------------------------------------------------------------------------------------------

class TestDetector:
    def test_finds_package_video_caption_cover(self, tmp_path):
        make_package(tmp_path)
        (pkg,) = ContentDetector(tmp_path).scan("incoming")
        assert pkg.content_id == "post_001"
        assert pkg.video_path.name == "video.mp4"
        assert pkg.caption_path.name == "caption.txt"
        assert pkg.cover_path.name == "cover.jpg"
        assert pkg.validation_errors == []

    @pytest.mark.parametrize("video", ["clip.mov", "clip.webm", "CLIP.MP4"])
    def test_supported_video_types(self, tmp_path, video):
        make_package(tmp_path, video=video)
        assert detect(tmp_path).valid

    def test_missing_video(self, tmp_path):
        make_package(tmp_path, video=None)
        pkg = detect(tmp_path)
        assert not pkg.valid and "has no video file" in pkg.validation_errors[0]

    def test_multiple_videos(self, tmp_path):
        make_package(tmp_path, extra={"second.mov": b"x"})
        pkg = detect(tmp_path)
        assert not pkg.valid and any("multiple video" in e for e in pkg.validation_errors)
        assert pkg.video_path is None  # never guessed

    def test_missing_caption(self, tmp_path):
        make_package(tmp_path, caption=None)
        pkg = detect(tmp_path)
        assert any("has no caption.txt" in e for e in pkg.validation_errors)

    def test_multiple_captions(self, tmp_path):
        make_package(tmp_path, extra={"caption_old.txt": "x"})
        assert any("multiple caption" in e for e in detect(tmp_path).validation_errors)

    def test_multiple_covers(self, tmp_path):
        make_package(tmp_path, extra={"cover.png": PNG})
        pkg = detect(tmp_path)
        assert any("multiple cover" in e for e in pkg.validation_errors) and not pkg.valid

    def test_unsupported_files_are_ignored_with_warning(self, tmp_path):
        make_package(tmp_path, extra={"notes.docx": b"x"})
        pkg = detect(tmp_path)
        assert pkg.valid and any("notes.docx" in w for w in pkg.validation_warnings)

    def test_empty_video_and_caption(self, tmp_path):
        d = make_package(tmp_path, caption="   \n")
        (d / "video.mp4").write_bytes(b"")
        errors = detect(tmp_path).validation_errors
        assert any("empty" in e for e in errors) and any("caption.txt is empty" in e for e in errors)

    def test_caption_preserved_exactly(self, tmp_path):
        caption = "Line 1 😀 #hashtag @user\n\nLine 3 — ünïcödé\n"
        make_package(tmp_path, caption=caption)
        assert detect(tmp_path).caption_text == caption

    def test_invalid_utf8_caption(self, tmp_path):
        d = make_package(tmp_path)
        (d / "caption.txt").write_bytes(b"\xff\xfe\xfa bad")
        assert any("UTF-8" in e for e in detect(tmp_path).validation_errors)

    def test_invalid_cover_is_warning_and_dropped(self, tmp_path):
        make_package(tmp_path, cover_bytes=b"not an image")
        pkg = detect(tmp_path)
        assert pkg.valid and pkg.cover_path is None
        assert any("could not be read as a valid image" in w for w in pkg.validation_warnings)

    def test_image_checks(self, tmp_path):
        (tmp_path / "a.png").write_bytes(PNG)
        (tmp_path / "b.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
        (tmp_path / "c.jpg").write_bytes(b"")
        assert image_problem(tmp_path / "a.png") is None
        assert image_problem(tmp_path / "b.webp") is None
        assert "empty" in image_problem(tmp_path / "c.jpg")

    def test_optional_title_and_video_url(self, tmp_path):
        make_package(tmp_path, extra={"title.txt": "My Title\n", "video_url.txt": "https://cdn/x.mp4\n"})
        pkg = detect(tmp_path)
        assert pkg.title == "My Title" and pkg.video_url == "https://cdn/x.mp4"

    def test_partial_download_marks_copying(self, tmp_path):
        make_package(tmp_path, extra={"video2.mp4.crdownload": b"x"})
        assert detect(tmp_path).validation_status == "copying"

    def test_safe_names(self):
        assert is_safe_name("post_001")
        for bad in ("..", ".", ".hidden", "a/b", "a\\b", ""):
            assert not is_safe_name(bad)


class TestStability:
    def test_changing_file_is_not_ready(self, tmp_path):
        d = tmp_path / "pkg"
        d.mkdir()
        f = d / "video.mp4"
        f.write_bytes(b"x")

        def grow(_):
            f.write_bytes(b"xx" * 1000)  # still being copied

        assert is_stable(d, 3, sleep=grow) is False

    def test_stable_file_becomes_ready(self, tmp_path):
        d = tmp_path / "pkg"
        d.mkdir()
        (d / "video.mp4").write_bytes(b"x")
        waited = []
        assert is_stable(d, 3, sleep=waited.append) is True
        assert waited == [3]

    def test_old_files_need_no_wait(self, tmp_path):
        d = make_package(tmp_path)
        waited = []
        assert is_stable(d, 3, sleep=waited.append) is True
        assert waited == []


class TestManager:
    def test_creates_all_stage_dirs(self, tmp_path):
        ContentManager(tmp_path).ensure_dirs()
        assert all((tmp_path / s).is_dir() for s in STAGES)

    def test_move_between_stages_never_deletes(self, tmp_path):
        mgr = ContentManager(tmp_path)
        d = make_package(tmp_path)
        moved = mgr.move(d, "publishing")
        assert moved == tmp_path / "publishing" / "post_001" and (moved / "video.mp4").exists()
        again = mgr.move(make_package(tmp_path), "publishing")  # target exists -> suffixed, not overwritten
        assert again.name.startswith("post_001__") and (moved / "video.mp4").exists()

    def test_refuses_paths_outside_root(self, tmp_path):
        mgr = ContentManager(tmp_path / "root")
        outside = tmp_path / "elsewhere" / "pkg"
        outside.mkdir(parents=True)
        with pytest.raises(ContentPathError):
            mgr.move(outside, "published")
        with pytest.raises(ContentPathError):
            mgr.stage_dir("../../etc")
        with pytest.raises(ContentPathError):
            mgr.locate("..")

    def test_content_root_configurable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONTENT_ROOT", str(tmp_path / "c"))
        assert content_root() == (tmp_path / "c").resolve()
        monkeypatch.delenv("CONTENT_ROOT")
        assert content_root().name == "content"


# ------------------------------------------------------------------------------------------
# Profile + intake integration (real DB, fake publishers)
# ------------------------------------------------------------------------------------------

class FakeYouTube(FakePublisher):
    """Fake adapter with YouTube's cover capability."""

    MAX_THUMBNAIL_BYTES = YouTubePublisher.MAX_THUMBNAIL_BYTES

    def supports_cover_upload(self):
        return True

    def validate_cover(self, cover_path):
        return YouTubePublisher.validate_cover(self, cover_path)


def ok(media="M", cover=None):
    return PublishOutcome("published", media, {"ref": "R1"}, cover_status=cover)


@pytest.fixture
def env(tmp_path):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    enc = TokenEncryption(generate_key().encode())
    db = Database(f"sqlite:///{path}", encryption=enc)
    db.init()
    accounts = AccountManager(db, enc)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    ids = {}
    for key, platform, pid in (("yt_a", "youtube", "UCA"), ("yt_b", "youtube", "UCB"),
                               ("tt", "tiktok", "open1"), ("ig", "instagram", "1784")):
        ids[key] = accounts.create_account(platform=platform, platform_account_id=pid, username=key,
                                           access_token=f"TOKEN_{key}", expires_at=future).id
    root = tmp_path / "content"
    yield db, accounts, ids, root
    db.engine.dispose()
    try:
        os.unlink(path)
    except PermissionError:
        pass


def intake_with(env, publishers):
    db, accounts, _, root = env
    engine = PublisherEngine(db, accounts, publishers=publishers, sleep=lambda s: None, probe_media=False)
    return ContentIntake(db, accounts, engine, root=root, stability=0, sleep=lambda s: None, probe_media=False)


def save_profile(env, platforms=("yt_a", "yt_b", "tt"), **kw):
    db, _, ids, _ = env
    accounts = {"instagram": [], "tiktok": [], "youtube": []}
    for key in platforms:
        accounts[{"yt_a": "youtube", "yt_b": "youtube", "tt": "tiktok", "ig": "instagram"}[key]].append(ids[key])
    profile = Profile(accounts=accounts, tiktok_privacy_level="SELF_ONLY", **kw)
    return ProfileStore(db).save(profile)


class TestProfile:
    def test_create_load_update_reset(self, env):
        store = ProfileStore(env[0])
        assert store.load() is None
        save_profile(env)
        loaded = store.load()
        assert loaded.accounts["youtube"] == [env[2]["yt_a"], env[2]["yt_b"]]
        assert loaded.mode == "verify" and loaded.after_success == "published" and loaded.cover_enabled
        loaded.mode = "auto"
        store.save(loaded)
        assert store.load().mode == "auto"
        assert store.reset() is True and store.load() is None

    def test_stores_account_ids_not_tokens(self, env):
        save_profile(env)
        with env[0].session() as s:
            raw = s.execute(__import__("sqlalchemy").text("select settings_json from publishing_profiles")).scalar()
        assert "TOKEN_" not in raw

    def test_invalid_account_reference(self, env):
        with pytest.raises(ProfileError, match="no longer exists"):
            ProfileStore(env[0]).save(Profile(accounts={"youtube": [999], "tiktok": [], "instagram": []}))
        with pytest.raises(ProfileError, match="not youtube"):
            ProfileStore(env[0]).save(Profile(accounts={"youtube": [env[2]["tt"]], "tiktok": [], "instagram": []}))

    def test_disconnected_account_blocks_publishing(self, env):
        save_profile(env)
        env[1].disconnect_account(env[2]["yt_a"])
        problems = ProfileStore(env[0]).check(ProfileStore(env[0]).load())
        assert any("not currently authorized" in p for p in problems)

    def test_no_destinations(self, env):
        problems = ProfileStore(env[0]).check(Profile())
        assert "No publishing accounts are enabled in the default profile." in problems


class TestIntakePublishing:
    def pubs(self, yt=None, tt=None, ig=None):
        tt = tt or FakePublisher("tiktok", ok("T1"))
        ig = ig or FakePublisher("instagram", ok("I1"))
        # Fakes carry the real adapters' cover capability text.
        tt.COVER_UNSUPPORTED_REASON = TikTokPublisher.COVER_UNSUPPORTED_REASON
        ig.COVER_UNSUPPORTED_REASON = InstagramPublisher.COVER_UNSUPPORTED_REASON
        return {"youtube": yt or FakeYouTube("youtube", ok("V1", "published"), ok("V2", "published")),
                "tiktok": tt, "instagram": ig}

    def test_ready_package_publishes_to_all_and_moves_to_published(self, env):
        save_profile(env)
        make_package(env[3])
        pubs = self.pubs()
        intake = intake_with(env, pubs)
        (entry,) = intake.scan()
        assert entry.status == "READY" and len(entry.destinations) == 3
        result = intake.publish(entry.package)
        assert result.outcome == "published" and result.stage == "published"
        assert (env[3] / "published" / "post_001" / "video.mp4").exists()
        assert not (env[3] / "incoming" / "post_001").exists()
        assert {j.status for j in result.jobs} == {"published"}
        assert len(pubs["youtube"].calls) == 2  # two channels, independent jobs
        # cover goes only to the adapter that supports uploads
        assert all(c.options.get("cover_path", "").endswith("cover.jpg") for c in pubs["youtube"].calls)
        assert "cover_path" not in pubs["tiktok"].calls[0].options
        tt_job = next(j for j in result.jobs if j.platform == "tiktok")
        assert tt_job.cover_status == "not_supported" and "video_cover_timestamp_ms" in tt_job.cover_error

    def test_youtube_options_come_from_package_and_profile(self, env):
        save_profile(env, platforms=("yt_a",))
        make_package(env[3], caption="First line title\nrest of description")
        pubs = self.pubs()
        intake = intake_with(env, pubs)
        intake.publish(intake.scan()[0].package)
        opts = pubs["youtube"].calls[0].options
        assert opts["title"] == "First line title" and opts["privacy_status"] == "private"
        assert pubs["youtube"].calls[0].caption == "First line title\nrest of description"

    def test_partial_failure_moves_to_failed_and_keeps_successes(self, env):
        save_profile(env)
        make_package(env[3])
        yt = FakeYouTube("youtube", ok("V1"), PublishError("quota", code="quota_exceeded"))
        intake = intake_with(env, self.pubs(yt=yt))
        result = intake.publish(intake.scan()[0].package)
        assert result.outcome == "failed" and result.stage == "failed"
        assert (env[3] / "failed" / "post_001").is_dir()
        statuses = sorted(j.status for j in result.jobs)
        assert statuses == ["failed", "published", "published"]

    def test_retry_failed_does_not_republish_successes(self, env):
        save_profile(env)
        make_package(env[3])
        yt = FakeYouTube("youtube", ok("V1"), PublishError("quota", code="quota_exceeded"), ok("V2"))
        tt = FakePublisher("tiktok", ok("T1"))
        intake = intake_with(env, self.pubs(yt=yt, tt=tt))
        intake.publish(intake.scan()[0].package)
        (entry,) = intake.scan()
        assert entry.status == "FAILED" and entry.package.stage == "failed"
        result = intake.publish(entry.package, retry_failed=True)
        assert result.outcome == "published"
        assert len(yt.calls) == 3 and len(tt.calls) == 1  # only the failed job ran again
        assert (env[3] / "published" / "post_001").is_dir()

    def test_crash_resume_does_not_republish(self, env):
        """Crash after YouTube+TikTok succeeded: restart must not publish them again."""
        save_profile(env)
        make_package(env[3])
        yt = FakeYouTube("youtube", ok("V1"), ok("V2"))
        tt = FakePublisher("tiktok", ok("T1"))
        intake = intake_with(env, self.pubs(yt=yt, tt=tt))

        real_finish = intake._finish
        with patch.object(intake, "_finish", side_effect=RuntimeError("crash")), pytest.raises(RuntimeError):
            intake.publish(intake.scan()[0].package)
        assert (env[3] / "publishing" / "post_001").is_dir()

        intake2 = intake_with(env, self.pubs(yt=yt, tt=tt))
        (entry,) = intake2.scan()
        assert entry.status == "RESUME"
        result = intake2.publish(entry.package)
        assert result.outcome == "published"
        assert len(yt.calls) == 2 and len(tt.calls) == 1  # nothing published twice
        assert real_finish is not None

    def test_duplicate_package_never_creates_duplicate_jobs(self, env):
        save_profile(env)
        make_package(env[3])
        intake = intake_with(env, self.pubs())
        intake.publish(intake.scan()[0].package)
        # Same video dropped again under another folder name
        make_package(env[3], name="post_001_copy")
        (entry,) = [e for e in intake.scan() if e.package.content_id == "post_001_copy"]
        assert entry.status == "PUBLISHED"
        result = intake.publish(entry.package)
        assert result.outcome == "skipped" and "Already published" in result.message
        with env[0].session() as s:
            assert s.query(ContentItem).count() == 1
        assert len(intake.engine.store.recent_jobs()) == 3

    def test_new_profile_account_gets_only_a_new_job(self, env):
        save_profile(env, platforms=("yt_a",))
        make_package(env[3])
        yt = FakeYouTube("youtube", PublishError("x", code="unauthorized"), ok("V2"), ok("V1"))
        intake = intake_with(env, self.pubs(yt=yt))
        intake.publish(intake.scan()[0].package)
        save_profile(env, platforms=("yt_a", "yt_b"))
        intake.publish(intake.scan()[0].package, retry_failed=True)
        jobs = intake.engine.store.recent_jobs()
        assert len(jobs) == 2 and len({j.account_id for j in jobs}) == 2

    def test_invalid_package_is_moved_to_failed_not_published(self, env):
        save_profile(env)
        make_package(env[3], caption=None)
        pubs = self.pubs()
        intake = intake_with(env, pubs)
        (entry,) = intake.scan()
        assert entry.status == "INVALID"
        result = intake.publish(entry.package)
        assert result.outcome == "invalid" and (env[3] / "failed" / "post_001").is_dir()
        assert all(not p.calls for p in pubs.values())

    def test_blocked_destination_blocks_package(self, env):
        save_profile(env, platforms=("ig",))  # Instagram needs video_url.txt
        make_package(env[3])
        (entry,) = intake_with(env, self.pubs()).scan()
        assert entry.status == "BLOCKED"
        assert any("video_url.txt" in e for d in entry.destinations for e in d.errors)

    def test_no_profile(self, env):
        make_package(env[3])
        (entry,) = intake_with(env, self.pubs()).scan()
        assert entry.status == "NO PROFILE"

    def test_copying_package_is_not_published(self, env):
        save_profile(env)
        make_package(env[3], extra={"big.mp4.part": b"x"})
        (entry,) = intake_with(env, self.pubs()).scan()
        assert entry.status == "COPYING" and not entry.publishable

    def test_after_success_archive(self, env):
        save_profile(env, platforms=("tt",), after_success="archive")
        make_package(env[3])
        intake = intake_with(env, self.pubs())
        assert intake.publish(intake.scan()[0].package).stage == "archive"
        assert (env[3] / "archive" / "post_001").is_dir()

    def test_auto_mode_publishes_ready_and_fails_invalid(self, env):
        save_profile(env, platforms=("tt",), mode="auto")
        make_package(env[3], name="good")
        make_package(env[3], name="bad", caption=None)
        tt = FakePublisher("tiktok", ok("T1"))
        intake = intake_with(env, self.pubs(tt=tt))
        results = {r.content_id: r for r in intake.publish_ready()}
        assert results["good"].outcome == "published" and results["bad"].outcome == "invalid"
        assert len(tt.calls) == 1

    def test_scan_is_read_only(self, env):
        save_profile(env)
        make_package(env[3])
        pubs = self.pubs()
        intake = intake_with(env, pubs)
        intake.scan()
        assert (env[3] / "incoming" / "post_001").is_dir()
        assert intake.engine.store.recent_jobs() == []
        with env[0].session() as s:
            assert s.query(ContentItem).count() == 0
        assert all(not p.calls for p in pubs.values())

    def test_content_key_ignores_folder_name_and_caption(self, env):
        make_package(env[3], name="a", caption="one")
        make_package(env[3], name="b", caption="two")
        det = ContentDetector(env[3])
        a, b = det.detect(env[3] / "incoming" / "a", "incoming"), det.detect(env[3] / "incoming" / "b", "incoming")
        assert content_key(a) == content_key(b)


class TestCoverCapabilities:
    def test_youtube_supports_upload_with_limits(self, tmp_path):
        yt = YouTubePublisher()
        (tmp_path / "c.jpg").write_bytes(JPEG)
        (tmp_path / "c.webp").write_bytes(b"RIFF....WEBP")
        big = tmp_path / "big.png"
        with open(big, "wb") as f:
            f.truncate(50 * 1024 * 1024 + 1)
        assert yt.supports_cover_upload() and yt.cover_plan(str(tmp_path / "c.jpg")) == ("upload", "")
        status, reason = yt.cover_plan(str(tmp_path / "c.webp"))
        assert status == "skipped" and "JPEG or PNG" in reason
        status, reason = yt.cover_plan(str(big))
        assert status == "skipped" and "50 MB" in reason

    def test_tiktok_truthful(self, tmp_path):
        tt = TikTokPublisher()
        assert not tt.supports_cover_upload() and tt.supports_cover_timestamp()
        status, reason = tt.cover_plan(str(tmp_path / "c.jpg"))
        assert status == "not_supported" and "video_cover_timestamp_ms" in reason

    def test_instagram_truthful(self, tmp_path):
        ig = InstagramPublisher()
        assert not ig.supports_cover_upload()
        status, reason = ig.cover_plan(str(tmp_path / "c.jpg"))
        assert status == "not_supported" and "Instagram Login" in reason

    def test_no_cover(self):
        assert YouTubePublisher().cover_plan(None) == ("none", "no cover")


# ------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------

class TestContentCli:
    def test_verify_asks_exactly_one_confirmation(self, env, capsys):
        save_profile(env)
        make_package(env[3])
        intake = intake_with(env, TestIntakePublishing().pubs())
        (entry,) = intake.scan()
        with patch("src.cli.content_menu.confirm", return_value=True) as confirm:
            result = verify_and_publish(intake, entry)
        assert confirm.call_count == 1
        assert "Publish to all selected destinations?" in confirm.call_args.args[0]
        assert result.outcome == "published"
        out = capsys.readouterr().out
        assert "DESTINATIONS" in out and "READY TO PUBLISH" in out and "COVER" in out

    def test_verify_decline_publishes_nothing(self, env):
        save_profile(env)
        make_package(env[3])
        pubs = TestIntakePublishing().pubs()
        intake = intake_with(env, pubs)
        with patch("src.cli.content_menu.confirm", return_value=False):
            assert verify_and_publish(intake, intake.scan()[0]) is None
        assert all(not p.calls for p in pubs.values())
        assert (env[3] / "incoming" / "post_001").is_dir()

    def test_inbox_verify_flow(self, env, capsys):
        save_profile(env)
        make_package(env[3])
        intake = intake_with(env, TestIntakePublishing().pubs())
        with patch("builtins.input", side_effect=["1", "y"]), patch("src.cli.content_menu.clear_screen"):
            run_content_inbox(intake)
        out = capsys.readouterr().out
        assert "post_001" in out and "READY" in out and "All destinations published." in out

    def test_inbox_auto_mode_no_prompt(self, env, capsys):
        save_profile(env, mode="auto")
        make_package(env[3])
        intake = intake_with(env, TestIntakePublishing().pubs())
        with patch("builtins.input", side_effect=AssertionError("AUTO must not prompt")), \
                patch("src.cli.content_menu.clear_screen"):
            run_content_inbox(intake)
        assert "All destinations published." in capsys.readouterr().out

    def test_profile_setup_flow(self, env):
        db, accounts, ids, _ = env
        intake = intake_with(env, TestIntakePublishing().pubs())
        # instagram: none; tiktok: 1; youtube: 1,2; tiktok privacy 4 (SELF_ONLY); yt privacy 1;
        # made for kids n; cover y; after success 1; mode 1 (VERIFY); save y
        answers = ["", "1", "1,2", "4", "1", "n", "y", "1", "1", "y"]
        with patch("builtins.input", side_effect=answers):
            profile = edit_profile(intake, accounts)
        assert profile is not None
        saved = ProfileStore(db).load()
        assert saved.accounts["youtube"] == sorted([ids["yt_a"], ids["yt_b"]])
        assert saved.accounts["tiktok"] == [ids["tt"]] and saved.accounts["instagram"] == []
        assert saved.tiktok_privacy_level == "SELF_ONLY" and saved.mode == "verify"

    def test_history_shows_destinations_and_covers(self, env, capsys):
        save_profile(env)
        make_package(env[3])
        intake = intake_with(env, TestIntakePublishing().pubs())
        intake.publish(intake.scan()[0].package)
        with patch("src.cli.content_menu.clear_screen"):
            run_history(intake)
        out = capsys.readouterr().out
        assert "post_001" in out and "Youtube yt_a — PUBLISHED" in out
        assert "Tiktok tt — NOT SUPPORTED" in out

    def test_dry_run_shows_content_without_side_effects(self, env, capsys, monkeypatch):
        import sys

        import main as main_module

        db, _, _, root = env
        save_profile(env)
        make_package(root)
        monkeypatch.setenv("DATABASE_URL", str(db.engine.url))
        monkeypatch.setenv("CONTENT_ROOT", str(root))
        monkeypatch.setattr("main.ENV_FILE", root / "none.env")
        with patch.object(sys, "argv", ["main.py", "--dry-run"]), \
                patch("httpx.Client.request", side_effect=AssertionError("network")):
            assert main_module.main() == 0
        out = capsys.readouterr().out
        assert "Content inbox" in out and "post_001" in out and "DESTINATIONS" in out
        assert (root / "incoming" / "post_001").is_dir()
        with db.session() as s:
            assert s.query(ContentItem).count() == 0
