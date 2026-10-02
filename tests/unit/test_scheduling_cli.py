# ruff: noqa: F811 - pytest fixtures imported from other test modules; local wall-clock dates
"""V2.2 phase 6: scheduling in the classic CLI (Create Post -> Schedule, Queue -> scheduled post actions)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.cli.publish_menu import run_publishing_queue
from src.core import publish_lock
from src.core.jobs import JobStore
from src.core.publish_lock import BUSY_MESSAGE
from src.core.publisher import PublisherEngine
from src.platforms.base import PublishOutcome
from src.services.publishing import PublishingService
from src.services.scheduling import SchedulingError, SchedulingService
from tests.unit.test_cli_publish import run, setup  # noqa: F401
from tests.unit.test_publish_lock import held_by_other_process
from tests.unit.test_publishing_engine import FakePublisher
from tests.unit.test_scheduling_ui import next_gap

UTC = timezone.utc
CREATE = ["", "Hello", "", "1", "", "4"]   # no cover, caption, Global audience, account 1, continue, SELF_ONLY


@pytest.fixture(autouse=True)
def _no_user_timezone(monkeypatch):
    monkeypatch.delenv("SOC_BOT_TIMEZONE", raising=False)


def day(days=1):
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%d")


def sched_of(setup):
    db, accounts, _ = setup
    engine = PublisherEngine(db, accounts, publishers={"tiktok": FakePublisher("tiktok")}, probe_media=False)
    return SchedulingService(PublishingService(engine, accounts))


def create(setup, answers, fake=None):
    fake = fake or FakePublisher("tiktok")
    return run(setup, [setup[2], *CREATE, *answers], fake), fake


def queue(setup, answers, fake=None):
    db, accounts, _ = setup
    engine = PublisherEngine(db, accounts, publishers={"tiktok": fake or FakePublisher("tiktok")}, probe_media=False)
    with patch("builtins.input", side_effect=answers), patch("src.cli.publish_menu.clear_screen"):
        run_publishing_queue(engine)
    return engine


def scheduled(setup):
    return sched_of(setup).scheduled(("scheduled", "missed", "cancelled"))


# --- Create -> Now / Schedule -----------------------------------------------------------------------

def test_create_now_unchanged(setup, capsys):
    _, fake = create(setup, ["1"], FakePublisher("tiktok", PublishOutcome("published", "TT1", {})))
    assert len(fake.calls) == 1 and "1 published" in capsys.readouterr().out and scheduled(setup) == []


def test_create_cancel(setup, capsys):
    engine, fake = create(setup, ["3"])
    assert fake.calls == [] and engine.store.recent_jobs() == [] and "Nothing was published" in capsys.readouterr().out


def test_create_schedule_custom_time(setup, capsys):
    # Publish 2 = Schedule; When 1 = Custom (Global audience: no suggestions); date; time; zone; confirm SCHEDULE
    engine, fake = create(setup, ["2", "1", day(), "19:30", "Asia/Tokyo", "1"])
    out = capsys.readouterr().out
    (view,) = scheduled(setup)
    assert view.status == "scheduled" and view.zone == "Asia/Tokyo" and view.local.strftime("%H:%M") == "19:30"
    assert "Timezone: Asia/Tokyo" in out and "UTC:" in out and f"Post #{view.post_id} scheduled" in out
    assert fake.calls == [] and {j.status for j in engine.store.recent_jobs()} == {"pending"}
    assert engine.store.open_post_ids() == []  # held: not resumable


def test_create_schedule_default_zone_is_resolved_by_service(setup, capsys):
    create(setup, ["2", "1", day(), "08:00", "", "1"])
    (view,) = scheduled(setup)
    assert (view.zone, view.zone_source) == ("UTC", "utc") and "Timezone: UTC (utc)" in capsys.readouterr().out


def test_create_schedule_from_suggestion(setup, capsys):
    # audience 8 = Japan (built-in order) -> suggestions; When 1 = first suggestion; confirm SCHEDULE
    answers = [setup[2], "", "Hello", "8", "1", "", "4", "2", "1", "1"]
    with patch("builtins.input", side_effect=answers), patch("src.cli.publish_menu.clear_screen"):
        from src.cli.publish_menu import run_create_post

        db, accounts, _ = setup
        run_create_post(accounts, PublisherEngine(db, accounts, publishers={"tiktok": FakePublisher("tiktok")},
                                                  probe_media=False))
    (view,) = scheduled(setup)
    assert view.source == "suggestion" and view.zone == "Asia/Tokyo" and view.suggestion["start_utc"]
    assert view.scheduled_at.strftime("%Y-%m-%dT%H:%M:%SZ") == view.suggestion["start_utc"]
    assert "suggested" in capsys.readouterr().out


@pytest.mark.parametrize("case, expected", [
    ("zone", "Unknown timezone"), ("gap", "does not exist"), ("soon", "at least 2 minutes"),
    ("far", "within 365 days"), ("format", "YYYY-MM-DD")])
def test_create_schedule_errors_then_cancel(setup, capsys, case, expected):
    now = datetime.now(UTC)
    first = {"zone": [day(), "19:30", "Mars/Olympus"], "gap": [next_gap().strftime("%Y-%m-%d"), "02:30",
             "America/New_York"], "soon": [now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), ""],
             "far": [day(400), "10:00", ""], "format": ["15/10/2026", "19:30", ""]}[case]
    # Schedule; Custom; bad input -> error, back to "When"; 2 = Cancel
    engine, fake = create(setup, ["2", "1", *first, "2"])
    out = capsys.readouterr().out
    assert expected in out and "Traceback" not in out and "Nothing was scheduled" in out
    assert scheduled(setup) == [] and engine.store.recent_jobs() == [] and fake.calls == []


def test_confirmation_back_returns_to_choice(setup):
    create(setup, ["2", "1", day(), "19:30", "", "2", "1", day(2), "07:00", "", "1"])
    (view,) = scheduled(setup)
    assert view.local.strftime("%H:%M") == "07:00"


# --- Queue -> scheduled posts ----------------------------------------------------------------------

def make_scheduled(setup, hour="19:30"):
    create(setup, ["2", "1", day(), hour, "", "1"])
    return scheduled(setup)[-1]


def test_queue_lists_scheduled_separately(setup, capsys):
    view = make_scheduled(setup)
    capsys.readouterr()
    queue(setup, ["4"])
    out = capsys.readouterr().out
    assert "SCHEDULED (waiting for their time, not ready to publish)" in out and f"#{view.post_id}" in out
    assert f"BATCH #{view.post_id}" not in out  # not shown as an ordinary pending batch


def test_queue_cancel(setup, capsys):
    view = make_scheduled(setup)
    queue(setup, ["3", str(view.post_id), "3", "y"])
    assert sched_of(setup).view(view.post_id).status == "cancelled"
    assert "Schedule cancelled" in capsys.readouterr().out


def test_queue_reschedule(setup):
    view = make_scheduled(setup)
    jobs = [j.id for j in JobStore(setup[0]).jobs_for_post(view.post_id)]
    queue(setup, ["3", str(view.post_id), "1", "1", day(2), "06:45", "", "1"])
    moved = sched_of(setup).view(view.post_id)
    assert moved.local.strftime("%H:%M") == "06:45" and [h["action"] for h in moved.history] == ["scheduled",
                                                                                                  "rescheduled"]
    assert [j.id for j in JobStore(setup[0]).jobs_for_post(view.post_id)] == jobs


def test_queue_publish_now(setup, capsys):
    view = make_scheduled(setup)
    fake = FakePublisher("tiktok", PublishOutcome("published", "TT1", {}))
    queue(setup, ["3", str(view.post_id), "2", "y"], fake)
    assert sched_of(setup).view(view.post_id).status == "released" and len(fake.calls) == 1
    assert "published" in capsys.readouterr().out.lower()


def test_queue_publish_now_lock_busy(setup, capsys):
    view = make_scheduled(setup)
    fake = FakePublisher("tiktok")
    with held_by_other_process(publish_lock.LOCK_PATH):
        queue(setup, ["3", str(view.post_id), "2", "y"], fake)
    out = capsys.readouterr().out
    assert BUSY_MESSAGE in out and "PublishLockBusy" not in out and fake.calls == []
    assert sched_of(setup).view(view.post_id).status == "scheduled"  # not released


def test_queue_conflict_is_friendly(setup, capsys, monkeypatch):
    view = make_scheduled(setup)

    def conflict(self, post_id):
        raise SchedulingError("conflict", "x")

    monkeypatch.setattr(SchedulingService, "cancel", conflict)
    queue(setup, ["3", str(view.post_id), "3", "y"])
    out = capsys.readouterr().out
    assert "changed before your action completed" in out and "Traceback" not in out
    assert sched_of(setup).view(view.post_id).status == "scheduled"


def test_queue_unknown_or_unscheduled_post(setup, capsys):
    db, accounts, video = setup
    ordinary = JobStore(db).create_post(video, "x", [(accounts.list_accounts()[0].id, {"privacy_level": "SELF_ONLY"})])
    queue(setup, ["3", "999"])
    queue(setup, ["3", str(ordinary)])
    out = capsys.readouterr().out
    assert "no longer exists" in out and "only scheduled or missed posts" in out
