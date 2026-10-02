# ruff: noqa: F811, DTZ001 - fixtures imported from other modules; naive datetimes are deliberate wall-clock input
"""Audience Strategy V2.1: timing suggestions (offline; ``now`` always fixed)."""

from datetime import datetime, time, timedelta, timezone
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from src.content.scheduling import (
    DEFAULT_WINDOWS,
    ENGINE_VERSION,
    GLOBAL_NOTE,
    MANUAL_NOTE,
    PostingWindow,
    TimezoneEngine,
    default_manual_zone,
    describe,
)
from src.storage.database import Post, PublishJob
from tests.unit.test_publishing_engine import env  # noqa: F401
from tests.unit.test_tui import services  # noqa: F401

UTC = timezone.utc
ENGINE = TimezoneEngine()


def local(*countries):
    return {"name": "x", "countries": list(countries), "timezone_strategy": "audience_local"}


def at(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def starts(rec):
    return [s.start_utc for s in rec.slots]


def view(slot, zone):
    return next(v for v in slot.local_views if v.zone == zone)


# --- windows / basic shape -----------------------------------------------------------------

def test_default_windows_and_quiet_penalty():
    assert [(w.name, w.start, w.end, w.weight) for w in DEFAULT_WINDOWS] == [
        ("morning", time(7), time(9), 0.6), ("midday", time(12), time(13, 30), 0.8),
        ("evening", time(19), time(22), 1.0), ("quiet hours", time(0), time(6), -0.5)]
    assert ENGINE._score_zone(time(20)) == (1.0, True)
    assert ENGINE._score_zone(time(2)) == (-0.5, False)
    assert ENGINE._score_zone(time(10)) == (0.0, False)
    assert PostingWindow("late", time(23), time(1), 1).contains(time(0, 30))  # crosses midnight


def test_india_half_hour_offset_evening():
    rec = ENGINE.recommend(local("IN"), at(2026, 10, 6))
    assert starts(rec) == [at(2026, 10, 6, 1, 30), at(2026, 10, 6, 6, 30), at(2026, 10, 6, 13, 30)]
    best = max(rec.slots, key=lambda s: s.score)
    assert best.start_utc == at(2026, 10, 6, 13, 30) and best.end_utc == at(2026, 10, 6, 16, 30)
    assert best.local_views[0].label == "Tue 19:00 IST Kolkata" and best.coverage == 1.0


# --- DST ---------------------------------------------------------------------------------------

def test_us_gb_dst_mismatch_moves_london_evening():
    # 16 March 2026: US already on EDT, UK still on GMT (4 h apart). 6 October: both on summer time (5 h).
    gb_march = ENGINE.recommend(local("GB"), at(2026, 3, 16, 6))
    gb_october = ENGINE.recommend(local("GB"), at(2026, 10, 6, 6))
    assert at(2026, 3, 16, 19) in starts(gb_march) and at(2026, 10, 6, 18) in starts(gb_october)
    for rec, gap in ((ENGINE.recommend(local("US", "GB"), at(2026, 3, 16, 6)), 4),
                     (ENGINE.recommend(local("US", "GB"), at(2026, 10, 6, 6)), 5)):
        slot = rec.slots[0]
        london = view(slot, "Europe/London").local.utcoffset()
        new_york = view(slot, "America/New_York").local.utcoffset()
        assert london - new_york == timedelta(hours=gap)


def test_australia_southern_hemisphere_dst():
    january = ENGINE.recommend(local("AU"), at(2026, 1, 6))
    july = ENGINE.recommend(local("AU"), at(2026, 7, 6))
    assert "AEDT" in january.slots[0].local_views[0].label and "AEST" in july.slots[0].local_views[0].label
    # Brisbane has no DST: a separate clock from Sydney in January, the same clock in July.
    assert any(v.zone == "Australia/Brisbane" for v in january.slots[0].local_views)
    assert not any(v.zone == "Australia/Brisbane" for v in july.slots[0].local_views)


@pytest.mark.parametrize("now", [at(2026, 3, 8), at(2026, 11, 1), at(2026, 3, 29), at(2026, 10, 25),
                                 at(2026, 4, 5), at(2026, 10, 4)])
def test_transition_days_have_a_complete_utc_grid(now):
    intervals = ENGINE._merged_intervals(*_prepared(local("US", "GB", "AU")), now)
    assert intervals[0][0] == now + timedelta(minutes=15)
    assert all(a[1] == b[0] for a, b in pairwise(intervals))  # no gaps, no overlaps
    assert intervals[-1][1] - intervals[0][0] == timedelta(hours=24)


def test_transition_day_local_labels():
    rec = ENGINE.recommend(local("US"), at(2026, 3, 8))  # US clocks jump at 07:00 UTC
    for slot in rec.slots:
        expected = "EDT" if slot.start_utc >= at(2026, 3, 8, 7) else "EST"
        assert expected in view(slot, "America/New_York").label
    assert {"EST", "EDT"} <= {view(s, "America/New_York").local.tzname() for s in rec.slots}
    conv = ENGINE.convert_manual(datetime(2026, 3, 8, 1, 30), "America/New_York", None)
    assert conv.utc == at(2026, 3, 8, 6, 30)  # still EST before the jump


def _prepared(snapshot):
    from src.content.timezones import zones_for

    shares, _ = zones_for(snapshot["countries"], 2026)
    return shares, ENGINE._audience_weights(shares)


# --- multi-country selection -------------------------------------------------------------------

def test_quiet_hours_keep_the_best_slot_awake():
    rec = ENGINE.recommend(local("US", "CA", "GB"), at(2026, 10, 6, 6))
    shares, weights = _prepared(local("US", "CA", "GB"))
    best = max(rec.slots, key=lambda s: (s.score, s.coverage))
    asleep = sum(w for s, w in zip(shares, weights)
                 if best.start_utc.astimezone(ZoneInfo(s.zone)).time() < time(6))
    assert asleep < 0.5
    gb = ENGINE.recommend(local("GB"), at(2026, 10, 6, 6))
    assert all(view(s, "Europe/London").local.hour >= 6 for s in gb.slots)  # never in quiet hours


def test_at_most_three_results():
    many = tuple(PostingWindow(f"w{h}", time(h), time(h, 30), 1.0) for h in range(0, 24, 3))
    rec = TimezoneEngine(windows=many).recommend(local("GB"), at(2026, 10, 6))
    assert len(rec.slots) == 3


def test_two_hour_minimum_gap():
    windows = (PostingWindow("a", time(18), time(18, 30), 1.0), PostingWindow("b", time(19, 30), time(20), 0.9),
               PostingWindow("c", time(21), time(21, 30), 0.8))
    rec = TimezoneEngine(windows=windows).recommend(local("IS"), at(2026, 10, 6))  # Iceland: UTC all year
    assert starts(rec) == [at(2026, 10, 6, 18), at(2026, 10, 6, 21)]  # b is only 1 h after a
    for x, y in pairwise(rec.slots):
        assert y.start_utc - x.end_utc >= timedelta(hours=2)


@pytest.mark.parametrize("weak, kept", [(0.2, False), (0.3, True)])
def test_quarter_of_best_threshold(weak, kept):
    windows = (PostingWindow("main", time(19), time(20), 1.0), PostingWindow("weak", time(9), time(10), weak))
    rec = TimezoneEngine(windows=windows).recommend(local("IS"), at(2026, 10, 6))
    assert (at(2026, 10, 6, 9) in starts(rec)) is kept and at(2026, 10, 6, 19) in starts(rec)


def test_tie_break_prefers_earliest_start():
    windows = (PostingWindow("a", time(9), time(10), 1.0), PostingWindow("b", time(15), time(16), 1.0))
    rec = TimezoneEngine(windows=windows, max_slots=1).recommend(local("IS"), at(2026, 10, 6))
    assert starts(rec) == [at(2026, 10, 6, 9)]


def test_deterministic_and_versioned():
    a = ENGINE.recommend(local("US", "CA", "GB"), at(2026, 10, 6, 6, 7))
    b = TimezoneEngine().recommend(local("US", "CA", "GB"), at(2026, 10, 6, 6, 7))
    assert a == b and a.engine_version == ENGINE_VERSION and a.tzdata_version
    assert all(s.start_utc.tzinfo is UTC for s in a.slots)
    assert a.slots[0].start_utc >= at(2026, 10, 6, 6, 22)  # rounded up after the 15-minute lead
    assert ENGINE.recommend(local("GB"), at(2026, 10, 6, 6)) == ENGINE.recommend(
        local("GB"), datetime(2026, 10, 6, 8, tzinfo=ZoneInfo("Europe/Berlin")))  # same instant, any zone


# --- strategies --------------------------------------------------------------------------------

@pytest.mark.parametrize("snapshot", [None, {}, {"countries": [], "timezone_strategy": "global"},
                                      {"countries": ["US"], "timezone_strategy": "global"},
                                      {"countries": [], "timezone_strategy": "audience_local"}])
def test_global_has_no_best_worldwide_time(snapshot):
    rec = ENGINE.recommend(snapshot, at(2026, 10, 6))
    assert rec.slots == () and rec.notes == (GLOBAL_NOTE,) and rec.strategy == "global"
    assert describe(rec) == [GLOBAL_NOTE]


def test_manual_strategy_and_conversion():
    snapshot = {"countries": ["US"], "timezone_strategy": "manual"}
    rec = ENGINE.recommend(snapshot, at(2026, 10, 6))
    assert rec.slots == () and rec.notes == (MANUAL_NOTE,)
    conv = ENGINE.convert_manual(datetime(2026, 10, 6, 19), "Europe/London", snapshot)
    assert conv.utc == at(2026, 10, 6, 18) and conv.zone == "Europe/London" and conv.notes == ()
    assert view(conv, "America/New_York").label == "Tue 14:00 EDT New York"
    assert ENGINE.convert_manual(datetime(2026, 10, 6, 19), "UTC", None).local_views == ()


def test_invalid_zone_and_default_zone(monkeypatch):
    for bad in ("Mars/Olympus", "", "../etc"):
        with pytest.raises(ValueError, match="Unknown timezone"):
            ENGINE.convert_manual(datetime(2026, 10, 6, 19), bad, None)
    monkeypatch.delenv("SOC_BOT_TIMEZONE", raising=False)
    assert default_manual_zone() == "UTC"
    monkeypatch.setenv("SOC_BOT_TIMEZONE", "Asia/Tokyo")
    assert default_manual_zone() == "Asia/Tokyo"
    monkeypatch.setenv("SOC_BOT_TIMEZONE", "Not/AZone")
    assert default_manual_zone() == "UTC"


def test_naive_datetimes_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        ENGINE.recommend(local("GB"), datetime(2026, 10, 6, 6))
    with pytest.raises(ValueError, match="without a timezone"):
        ENGINE.convert_manual(at(2026, 10, 6, 19), "Europe/London", None)


@pytest.mark.parametrize("wall, zone", [(datetime(2026, 3, 8, 2, 30), "America/New_York"),
                                        (datetime(2026, 3, 29, 1, 30), "Europe/London")])
def test_dst_gap_rejected(wall, zone):
    with pytest.raises(ValueError, match="does not exist"):
        ENGINE.convert_manual(wall, zone, None)


def test_dst_overlap_uses_first_occurrence():
    conv = ENGINE.convert_manual(datetime(2026, 11, 1, 1, 30), "America/New_York", None)
    assert conv.utc == at(2026, 11, 1, 5, 30)  # 01:30 EDT, not 01:30 EST (06:30 UTC)
    assert "happens twice" in conv.notes[0]


# --- history + service safety ----------------------------------------------------------------

def test_recompute_from_post_snapshot_after_profile_edit(services):
    audiences = services.audiences
    us = next(a for a in audiences.list() if a.name == "United States")
    plan = services.publishing.plan(services.video, "hi", None, [services.accounts.list("instagram")[0].id])
    plan.audience_id = us.id
    post_id = services.publishing.create_batch(plan)
    us.countries, us.caption_locale = ["JP"], "ja-JP"
    audiences.save(us)
    from_history = ENGINE.recommend(audiences.for_post(post_id), at(2026, 10, 6))
    assert {z.country for z in from_history.zones} == {"US"}
    assert {z.country for z in services.publishing.suggest_times(us.id, at(2026, 10, 6)).zones} == {"JP"}


def test_suggest_times_has_no_side_effects(services):
    db = services.engine.store.database
    us = next(a for a in services.audiences.list() if a.name == "United States")
    rec = services.publishing.suggest_times(us.id, at(2026, 10, 6))
    assert rec.slots and services.publishing.suggest_times(None, at(2026, 10, 6)).notes == (GLOBAL_NOTE,)
    assert services.publishing.convert_manual(datetime(2026, 10, 6, 19), us.id, "Europe/London").utc == \
        at(2026, 10, 6, 18)
    with db.session() as session:
        assert session.query(Post).count() == 0 and session.query(PublishJob).count() == 0
    assert services.fake.calls == [] and services.publishing.batches() == []
