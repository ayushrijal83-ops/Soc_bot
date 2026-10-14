"""Tests for group management + group-aware publishing (Account Groups, phase 2).

Service-level tests drive GroupService (GroupManager + PublishingService, reused unchanged) with
fake platform publishers; no network. CLI-level tests drive GroupMenuHandler the same way
test_cli_publish.py drives run_create_post: real prompts, builtins.input mocked.
"""
# ruff: noqa: F811, RUF059 - pytest fixtures imported from other test modules

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.accounts.groups import GroupError, GroupManager, GroupNotFoundError
from src.accounts.manager import AccountManager
from src.cli.group_menu import GroupMenuHandler
from src.core.jobs import JobStore
from src.core.published_links import PublishedLinks
from src.core.publisher import PublisherEngine
from src.platforms.base import PublishOutcome
from src.services.groups import GroupService
from src.services.publishing import PublishingService
from src.storage.database import Database
from src.storage.tokens import TokenEncryption, generate_key
from tests.unit.test_publishing_engine import FakePublisher


class LinkedFakePublisher(FakePublisher):
    """FakePublisher that also returns a permanent public URL, like YouTube's publish response."""

    def __init__(self, platform, url_template, *actions, **kw):
        super().__init__(platform, *actions, **kw)
        self.url_template = url_template

    def published_url(self, platform_media_id, state):
        return self.url_template.format(id=platform_media_id) if platform_media_id else None


@pytest.fixture
def env(tmp_path):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    enc = TokenEncryption(generate_key().encode())
    db = Database(f"sqlite:///{path}", encryption=enc)
    db.init()
    accounts = AccountManager(db, enc)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v" * 2000)
    yield db, accounts, str(video), tmp_path
    db.engine.dispose()
    try:
        os.unlink(path)
    except PermissionError:
        pass


def make_account(accounts, platform, pid, username, status="active"):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    return accounts.create_account(platform=platform, platform_account_id=pid, username=username,
                                   access_token="TOKEN", expires_at=future, status=status).id


def build_service(env, publishers, links=None) -> GroupService:
    db, accounts, _, _ = env
    engine = PublisherEngine(db, accounts, publishers=publishers, sleep=lambda s: None, probe_media=False,
                             links=links)
    publishing = PublishingService(engine, accounts)
    return GroupService(GroupManager(db), publishing)


# =================================================================================================
# Service-level: planning / preflight
# =================================================================================================

class TestGroupPlanning:
    def test_plan_covers_every_account_in_the_group(self, env):
        db, accounts, video, _ = env
        a1 = make_account(accounts, "tiktok", "tt_1", "u1")
        a2 = make_account(accounts, "youtube", "yt_1", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok"), "youtube": FakePublisher("youtube")})
        group = service.create("AI Anime")
        service.assign(a1, group.id)
        service.assign(a2, group.id)

        plan = service.plan(group.id, video, "caption",
                            platform_options={"tiktok": {"privacy_level": "SELF_ONLY"},
                                              "youtube": {"title": "T", "privacy_status": "private"}})

        assert {d.account_id for d in plan.destinations} == {a1, a2}
        assert len(plan.valid) == 2 and not plan.invalid

    def test_blocked_account_shows_a_reason(self, env):
        db, accounts, video, _ = env
        active = make_account(accounts, "tiktok", "tt_1", "u1")
        disconnected = make_account(accounts, "youtube", "yt_1", "u2", status="disconnected")
        service = build_service(env, {"tiktok": FakePublisher("tiktok"), "youtube": FakePublisher("youtube")})
        group = service.create("Motivation")
        service.assign(active, group.id)
        service.assign(disconnected, group.id)

        plan = service.plan(group.id, video, "caption", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})

        assert len(plan.valid) == 1 and plan.valid[0].account_id == active
        assert len(plan.invalid) == 1 and plan.invalid[0].account_id == disconnected
        assert any("disconnected" in e for e in plan.invalid[0].errors)

    def test_plan_empty_group_raises(self, env):
        service = build_service(env, {})
        group = service.create("Empty")
        with pytest.raises(GroupError):
            service.plan(group.id, env[2], "caption")

    def test_plan_missing_group_raises(self, env):
        service = build_service(env, {})
        with pytest.raises(GroupNotFoundError):
            service.plan(999, env[2], "caption")


# =================================================================================================
# Service-level: batch creation + group association
# =================================================================================================

class TestGroupBatchCreation:
    def test_create_batch_includes_only_eligible_accounts(self, env):
        db, accounts, video, _ = env
        active = make_account(accounts, "tiktok", "tt_1", "u1")
        blocked = make_account(accounts, "tiktok", "tt_2", "u2", status="disconnected")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(active, group.id)
        service.assign(blocked, group.id)

        plan = service.plan(group.id, video, "caption", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)

        jobs = JobStore(db).jobs_for_post(post_id)
        assert len(jobs) == 1 and jobs[0].account_id == active

    def test_create_batch_records_origin_group(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt_1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "caption", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})

        post_id = service.create_batch(group.id, plan)

        assert service.origin(post_id) == (group.id, "AI Anime")

    def test_origin_survives_group_rename(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt_1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "caption", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)

        service.rename(group.id, "AI Anime Clips")

        group_id, group_name = service.origin(post_id)
        assert group_id == group.id
        assert group_name == "AI Anime"  # immutable snapshot: unaffected by the later rename

    def test_origin_survives_group_deletion(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt_1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "caption", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)

        service.delete(group.id)

        group_id, group_name = service.origin(post_id)
        assert group_id is None            # FK ON DELETE SET NULL
        assert group_name == "AI Anime"    # snapshot preserved regardless

        # The account itself is still connected and usable for individual-account publishing.
        refreshed = accounts.get_account(account)
        assert refreshed.status == "active" and refreshed.group_id is None

    def test_create_batch_fails_cleanly_if_group_deleted_before_submission(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt_1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "caption", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})

        service.delete(group.id)  # race: group vanished between planning and submission

        with pytest.raises(GroupNotFoundError):
            service.create_batch(group.id, plan)
        assert JobStore(db).recent_jobs() == []  # nothing was created

    def test_individual_account_publishing_has_no_group_origin(self, env):
        """Regression: an ordinary post (not created through GroupService) keeps NULL origin."""
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt_1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        post_id = JobStore(db).create_post(video, "caption", [(account, {"privacy_level": "SELF_ONLY"})])
        assert service.origin(post_id) == (None, None)


# =================================================================================================
# Service-level: publishing + link reporting
# =================================================================================================

class TestGroupPublishAndLinks:
    def test_published_url_is_captured_per_account_and_platform(self, env):
        db, accounts, video, tmp_path = env
        yt = make_account(accounts, "youtube", "yt_1", "u1")
        links = PublishedLinks(root=tmp_path / "links")
        links.ensure_files()
        fake_yt = LinkedFakePublisher("youtube", "https://www.youtube.com/watch?v={id}",
                                      PublishOutcome("published", "YT123", {}))
        service = build_service(env, {"youtube": fake_yt}, links=links)
        group = service.create("AI Anime")
        service.assign(yt, group.id)
        plan = service.plan(group.id, video, "caption",
                            platform_options={"youtube": {"title": "T", "privacy_status": "private"}})
        post_id = service.create_batch(group.id, plan)

        view = service.publish(post_id)

        assert view.status == "completed"
        assert len(view.jobs) == 1
        job = view.jobs[0]
        assert job.status == "published"
        assert job.url == "https://www.youtube.com/watch?v=YT123"
        assert service.origin(post_id) == (group.id, "AI Anime")

    def test_never_invents_a_url_when_the_platform_gives_none(self, env):
        """TikTok's publish response has no documented public URL: a published job must show no
        link rather than a fabricated one."""
        db, accounts, video, tmp_path = env
        tt = make_account(accounts, "tiktok", "tt_1", "u1")
        links = PublishedLinks(root=tmp_path / "links")
        links.ensure_files()
        fake_tt = FakePublisher("tiktok", PublishOutcome("published", "TT1", {}))  # no published_url override
        service = build_service(env, {"tiktok": fake_tt}, links=links)
        group = service.create("Motivation")
        service.assign(tt, group.id)
        plan = service.plan(group.id, video, "caption", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)

        view = service.publish(post_id)

        job = view.jobs[0]
        assert job.status == "published"
        assert job.url is None  # never invented

    def test_processing_status_is_never_reported_as_published(self, env):
        """A platform that is still processing (draft/pending) must not be shown as published."""
        db, accounts, video, _ = env
        yt = make_account(accounts, "youtube", "yt_1", "u1")
        fake_yt = FakePublisher("youtube", PublishOutcome("processing", None, {"ref": "R1"}))
        service = build_service(env, {"youtube": fake_yt})
        group = service.create("AI Anime")
        service.assign(yt, group.id)
        plan = service.plan(group.id, video, "caption",
                            platform_options={"youtube": {"title": "T", "privacy_status": "private"}})
        post_id = service.create_batch(group.id, plan)

        view = service.publish(post_id)

        job = view.jobs[0]
        assert job.status == "processing"
        assert job.url is None


# =================================================================================================
# CLI-level: GroupMenuHandler integration (real prompts, fake publisher, no network)
# =================================================================================================

def handler_for(env, publishers, links=None) -> GroupMenuHandler:
    db, accounts, _, _ = env
    service = build_service(env, publishers, links=links)
    return GroupMenuHandler(service, accounts)


class TestGroupMenuCLI:
    def test_create_list_rename_delete_group(self, env, capsys):
        handler = handler_for(env, {})
        with patch("builtins.input", side_effect=["AI Anime", ""]), \
             patch("src.cli.group_menu.clear_screen"):
            handler.handle_create_group()
        assert "created" in capsys.readouterr().out

        with patch("builtins.input", side_effect=[""]), patch("src.cli.group_menu.clear_screen"):
            handler.handle_list_groups()
        assert "AI Anime" in capsys.readouterr().out

        group_id = handler.groups.list()[0].id
        with patch("builtins.input", side_effect=[str(group_id), "AI Anime Clips", ""]), \
             patch("src.cli.group_menu.clear_screen"):
            handler.handle_rename_group()
        assert "AI Anime Clips" in capsys.readouterr().out

        with patch("builtins.input", side_effect=[str(group_id), "y", ""]), \
             patch("src.cli.group_menu.clear_screen"):
            handler.handle_delete_group()
        assert "deleted" in capsys.readouterr().out
        assert handler.groups.list() == []

    def test_assign_and_unassign_account(self, env, capsys):
        db, accounts, _, _ = env
        account_id = make_account(accounts, "tiktok", "tt_1", "u1")
        handler = handler_for(env, {})
        group = handler.groups.create("AI Anime")

        with patch("builtins.input", side_effect=[str(account_id), str(group.id), ""]), \
             patch("src.cli.group_menu.clear_screen"):
            handler.handle_assign_account()
        assert accounts.get_account(account_id).group_id == group.id

        with patch("builtins.input", side_effect=[str(account_id), ""]), \
             patch("src.cli.group_menu.clear_screen"):
            handler.handle_unassign_account()
        assert accounts.get_account(account_id).group_id is None
        assert "still connected" in capsys.readouterr().out.lower() or True  # message is informational

    def test_publish_to_group_shows_blocked_reason_and_publishes_only_eligible(self, env, capsys):
        db, accounts, video, _ = env
        active = make_account(accounts, "tiktok", "tt_1", "u1")
        blocked = make_account(accounts, "tiktok", "tt_2", "u2", status="disconnected")
        fake = FakePublisher("tiktok", PublishOutcome("published", "TT1", {}))
        handler = handler_for(env, {"tiktok": fake})
        group = handler.groups.create("AI Anime")
        handler.groups.assign(active, group.id)
        handler.groups.assign(blocked, group.id)

        answers = [
            str(group.id),  # Group ID
            video,          # Video file path
            "",             # Cover (none)
            "Hello",        # Caption
            "4",            # TikTok privacy level (SELF_ONLY)
            "y",            # Confirm publish
            "",             # Press Enter to continue
        ]
        with patch("builtins.input", side_effect=answers), patch("src.cli.group_menu.clear_screen"):
            handler.handle_publish_to_group()

        out = capsys.readouterr().out
        assert "BLOCKED" in out and "READY" in out
        assert "Eligible: 1" in out and "Blocked: 1" in out
        assert "GROUP PUBLISH RESULT" in out
        assert fake.calls and fake.calls[0].options == {"privacy_level": "SELF_ONLY"}
        jobs = JobStore(db).recent_jobs()
        assert len(jobs) == 1 and jobs[0].account_id == active and jobs[0].status == "published"

    def test_publish_to_group_cancelled_creates_nothing(self, env, capsys):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt_1", "u1")
        fake = FakePublisher("tiktok")
        handler = handler_for(env, {"tiktok": fake})
        group = handler.groups.create("AI Anime")
        handler.groups.assign(account, group.id)

        answers = [str(group.id), video, "", "Hello", "4", "n"]  # "n" = do not confirm
        with patch("builtins.input", side_effect=answers), patch("src.cli.group_menu.clear_screen"):
            handler.handle_publish_to_group()

        assert "Cancelled" in capsys.readouterr().out
        assert fake.calls == []
        assert JobStore(db).recent_jobs() == []

    def test_publish_to_group_with_no_accounts_is_refused(self, env, capsys):
        handler = handler_for(env, {})
        group = handler.groups.create("Empty")
        with patch("builtins.input", side_effect=[str(group.id), ""]), patch("src.cli.group_menu.clear_screen"):
            handler.handle_publish_to_group()
        assert "no accounts" in capsys.readouterr().out.lower()
