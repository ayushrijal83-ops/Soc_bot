"""Audience-aware timing suggestions (Audience Strategy V2.1). Recommendation only.

From an audience snapshot (V1 ``Audience.snapshot()`` / ``posts.audience_json``) the engine suggests
up to three UTC publishing slots that fall in good LOCAL hours for the audience's timezones. It
never schedules, creates jobs or talks to a platform, and it does not influence who sees a post:
organic platforms decide distribution. Window times and timezone weights are heuristics.

Model: country -> weighted IANA zones (``timezones.zones_for``) -> 15-minute UTC slots over 24 h ->
each zone scores the slot by the local posting window it falls in (DST-aware via zoneinfo, on the
slot's real date) minus a quiet-hours penalty -> equal-score neighbours merge into intervals ->
best intervals by score, coverage, earliest start; at least 2 h apart; at most 3.

Deterministic: ``now`` is always passed in (the engine never reads the clock) and the result
records the engine and tzdata versions.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.content.audience import AudienceTimeStrategy
from src.content.timezones import ZoneShare, tzdata_version, zones_for

ENGINE_VERSION = "2.1"
GLOBAL_NOTE = "Global audience: no audience-local time preference; publish when convenient."
MANUAL_NOTE = "Manual timing: no automatic suggestion; choose a time and compare it with the audience's local time."
VIEWS_PER_SLOT = 3


@dataclass(frozen=True)
class PostingWindow:
    """Local wall-clock range. A negative weight is a penalty (quiet hours)."""

    name: str
    start: time
    end: time
    weight: float

    def contains(self, t: time) -> bool:
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end  # crosses midnight


# Heuristic defaults, not platform facts.
DEFAULT_WINDOWS: tuple[PostingWindow, ...] = (
    PostingWindow("morning", time(7, 0), time(9, 0), 0.6),
    PostingWindow("midday", time(12, 0), time(13, 30), 0.8),
    PostingWindow("evening", time(19, 0), time(22, 0), 1.0),
    PostingWindow("quiet hours", time(0, 0), time(6, 0), -0.5),
)


@dataclass(frozen=True)
class LocalView:
    country: str
    zone: str
    local: datetime
    label: str          # e.g. "Tue 19:30 EDT New York"


@dataclass(frozen=True)
class TimeSlot:
    start_utc: datetime
    end_utc: datetime
    score: float
    coverage: float     # share of the audience inside a positive posting window at the start
    local_views: tuple[LocalView, ...]


@dataclass(frozen=True)
class Recommendation:
    strategy: str
    slots: tuple[TimeSlot, ...]
    zones: tuple[ZoneShare, ...]
    notes: tuple[str, ...]
    engine_version: str
    tzdata_version: str
    windows: tuple[PostingWindow, ...]


@dataclass(frozen=True)
class ManualConversion:
    local: datetime     # aware, in the chosen zone
    zone: str
    utc: datetime
    local_views: tuple[LocalView, ...]
    notes: tuple[str, ...]


def default_manual_zone() -> str:
    """SOC_BOT_TIMEZONE if it is a valid IANA zone, else UTC. The computer's zone is never guessed."""
    zone = (os.environ.get("SOC_BOT_TIMEZONE") or "").strip()
    try:
        return ZoneInfo(zone).key if zone else "UTC"
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _view(share: ZoneShare, moment: datetime) -> LocalView:
    local = moment.astimezone(ZoneInfo(share.zone))
    city = share.zone.rsplit("/", 1)[-1].replace("_", " ")
    return LocalView(share.country, share.zone, local, f"{local:%a %H:%M} {local.tzname()} {city}")


def _views(ranked: list[ZoneShare], moment: datetime) -> tuple[LocalView, ...]:
    """Up to VIEWS_PER_SLOT views with distinct local clocks (Toronto = New York is shown once)."""
    views: list[LocalView] = []
    for share in ranked:
        view = _view(share, moment)
        if all(v.local.utcoffset() != view.local.utcoffset() for v in views):
            views.append(view)
        if len(views) == VIEWS_PER_SLOT:
            break
    return tuple(views)


class TimezoneEngine:
    def __init__(self, windows: tuple[PostingWindow, ...] = DEFAULT_WINDOWS, slot_minutes: int = 15,
                 horizon: timedelta = timedelta(hours=24), max_slots: int = 3,
                 min_gap: timedelta = timedelta(hours=2), lead: timedelta = timedelta(minutes=15),
                 min_relative_score: float = 0.25):
        self.windows = tuple(windows)
        self.slot = timedelta(minutes=slot_minutes)
        self.horizon = horizon
        self.max_slots = max_slots
        self.min_gap = min_gap
        self.lead = lead
        self.min_relative_score = min_relative_score

    # --- public ------------------------------------------------------------------------------

    def recommend(self, snapshot: dict | None, now: datetime) -> Recommendation:
        now = _utc(now)
        strategy = (snapshot or {}).get("timezone_strategy") or AudienceTimeStrategy.GLOBAL
        countries = list((snapshot or {}).get("countries") or [])
        if strategy == AudienceTimeStrategy.MANUAL:
            return self._empty(strategy, (MANUAL_NOTE,))
        if strategy == AudienceTimeStrategy.GLOBAL or not countries:
            return self._empty(AudienceTimeStrategy.GLOBAL, (GLOBAL_NOTE,))
        if strategy != AudienceTimeStrategy.AUDIENCE_LOCAL:
            raise ValueError(f"Unknown timezone strategy {strategy!r}")

        shares, notes = zones_for(countries, now.year)
        if not shares:
            return self._empty(strategy, (*notes, "No audience timezone data; no suggestion."), ())
        weights = self._audience_weights(shares)
        slots = self._merged_intervals(shares, weights, now)
        picked = self._select(slots)
        ranked = self._ranked(shares, weights)
        result = tuple(TimeSlot(a, b, score, cov, _views(ranked, a)) for a, b, score, cov in picked)
        if not result:
            notes.append("No time falls inside the audience's posting windows in the next 24 hours.")
        return Recommendation(strategy, result, tuple(shares), tuple(notes), ENGINE_VERSION, tzdata_version(),
                              self.windows)

    def convert_manual(self, local: datetime, zone: str, snapshot: dict | None) -> ManualConversion:
        """A wall-clock time in ``zone`` -> UTC, plus how it looks for the audience."""
        if local.tzinfo is not None:
            raise ValueError("local must be a wall-clock time without a timezone")
        try:
            tz = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"Unknown timezone {zone!r}") from e
        first = local.replace(tzinfo=tz, fold=0)
        utc = first.astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) != local:
            raise ValueError(f"{local:%Y-%m-%d %H:%M} does not exist in {zone} (clocks jump forward).")
        notes = []
        if first.utcoffset() != local.replace(tzinfo=tz, fold=1).utcoffset():
            notes.append(f"{local:%Y-%m-%d %H:%M} happens twice in {zone} (clocks go back); "
                         "the first occurrence is used.")
        views: tuple[LocalView, ...] = ()
        countries = list((snapshot or {}).get("countries") or [])
        if countries:
            shares, extra = zones_for(countries, local.year)
            notes += extra
            views = _views(self._ranked(shares, self._audience_weights(shares)), utc)
        return ManualConversion(first, tz.key, utc, views, tuple(notes))

    # --- internals ---------------------------------------------------------------------------

    def _empty(self, strategy: str, notes: tuple[str, ...], zones: tuple = ()) -> Recommendation:
        return Recommendation(strategy, (), tuple(zones), notes, ENGINE_VERSION, tzdata_version(), self.windows)

    @staticmethod
    def _audience_weights(shares: list[ZoneShare]) -> list[float]:
        """Each country gets an equal share of the audience, split by its zone weights."""
        countries = len({s.country for s in shares})
        return [s.weight / countries for s in shares]

    def _score_zone(self, local: time) -> tuple[float, bool]:
        best = max((w.weight for w in self.windows if w.weight > 0 and w.contains(local)), default=0.0)
        penalty = sum(w.weight for w in self.windows if w.weight < 0 and w.contains(local))
        return best + penalty, best > 0

    def _merged_intervals(self, shares, weights, now: datetime) -> list[tuple[datetime, datetime, float, float]]:
        step = self.slot.total_seconds()
        start = datetime.fromtimestamp(math.ceil((now + self.lead).timestamp() / step) * step, timezone.utc)
        zones = [ZoneInfo(s.zone) for s in shares]
        intervals: list[list] = []
        for i in range(int(self.horizon / self.slot)):
            t = start + i * self.slot
            score = coverage = 0.0
            for tz, weight in zip(zones, weights):
                value, inside = self._score_zone(t.astimezone(tz).time())
                score += weight * value
                coverage += weight if inside else 0.0
            score, coverage = round(score, 6), round(coverage, 6)
            if intervals and intervals[-1][2] == score:
                intervals[-1][1] = t + self.slot
                intervals[-1][3] = min(intervals[-1][3], coverage)
            else:
                intervals.append([t, t + self.slot, score, coverage])
        return [tuple(i) for i in intervals]

    def _select(self, intervals):
        positive = [i for i in intervals if i[2] > 0]
        if not positive:
            return []
        floor = max(i[2] for i in positive) * self.min_relative_score
        picked = []
        for a, b, score, coverage in sorted(positive, key=lambda i: (-i[2], -i[3], i[0])):
            if score < floor or len(picked) == self.max_slots:
                continue
            if all(a - pb >= self.min_gap or pa - b >= self.min_gap for pa, pb, _, _ in picked):
                picked.append((a, b, score, coverage))
        return sorted(picked)

    @staticmethod
    def _ranked(shares, weights) -> list[ZoneShare]:
        """Zones by audience weight, highest first (stable order on ties)."""
        return [shares[i] for i in sorted(range(len(shares)), key=lambda i: (-weights[i], i))]


def describe(rec: Recommendation) -> list[str]:
    """Plain lines for review screens: one per suggestion, then notes."""
    lines = [f"{s.start_utc:%a %H:%M}-{s.end_utc:%H:%M} UTC  ·  "
             + ", ".join(v.label.split(" ", 1)[1] for v in s.local_views)
             + f"  ({s.coverage:.0%} in window)" for s in rec.slots]
    return lines + list(rec.notes)
