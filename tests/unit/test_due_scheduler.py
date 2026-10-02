# ruff: noqa: F811, DTZ001 - fixtures imported from other modules; naive datetimes are deliberate wall-clock input
"""V2.2 phase 3: one-pass due scheduler (UTC only; injected clock; publishes only via PublishingService)."""

import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import update

from src.services.due_scheduler import MISSED_GRACE, DueScheduler
from src.services.scheduling import SchedulingService
from src.storage.database import Post
from tests.unit.test_publishing_engine import env  # noqa: F401
from tests.unit.test_tui import services  # noqa: F401

UTC = timezone.utc
T0 = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)       # when posts are scheduled
S = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)       # their scheduled time


@pytest.fixture
def calls(services, monkeypatch):
    """Every PublishingService.publish_batch call (the scheduler's only way to publish)."""
    seen, lock, real = [], threading.Lock(), services.publishing.publish_batch

    def spy(post_id, *args, **kwargs):
        with lock:
            seen.append(post_id)
        return real(post_id, *args, **kwargs)

    monkeypatch.setattr(services.publishing, "publish_batch", spy)
    return seen


def make(services, at=S, accounts=1):
    sched = SchedulingService(services.publishing, clock=lambda: T0)
    ids = [a.id for a in services.accounts.list("instagram")][:accounts]
    p = services.publishing.plan(services.video, "hello", None, ids)
    local = at.astimezone(UTC).replace(tzinfo=None)
    return sched.schedule(p, local, zone="UTC", include_already_published=True).post_id


def status(services, post_id):
    with services.engine.store.database.session() as session:
        return session.get(Post, post_id).schedule_status


def history(services, post_id):
    with services.engine.store.database.session() as session:
        return [h["action"] for h in json.loads(session.get(Post, post_id).schedule_json)["history"]]


def scheduler(services, **kw):
    return DueScheduler(services.publishing, **kw)


def kinds(result):
    return [e.kind for e in result.events]


# --- basic ------------------------------------------------------------------------------------

def test_nothing_scheduled(services, calls):
    result = scheduler(services).run_due(S)
    assert (result.released, result.missed, result.events, calls) == ([], [], [], [])
    assert result.started_at == S and result.finished_at is not None


@pytest.mark.parametrize("now, expected", [
    (S - timedelta(minutes=1), "scheduled"),             # before: future
    (S, "released"),                                     # exactly at scheduled_at: due
    (S + MISSED_GRACE, "released"),                      # exactly at the end of the grace: still due
    (S + MISSED_GRACE + timedelta(seconds=1), "missed"),  # strictly after: missed
])
def test_due_and_missed_boundaries(services, calls, now, expected):
    post_id = make(services)
    result = scheduler(services).run_due(now)
    assert status(services, post_id) == expected
    assert calls == ([post_id] if expected == "released" else [])
    if expected == "missed":
        assert result.missed == [post_id] and history(services, post_id) == ["scheduled", "missed"]
        assert {j.status for j in services.engine.store.jobs_for_post(post_id)} == {"pending"}


def test_due_posts_released_and_published_oldest_first_one_at_a_time(services, calls):
    late = make(services, S + timedelta(minutes=30))
    first = make(services, S)
    tie = make(services, S)           # same time: lower id first
    order = []
    run = scheduler(services).run_due(S + timedelta(minutes=45), on_event=lambda e: order.append((e.kind, e.post_id)))
    assert calls == [first, tie, late] and run.released == [first, tie, late] and run.published == [first, tie, late]
    # release -> publish per post, never all releases first
    assert [k for k, _ in order] == ["released", "publishing_started", "published"] * 3
    assert all(status(services, p) == "released" for p in (first, tie, late))
    assert history(services, first) == ["scheduled", "released"]


def test_missed_posts_are_never_published(services, calls):
    old = make(services, S - timedelta(hours=3))
    due = make(services, S)
    result = scheduler(services).run_due(S + timedelta(minutes=5))
    assert result.missed == [old] and calls == [due]
    again = scheduler(services).run_due(S + timedelta(minutes=10))  # a missed post stays missed
    assert status(services, old) == "missed" and again.released == [] and calls == [due]


def test_cancelled_released_and_ordinary_posts_are_ignored(services, calls):
    sched = SchedulingService(services.publishing, clock=lambda: T0)
    cancelled = make(services)
    sched.cancel(cancelled)
    released = make(services)
    sched.publish_now(released)
    ordinary = services.publishing.create_batch(
        services.publishing.plan(services.video, "now", None, [services.accounts.list("instagram")[0].id]),
        include_already_published=True)
    result = scheduler(services).run_due(S + timedelta(days=2))  # far past every grace window
    assert calls == [] and result.missed == [] and result.released == []
    assert (status(services, cancelled), status(services, released), status(services, ordinary)) == \
        ("cancelled", "released", None)


# --- publishing -------------------------------------------------------------------------------

def test_publishes_only_through_publishing_service(services):
    post_id = make(services, accounts=3)
    used = []
    stub = SimpleNamespace(store=services.publishing.store,
                           publish_batch=lambda pid: used.append(pid) or SimpleNamespace(status="completed"))
    services.engine.publish_post = lambda *a, **k: pytest.fail("scheduler must not call the engine directly")
    result = DueScheduler(stub).run_due(S)
    assert used == [post_id] and result.published == [post_id]


def test_multi_destination_post_released_once_and_published_normally(services, calls):
    post_id = make(services, accounts=3)
    result = scheduler(services).run_due(S)
    assert calls == [post_id] and result.published == [post_id]
    assert {j.status for j in services.engine.store.jobs_for_post(post_id)} == {"published"}
    assert scheduler(services).run_due(S + timedelta(minutes=1)).released == [] and calls == [post_id]


@pytest.mark.parametrize("outcome", ["exception", "failed_batch"])
def test_publishing_failure_keeps_post_released(services, monkeypatch, outcome):
    post_id = make(services)
    other = make(services, S + timedelta(minutes=1))

    def broken(pid, *args, **kwargs):
        if pid == post_id and outcome == "exception":
            raise RuntimeError("engine exploded")
        return SimpleNamespace(status="failed" if pid == post_id else "completed")

    monkeypatch.setattr(services.publishing, "publish_batch", broken)
    result = scheduler(services).run_due(S + timedelta(minutes=2))
    assert status(services, post_id) == "released" and result.failed == [post_id] and result.published == [other]
    if outcome == "exception":
        assert any(e.post_id == post_id and "engine exploded" in e.message for e in result.errors)
    assert scheduler(services).run_due(S + timedelta(minutes=3)).released == []  # never scheduled again


# --- concurrency / races ----------------------------------------------------------------------

def test_two_scheduler_runs_race_one_winner(services, calls):
    post_id = make(services)
    barrier, results = threading.Barrier(2), []

    def run():
        barrier.wait()
        results.append(scheduler(services).run_due(S))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(len(r.released) for r in results) == [0, 1] and calls == [post_id]
    assert history(services, post_id).count("released") == 1


def test_many_posts_two_runs_each_published_once(services, calls):
    posts = [make(services, S + timedelta(minutes=i)) for i in range(3)]
    barrier = threading.Barrier(2)

    def run():
        barrier.wait()
        scheduler(services).run_due(S + timedelta(minutes=10))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(calls) == posts


def race(services, monkeypatch, sched_obj, action):
    """Run ``action(post_id)`` right after the scheduler picked a candidate, before its release UPDATE."""
    real = sched_obj._next_due

    def picked_then_changed(now, exclude):
        candidate = real(now, exclude)
        if candidate is not None:
            action(candidate[0])
        return candidate

    monkeypatch.setattr(sched_obj, "_next_due", picked_then_changed)


@pytest.mark.parametrize("who", ["cancel", "reschedule", "other_scheduler"])
def test_scheduler_loses_race_and_does_not_publish(services, calls, monkeypatch, who):
    post_id = make(services)
    sched = SchedulingService(services.publishing, clock=lambda: T0)
    actions = {
        "cancel": sched.cancel,
        "reschedule": lambda pid: sched.reschedule(pid, datetime(2026, 10, 5, 9, 0), zone="UTC"),
        "other_scheduler": lambda pid: sched.publish_now(pid),  # someone else released it first
    }
    s = scheduler(services)
    race(services, monkeypatch, s, actions[who])
    result = s.run_due(S)
    assert result.skipped == [post_id] and result.released == [] and calls == []
    assert status(services, post_id) == {"cancel": "cancelled", "reschedule": "scheduled",
                                         "other_scheduler": "released"}[who]


def test_cancel_after_release_is_rejected(services, calls):
    post_id = make(services)
    scheduler(services).run_due(S)
    with pytest.raises(Exception, match="already been released"):
        SchedulingService(services.publishing, clock=lambda: T0).cancel(post_id)


# --- missed transition is conditional ---------------------------------------------------------

def test_missed_transition_never_overwrites_a_concurrent_change(services, calls):
    post_id = make(services)
    s = scheduler(services)
    with services.engine.store.database.session() as session:
        raw = session.get(Post, post_id).schedule_json
    SchedulingService(services.publishing, clock=lambda: T0).cancel(post_id)  # happens after the read
    late = S + timedelta(hours=2)
    outcome = s._swap(post_id, raw, "missed", late, S, Post.scheduled_at < s._naive(late - MISSED_GRACE),
                      lambda e: None)
    assert outcome == "lost" and status(services, post_id) == "cancelled"


# --- clock ------------------------------------------------------------------------------------

def test_clock_handling(services, calls):
    post_id = make(services)
    with pytest.raises(ValueError, match="timezone-aware"):
        scheduler(services).run_due(datetime(2026, 10, 2, 12, 0))
    tokyo = S.astimezone(ZoneInfo("Asia/Tokyo"))  # 21:00 JST = 12:00 UTC
    result = scheduler(services).run_due(tokyo)
    assert result.started_at == S and result.started_at.tzinfo is UTC and calls == [post_id]
    later = make(services, S + timedelta(minutes=5))
    assert scheduler(services, clock=lambda: S + timedelta(minutes=5)).run_due().released == [later]


# --- defensive ----------------------------------------------------------------------------------

def test_malformed_rows_reported_not_published(services, calls):
    no_time = make(services)
    bad_json = make(services)
    good = make(services, S + timedelta(minutes=1))
    with services.engine.store.database.session() as session:
        session.execute(update(Post).where(Post.id == no_time).values(scheduled_at=None))
        session.execute(update(Post).where(Post.id == bad_json).values(schedule_json="{not json"))
        session.commit()
    result = scheduler(services).run_due(S + timedelta(minutes=2))
    assert calls == [good] and result.published == [good]
    assert {e.post_id for e in result.errors} == {no_time, bad_json}
    assert status(services, no_time) == status(services, bad_json) == "scheduled"  # not repaired, not published
    assert result.skipped == []


# --- callback -----------------------------------------------------------------------------------

def test_callback_receives_events_and_errors_are_contained(services, calls):
    missed = make(services, S - timedelta(hours=2))
    due = make(services, S)
    seen = []

    def explode(event):
        seen.append(event.kind)
        raise RuntimeError("UI bug")

    result = scheduler(services).run_due(S + timedelta(minutes=1), on_event=explode)
    assert seen == ["missed", "released", "publishing_started", "published"]
    assert status(services, missed) == "missed" and status(services, due) == "released" and calls == [due]
    assert len([e for e in result.errors if "callback" in e.message]) == 4
    assert kinds(result) == seen  # the scheduler's own record is complete


def test_without_callback_events_are_still_recorded(services, calls):
    due = make(services)
    result = scheduler(services).run_due(S)
    assert kinds(result) == ["released", "publishing_started", "published"] and result.events[0].post_id == due
    assert result.events[0].scheduled_at == S


def test_registered_in_app_services(services):
    assert isinstance(services.due_scheduler, DueScheduler)
