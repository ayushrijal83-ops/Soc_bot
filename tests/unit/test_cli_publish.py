"""Tests for the minimal Phase 4 CLI (create post / queue). Publishers are fakes; no network."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.accounts.manager import AccountManager
from src.cli.publish_menu import run_create_post, run_publishing_queue
from src.core.jobs import JobStore
from src.core.publisher import PublisherEngine
from src.platforms.base import PublishOutcome
from src.storage.database import Database
from src.storage.tokens import TokenEncryption, generate_key
from tests.unit.test_publishing_engine import FakePublisher


@pytest.fixture
def setup(tmp_path):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    enc = TokenEncryption(generate_key().encode())
    db = Database(f"sqlite:///{path}", encryption=enc)
    db.init()
    accounts = AccountManager(db, enc)
    accounts.create_account(platform="tiktok", platform_account_id="o1", username="tt_user", access_token="TOKEN",
                            expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 100)
    yield db, accounts, str(video)
    db.engine.dispose()
    try:
        os.unlink(path)
    except PermissionError:
        pass


def run(setup, answers, fake):
    db, accounts, _ = setup
    engine = PublisherEngine(db, accounts, publishers={"tiktok": fake}, sleep=lambda s: None, probe_media=False)
    with patch("builtins.input", side_effect=answers), patch("src.cli.publish_menu.clear_screen"):
        run_create_post(accounts, engine)
    return engine


def test_create_post_publishes_after_confirmation(setup, capsys):
    fake = FakePublisher("tiktok", PublishOutcome("published", "TT1", {}))
    # video, caption, accounts, privacy (4 = SELF_ONLY), confirm
    engine = run(setup, [setup[2], "Hello", "1", "4", "y"], fake)
    out = capsys.readouterr().out
    assert "PUBLISHING PLAN" in out and "READY" in out
    assert "PUBLISHED" in out and "1 published" in out
    assert fake.calls[0].options == {"privacy_level": "SELF_ONLY"}
    jobs = engine.store.recent_jobs()
    assert jobs[0].status == "published" and jobs[0].platform_media_id == "TT1"


def test_create_post_cancel_publishes_nothing(setup, capsys):
    fake = FakePublisher("tiktok")
    engine = run(setup, [setup[2], "Hello", "1", "4", "n"], fake)
    assert fake.calls == []
    assert engine.store.recent_jobs() == []
    assert "Nothing was published" in capsys.readouterr().out


def test_create_post_blocked_plan_is_not_published(setup, capsys):
    fake = FakePublisher("tiktok", validate_errors=["caption too long"])
    engine = run(setup, [setup[2], "Hello", "1", "4"], fake)
    out = capsys.readouterr().out
    assert "BLOCKED" in out and "caption too long" in out
    assert engine.store.recent_jobs() == [] and fake.calls == []


def test_create_post_rejects_missing_video(setup, capsys):
    run(setup, ["does_not_exist.mp4"], FakePublisher("tiktok"))
    assert "not found" in capsys.readouterr().out


def test_queue_lists_jobs(setup, capsys):
    db, accounts, video = setup
    JobStore(db).create_post(video, "c", [(accounts.list_accounts()[0].id, {"privacy_level": "SELF_ONLY"})])
    engine = PublisherEngine(db, accounts, publishers={"tiktok": FakePublisher("tiktok")}, probe_media=False)
    with patch("builtins.input", side_effect=["3"]), patch("src.cli.publish_menu.clear_screen"):
        run_publishing_queue(engine)
    out = capsys.readouterr().out
    assert "PENDING" in out and "tiktok" in out
