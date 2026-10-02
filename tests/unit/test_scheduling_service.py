# ruff: noqa: F811, DTZ001 - fixtures imported from other modules; naive datetimes are deliberate wall-clock input
"""V2.2 phase 2: SchedulingService (state changes only; nothing publishes, no scheduler)."""

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from src.core.publisher import PublisherEngine
from src.services.publishing import PublishingService
from src.services.scheduling import OVERLAP_NOTE, SchedulingError, SchedulingService
from src.storage.database import Database, Post
from tests.unit.test_publishing_engine import env  # noqa: F401
from tests.unit.test_tui import services  # noqa: F401

UTC = timezone.utc
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def sched(services, monkeypatch):
    monkeypatch.delenv("SOC_BOT_TIMEZONE", raising=False)
    return SchedulingService(services.publishing, clock=lambda: NOW)


def plan(services, audience=None, accounts=2):
    ids = [a.id for a in services.accounts.list("instagram")][:accounts]
    p = services.publishing.plan(services.video, "hello", None, ids)
    if audience:
        p.audience_id = next(a.id for a in services.audiences.list() if a.name == audience).__int__()
    return p


def code_of(excinfo):
    return excinfo.value.code


def set_status(services, post_id, status):
    with services.engine.store.database.session() as session:
        session.execute(update(Post).where(Post.id == post_id).values(schedule_status=status))
        session.commit()


def job_ids(services, post_id):
    return [j.id for j in services.engine.store.jobs_for_post(post_id)]


def stored_json(services, post_id):
    with services.engine.store.database.session() as session:
        return session.get(Post, post_id).schedule_json


# --- schedule ---------------------------------------------------------------------------------

def test_manual_schedule_is_held_and_publishes_nothing(services, sched):
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), zone="Asia/Tokyo")
    assert view.status == "scheduled" and view.scheduled_at == datetime(2026, 10, 15, 10, 30, tzinfo=UTC)
    assert view.zone == "Asia/Tokyo" and view.zone_source == "explicit" and view.source == "manual"
    assert view.local.strftime("%Y-%m-%d %H:%M %Z") == "2026-10-15 19:30 JST"
    assert view.destinations == 2 and view.job_counts == {"pending": 2} and not view.ready_to_publish
    data = json.loads(stored_json(services, view.post_id))
    assert {k: data[k] for k in ("source", "local", "zone", "fold", "utc")} == {
        "source": "manual", "local": "2026-10-15T19:30", "zone": "Asia/Tokyo", "fold": 0, "utc": "2026-10-15T10:30:00Z"}
    assert data["tzdata_version"] and data["history"] == [
        {"at": "2026-10-02T12:00:00Z", "action": "scheduled", "from_utc": None, "to_utc": "2026-10-15T10:30:00Z",
         "source": "manual", "zone": "Asia/Tokyo"}]
    assert services.fake.calls == [] and services.engine.store.open_post_ids() == []


def test_explicit_zone_beats_audience(services, sched):
    view = sched.schedule(plan(services, "Japan"), datetime(2026, 10, 15, 19, 30), zone="Europe/London")
    assert view.zone == "Europe/London" and view.zone_source == "explicit"


@pytest.mark.parametrize("audience, zone, note", [("Japan", "Asia/Tokyo", False),
                                                  ("United States", "America/New_York", True)])
def test_audience_primary_zone(services, sched, audience, zone, note):
    p = plan(services, audience)
    when = sched.resolve_time(datetime(2026, 10, 15, 19, 30), services.audiences.get(p.audience_id).snapshot())
    assert (when.zone, when.zone_source) == (zone, "audience_primary")
    assert any("Primary audience timezone" in n for n in when.notes) is note  # never hidden for multi-zone
    assert sched.schedule(p, datetime(2026, 10, 15, 19, 30)).zone == zone


def test_env_zone_then_utc_fallback(services, sched, monkeypatch):
    global_plan = plan(services, "Global")  # global strategy: no audience zone
    monkeypatch.setenv("SOC_BOT_TIMEZONE", "Europe/Paris")
    view = sched.schedule(global_plan, datetime(2026, 10, 15, 19, 30))
    assert (view.zone, view.zone_source) == ("Europe/Paris", "env")
    assert view.scheduled_at == datetime(2026, 10, 15, 17, 30, tzinfo=UTC)
    monkeypatch.setenv("SOC_BOT_TIMEZONE", "Not/AZone")
    assert sched.resolve_zone(None)[:2] == ("UTC", "utc")
    monkeypatch.delenv("SOC_BOT_TIMEZONE")
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), include_already_published=True)
    assert (view.zone, view.zone_source, view.scheduled_at) == ("UTC", "utc", datetime(2026, 10, 15, 19, 30, tzinfo=UTC))


def test_range_boundaries(services, sched):
    assert sched.resolve_time(datetime(2026, 10, 2, 12, 2), zone="UTC").utc == NOW + timedelta(minutes=2)
    with pytest.raises(SchedulingError, match="Scheduling time must be at least 2 minutes in the future.") as e:
        sched.resolve_time(datetime(2026, 10, 2, 12, 1), zone="UTC")
    assert code_of(e) == "too_soon"
    assert sched.resolve_time(datetime(2027, 10, 2, 12, 0), zone="UTC").utc == NOW + timedelta(days=365)
    with pytest.raises(SchedulingError) as e:
        sched.resolve_time(datetime(2027, 10, 2, 12, 1), zone="UTC")
    assert code_of(e) == "too_far"
    with pytest.raises(SchedulingError) as e:
        sched.resolve_time(datetime(2026, 10, 15, 19, 30, tzinfo=UTC), zone="UTC")
    assert code_of(e) == "invalid_input"
    with pytest.raises(SchedulingError) as e:
        sched.resolve_time(datetime(2026, 10, 15, 19, 30), zone="Mars/Olympus")
    assert code_of(e) == "invalid_zone"
    tight = SchedulingService(services.publishing, clock=lambda: NOW, min_lead=timedelta(hours=1),
                              max_ahead=timedelta(days=1))
    with pytest.raises(SchedulingError, match="60 minutes"):
        tight.resolve_time(datetime(2026, 10, 2, 12, 30), zone="UTC")


# --- DST --------------------------------------------------------------------------------------

@pytest.mark.parametrize("wall, zone", [(datetime(2027, 3, 14, 2, 30), "America/New_York"),
                                        (datetime(2027, 3, 28, 1, 30), "Europe/London")])
def test_dst_gap_rejected_not_shifted(services, sched, wall, zone):
    with pytest.raises(SchedulingError, match="does not exist") as e:
        sched.schedule(plan(services), wall, zone=zone)
    assert code_of(e) == "dst_gap" and services.publishing.batches() == []


def test_dst_overlap_first_occurrence_with_note(services, sched):
    view = sched.schedule(plan(services), datetime(2026, 11, 1, 1, 30), zone="America/New_York")
    assert view.scheduled_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT, not EST
    assert view.fold == 0 and view.dst_overlap and view.overlap_note == OVERLAP_NOTE
    assert json.loads(stored_json(services, view.post_id))["dst_overlap"] is True
    assert sched.view(view.post_id).overlap_note == OVERLAP_NOTE  # survives a reload


# --- recommendations --------------------------------------------------------------------------

def test_schedule_from_suggestion_uses_start_utc_exactly(services, sched):
    p = plan(services, "Japan")
    rec = services.publishing.timing.recommend(services.audiences.get(p.audience_id).snapshot(), NOW)
    slot = rec.slots[1]
    view = sched.schedule_from_suggestion(p, rec, slot)
    assert view.scheduled_at == slot.start_utc and view.source == "suggestion" and view.zone == "Asia/Tokyo"
    assert view.suggestion == {"start_utc": slot.start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "end_utc": slot.end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "score": slot.score,
                               "coverage": slot.coverage, "engine_version": rec.engine_version,
                               "tzdata_version": rec.tzdata_version}


def test_stale_or_foreign_suggestion_rejected(services, sched):
    p = plan(services, "Japan")
    rec = services.publishing.timing.recommend(services.audiences.get(p.audience_id).snapshot(), NOW)
    late = SchedulingService(services.publishing, clock=lambda: rec.slots[0].start_utc - timedelta(minutes=1))
    with pytest.raises(SchedulingError, match="Refresh the suggestions") as e:
        late.schedule_from_suggestion(p, rec, rec.slots[0])
    assert code_of(e) == "stale_suggestion"
    other = services.publishing.timing.recommend(services.audiences.get(p.audience_id).snapshot(),
                                                 NOW + timedelta(days=1))
    with pytest.raises(SchedulingError) as e:
        sched.schedule_from_suggestion(p, rec, other.slots[0])
    assert code_of(e) == "invalid_input" and services.publishing.batches() == []


# --- cancel -----------------------------------------------------------------------------------

@pytest.mark.parametrize("start", ["scheduled", "missed"])
def test_cancel_keeps_jobs_pending_and_appends_history(services, sched, start):
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), zone="UTC")
    set_status(services, view.post_id, start)
    before = job_ids(services, view.post_id)
    cancelled = sched.cancel(view.post_id)
    assert cancelled.status == "cancelled" and cancelled.job_counts == {"pending": 2}
    assert job_ids(services, view.post_id) == before
    assert [h["action"] for h in cancelled.history] == ["scheduled", "cancelled"]
    assert cancelled.history[0] == view.history[0]
    assert not services.engine.store.claim(before[0])  # still held


def test_cancel_errors(services, sched):
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), zone="UTC")
    sched.publish_now(view.post_id)
    with pytest.raises(SchedulingError, match="already been released") as e:
        sched.cancel(view.post_id)
    assert (code_of(e), e.value.status) == ("invalid_state", "released")
    ordinary = services.publishing.create_batch(plan(services, accounts=1), include_already_published=True)
    with pytest.raises(SchedulingError) as e:
        sched.cancel(ordinary)
    assert code_of(e) == "not_scheduled"
    with pytest.raises(SchedulingError) as e:
        sched.cancel(9999)
    assert code_of(e) == "not_found"


# --- reschedule -------------------------------------------------------------------------------

@pytest.mark.parametrize("start", ["scheduled", "missed", "cancelled"])
def test_reschedule_keeps_jobs_and_history(services, sched, start):
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), zone="UTC")
    set_status(services, view.post_id, start)
    before = job_ids(services, view.post_id)
    moved = sched.reschedule(view.post_id, datetime(2026, 10, 20, 8, 0), zone="Europe/London")
    assert moved.status == "scheduled" and moved.scheduled_at == datetime(2026, 10, 20, 7, 0, tzinfo=UTC)
    assert moved.zone == "Europe/London" and job_ids(services, view.post_id) == before
    assert moved.history[0] == view.history[0]
    assert moved.history[-1] | {"at": None} == {"at": None, "action": "rescheduled",
                                                "from_utc": "2026-10-15T19:30:00Z", "to_utc": "2026-10-20T07:00:00Z",
                                                "source": "manual", "zone": "Europe/London"}


def test_reschedule_validates_and_rejects_released(services, sched):
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), zone="UTC")
    with pytest.raises(SchedulingError) as e:
        sched.reschedule(view.post_id, datetime(2026, 10, 2, 12, 1), zone="UTC")
    assert code_of(e) == "too_soon" and sched.view(view.post_id).scheduled_at == view.scheduled_at
    sched.publish_now(view.post_id)
    with pytest.raises(SchedulingError) as e:
        sched.reschedule(view.post_id, datetime(2026, 10, 20, 8, 0), zone="UTC")
    assert (code_of(e), e.value.status) == ("invalid_state", "released")


def test_reschedule_uses_post_snapshot_not_current_profile(services, sched):
    p = plan(services, "Japan")
    view = sched.schedule(p, datetime(2026, 10, 15, 19, 30))
    japan = services.audiences.get(p.audience_id)
    japan.countries, japan.caption_locale, japan.language = ["GB"], "en-GB", "en"
    services.audiences.save(japan)
    assert sched.reschedule(view.post_id, datetime(2026, 10, 16, 19, 30)).zone == "Asia/Tokyo"


# --- publish now ------------------------------------------------------------------------------

@pytest.mark.parametrize("start", ["scheduled", "missed"])
def test_publish_now_releases_for_existing_flow(services, sched, start):
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), zone="UTC")
    set_status(services, view.post_id, start)
    released = sched.publish_now(view.post_id)
    assert released.status == "released" and released.ready_to_publish
    assert released.scheduled_at == view.scheduled_at and released.history[-1]["action"] == "publish_now"
    assert services.fake.calls == []  # released only: nothing published by the service
    assert services.publishing.publish_batch(view.post_id).status == "completed"


@pytest.mark.parametrize("start", ["cancelled", "released"])
def test_publish_now_rejected(services, sched, start):
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), zone="UTC")
    set_status(services, view.post_id, start)
    with pytest.raises(SchedulingError) as e:
        sched.publish_now(view.post_id)
    assert (code_of(e), e.value.status) == ("invalid_state", start)


# --- atomicity --------------------------------------------------------------------------------

def test_competing_operations_have_one_winner(services, sched):
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), zone="UTC")
    barrier, results = threading.Barrier(6), []

    def worker(op):
        barrier.wait()
        try:
            getattr(SchedulingService(services.publishing, clock=lambda: NOW), op)(view.post_id)
            results.append((op, "ok"))
        except SchedulingError as e:
            results.append((op, e.code))

    threads = [threading.Thread(target=worker, args=(op,)) for op in ["cancel", "publish_now"] * 3]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [op for op, outcome in results if outcome == "ok"]
    assert len(winners) == 1 and {o for _, o in results if o != "ok"} <= {"invalid_state", "conflict"}
    final = sched.view(view.post_id)
    assert final.status == {"cancel": "cancelled", "publish_now": "released"}[winners[0]]
    action = {"cancel": "cancelled", "publish_now": "publish_now"}[winners[0]]
    assert [h["action"] for h in final.history] == ["scheduled", action]  # exactly one transition recorded


def test_stale_read_never_loses_history(services, sched, monkeypatch):
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), zone="UTC")
    stale = sched._state(view.post_id)
    sched.reschedule(view.post_id, datetime(2026, 10, 16, 9, 0), zone="UTC")  # someone else changed it first
    calls = []
    real = sched._state

    def first_stale(post_id):
        calls.append(1)
        return stale if len(calls) == 1 else real(post_id)

    monkeypatch.setattr(sched, "_state", first_stale)
    cancelled = sched.cancel(view.post_id)  # first UPDATE misses (CAS), second succeeds on fresh data
    assert len(calls) == 2
    assert [h["action"] for h in cancelled.history] == ["scheduled", "rescheduled", "cancelled"]


# --- persistence, views, publishing guard -----------------------------------------------------

def test_snapshot_survives_fresh_service_and_store(services, sched):
    view = sched.schedule(plan(services, "Japan"), datetime(2026, 10, 15, 19, 30))
    raw = stored_json(services, view.post_id)
    fresh_db = Database(str(services.engine.store.database.engine.url))
    fresh = SchedulingService(PublishingService(PublisherEngine(fresh_db, services.account_manager),
                                                services.account_manager), clock=lambda: NOW)
    again = fresh.view(view.post_id)
    assert again == view
    with fresh_db.session() as session:
        assert session.get(Post, view.post_id).schedule_json == raw
    assert raw == json.dumps(json.loads(raw), sort_keys=True)  # deterministic form
    fresh.cancel(view.post_id)
    after = stored_json(services, view.post_id)
    assert after == json.dumps(json.loads(after), sort_keys=True)  # same form after a transition
    fresh_db.engine.dispose()


def test_scheduled_list_is_soonest_first(services, sched):
    late = sched.schedule(plan(services), datetime(2026, 12, 1, 9, 0), zone="UTC")
    soon = sched.schedule(plan(services), datetime(2026, 10, 10, 9, 0), zone="UTC", include_already_published=True)
    gone = sched.schedule(plan(services), datetime(2026, 10, 11, 9, 0), zone="UTC", include_already_published=True)
    sched.cancel(gone.post_id)
    assert [v.post_id for v in sched.scheduled()] == [soon.post_id, late.post_id]
    assert [v.post_id for v in sched.scheduled(("cancelled",))] == [gone.post_id]
    assert services.scheduling.scheduled() is not None  # registered in AppServices


def test_publish_batch_refuses_held_post_before_the_engine(services, sched, monkeypatch):
    """Phase 1 edge case: an exception before claim would make the engine mark a held job failed."""
    view = sched.schedule(plan(services), datetime(2026, 10, 15, 19, 30), zone="UTC")

    def crash(*args, **kwargs):
        raise RuntimeError("adapter bug")

    monkeypatch.setattr(services.fake, "validate", crash)
    with pytest.raises(ValueError, match="release it"):
        services.publishing.publish_batch(view.post_id)
    assert sched.view(view.post_id).job_counts == {"pending": 2} and services.fake.calls == []
