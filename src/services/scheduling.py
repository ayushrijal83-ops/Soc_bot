"""Scheduling service (V2.2 phase 2): schedule / cancel / reschedule / publish-now and schedule views.

State changes only. Nothing here publishes, polls or runs in the background: a scheduled post is created
HELD (``posts.schedule_status = 'scheduled'``), and JobStore never claims, resumes or auto-retries the jobs
of a held post. ``publish_now`` only RELEASES a post; the caller then runs the existing publishing flow.

Time: a wall-clock time + IANA zone goes through the V2.1 ``TimezoneEngine.convert_manual`` (DST gap
rejected, DST overlap = first occurrence, fold=0). UTC is the authoritative instant and is stored as
naive UTC. Every transition is a conditional UPDATE on the current status AND the current
``schedule_json`` (compare-and-swap), so competing operations have exactly one winner and history
entries are never lost.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from sqlalchemy import update

from src.content.audience import AudienceStore, AudienceTimeStrategy
from src.content.scheduling import Recommendation, TimeSlot, TimezoneEngine
from src.content.timezones import tzdata_version, zones_for
from src.core.publisher import batch_status
from src.storage.database import Post

UTC = timezone.utc
MIN_LEAD = timedelta(minutes=2)      # earliest: now + 2 minutes
MAX_AHEAD = timedelta(days=365)      # latest: now + 365 days
CAS_ATTEMPTS = 3                     # retries when schedule_json changed under us but the state still allows it

CANCELLABLE = ("scheduled", "missed")
RESCHEDULABLE = ("scheduled", "missed", "cancelled")
RELEASABLE = ("scheduled", "missed")


class SchedulingError(Exception):
    """A scheduling request that can't be carried out. ``code`` is stable for UIs and tests:
    invalid_input, invalid_zone, dst_gap, too_soon, too_far, stale_suggestion, not_found, not_scheduled,
    invalid_state, conflict."""

    def __init__(self, code: str, message: str, status: str | None = None):
        super().__init__(message)
        self.code = code
        self.status = status  # current schedule_status for invalid_state


@dataclass(frozen=True)
class ResolvedTime:
    utc: datetime              # aware UTC: the instant that is stored
    local: datetime            # aware, in ``zone``
    zone: str
    zone_source: str           # explicit | audience_primary | env | utc
    fold: int
    dst_overlap: bool
    notes: tuple[str, ...]     # e.g. overlap note, "primary audience timezone" note


@dataclass(frozen=True)
class ScheduleView:
    post_id: int
    status: str | None             # scheduled | released | missed | cancelled
    scheduled_at: datetime | None  # aware UTC
    local: datetime | None         # aware, in ``zone``
    zone: str | None
    zone_source: str | None
    source: str | None             # manual | suggestion
    fold: int
    dst_overlap: bool
    overlap_note: str | None
    suggestion: dict | None
    history: tuple[dict, ...]
    destinations: int
    job_counts: dict[str, int]
    batch_status: str
    ready_to_publish: bool         # released: the caller may run the existing publishing flow
    video: str = ""                # video file name (display only)
    audience: str | None = None    # audience snapshot name recorded on the post (display only)


OVERLAP_NOTE = "This local time occurs twice; the first occurrence was selected."

# What users see for each SchedulingError code (UIs and CLI share this; codes stay stable).
ERROR_MESSAGES = {
    "invalid_zone": "Unknown timezone. Choose one from the list (an IANA name such as Europe/London).",
    "stale_suggestion": "That suggested time is too close or already past. The suggestions were refreshed; "
                        "choose again.",
    "not_found": "This post no longer exists.",
    "not_scheduled": "This post is not a scheduled post.",
    "conflict": "This scheduled post changed before your action completed. The queue has been refreshed.",
}


def friendly_error(error: SchedulingError) -> str:
    """User-facing text for a SchedulingError. Codes whose service message is already user-facing (dst_gap,
    too_soon, too_far, invalid_input, invalid_state) keep it, so limits and times stay accurate."""
    return ERROR_MESSAGES.get(error.code) or str(error)


@lru_cache(maxsize=1)
def all_zones() -> tuple[str, ...]:
    """Every IANA zone of the installed tzdata, sorted (computed once)."""
    return tuple(sorted(available_timezones()))
SECOND_OCCURRENCE_NOTE = "This local time occurs twice; this is the second occurrence (exact UTC instant kept)."


def _overlap(local: datetime) -> bool:
    """True when this wall-clock time happens twice in its zone (DST fall-back)."""
    return local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset()


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump(data: dict) -> str:
    return json.dumps(data, sort_keys=True)  # same form JobStore.create_post writes for a sorted dict


class SchedulingService:
    def __init__(self, publishing, clock: Callable[[], datetime] | None = None,
                 min_lead: timedelta = MIN_LEAD, max_ahead: timedelta = MAX_AHEAD):
        self.publishing = publishing                     # PublishingService (creates the batch)
        self.store = publishing.store
        self.database = publishing.store.database
        self.audiences: AudienceStore = publishing.audiences
        self.timing: TimezoneEngine = publishing.timing
        self.clock = clock or (lambda: datetime.now(UTC))
        self.min_lead = min_lead
        self.max_ahead = max_ahead

    # --- time ----------------------------------------------------------------------------

    @staticmethod
    def parse_local(date_text: str, time_text: str) -> datetime:
        """'YYYY-MM-DD' + 'HH:MM' -> naive wall-clock datetime (no seconds). invalid_input otherwise."""
        try:
            text = f"{(date_text or '').strip()} {(time_text or '').strip()}"
            return datetime.strptime(text, "%Y-%m-%d %H:%M")  # noqa: DTZ007 - wall clock; resolve_time adds the zone
        except ValueError as e:
            raise SchedulingError("invalid_input", "Enter the date as YYYY-MM-DD and the time as HH:MM "
                                                   "(for example 2026-10-15 and 19:30).") from e

    def resolve_zone(self, snapshot: dict | None, explicit: str | None = None) -> tuple[str, str, tuple[str, ...]]:
        """(zone, source, notes): explicit > audience primary (audience_local) > SOC_BOT_TIMEZONE > UTC."""
        if explicit:
            return self._zone(explicit), "explicit", ()
        snapshot = snapshot or {}
        countries = list(snapshot.get("countries") or [])
        if snapshot.get("timezone_strategy") == AudienceTimeStrategy.AUDIENCE_LOCAL and countries:
            shares, _ = zones_for(countries, self.clock().year)
            if shares:
                # Reuse the V2.1 weighting: equal share per country, split by the country's zone weights.
                primary = TimezoneEngine._ranked(shares, TimezoneEngine._audience_weights(shares))[0]
                notes = ()
                if len(shares) > 1:
                    notes = ((f"Primary audience timezone {primary.zone} ({primary.country}) is used; "
                              "the audience spans several timezones."),)
                return primary.zone, "audience_primary", notes
        env = (os.environ.get("SOC_BOT_TIMEZONE") or "").strip()
        if env:
            try:
                return ZoneInfo(env).key, "env", ()
            except (ZoneInfoNotFoundError, ValueError):
                pass  # an invalid setting falls back to UTC (same rule as V2.1 default_manual_zone)
        return "UTC", "utc", ()

    def resolve_time(self, local: datetime, snapshot: dict | None = None, zone: str | None = None) -> ResolvedTime:
        """Wall-clock ``local`` (naive) in the resolved zone -> validated UTC instant."""
        if not isinstance(local, datetime) or local.tzinfo is not None:
            raise SchedulingError("invalid_input", "Give the date and time without a timezone; choose the zone separately.")
        zone_name, source, notes = self.resolve_zone(snapshot, zone)
        try:
            conv = self.timing.convert_manual(local, zone_name, None)
        except ValueError as e:  # the zone is valid and local is naive, so only the DST gap remains
            raise SchedulingError("dst_gap", f"{local:%Y-%m-%d %H:%M} does not exist in {zone_name}: the clocks "
                                             "jump forward at that time. Choose another time.") from e
        overlap = _overlap(conv.local)
        self._check_range(conv.utc)
        return ResolvedTime(conv.utc, conv.local, zone_name, source, 0, overlap,
                            notes + ((OVERLAP_NOTE,) if overlap else ()))

    def _zone(self, name: str) -> str:
        try:
            return ZoneInfo(name).key
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise SchedulingError("invalid_zone", f"Unknown timezone {name!r}. Use an IANA name such as "
                                                  "Europe/London.") from e

    def _check_range(self, utc: datetime, stale_code: str | None = None) -> None:
        now = self.clock().astimezone(UTC)
        if utc < now + self.min_lead:
            if stale_code:
                raise SchedulingError(stale_code, "This suggested time is too close or already past. "
                                                  "Refresh the suggestions and choose again.")
            raise SchedulingError("too_soon", f"Scheduling time must be at least "
                                              f"{int(self.min_lead.total_seconds() // 60)} minutes in the future.")
        if utc > now + self.max_ahead:
            raise SchedulingError("too_far", f"Scheduling time must be within {self.max_ahead.days} days.")

    # --- snapshot ------------------------------------------------------------------------

    def _entry(self, action: str, from_utc: datetime | None, to_utc: datetime | None, **extra) -> dict:
        return {"at": _iso(self.clock()), "action": action, "from_utc": _iso(from_utc) if from_utc else None,
                "to_utc": _iso(to_utc) if to_utc else None, **extra}

    def _snapshot(self, when: ResolvedTime, source: str, suggestion: dict | None, history: list[dict]) -> dict:
        data = {"source": source, "local": when.local.strftime("%Y-%m-%dT%H:%M"), "zone": when.zone,
                "zone_source": when.zone_source, "fold": when.fold, "dst_overlap": when.dst_overlap,
                "utc": _iso(when.utc), "tzdata_version": tzdata_version(), "history": history}
        if suggestion is not None:
            data["suggestion"] = suggestion
        return json.loads(_dump(data))  # deterministic key order

    # --- create --------------------------------------------------------------------------

    def schedule(self, plan, local: datetime, zone: str | None = None,
                 include_already_published: bool = False) -> ScheduleView:
        """Create a HELD post for a manually chosen wall-clock time. Publishes nothing."""
        when = self.resolve_time(local, self._plan_snapshot(plan), zone)
        return self._create(plan, when, "manual", None, include_already_published)

    def schedule_from_suggestion(self, plan, recommendation: Recommendation, slot: TimeSlot,
                                 include_already_published: bool = False) -> ScheduleView:
        """Schedule at a V2.1 suggestion's ``start_utc`` exactly (never re-converted from local display)."""
        if slot not in recommendation.slots:
            raise SchedulingError("invalid_input", "That suggestion is not part of this recommendation.")
        start = slot.start_utc.astimezone(UTC)
        self._check_range(start, stale_code="stale_suggestion")
        zone, source, notes = self.resolve_zone(self._plan_snapshot(plan))
        local = start.astimezone(ZoneInfo(zone))  # display only; start_utc stays the instant
        overlap = _overlap(local)
        if overlap:
            notes += (OVERLAP_NOTE if local.fold == 0 else SECOND_OCCURRENCE_NOTE,)
        when = ResolvedTime(start, local, zone, source, local.fold, overlap, notes)
        suggestion = {"start_utc": _iso(slot.start_utc), "end_utc": _iso(slot.end_utc), "score": slot.score,
                      "coverage": slot.coverage, "engine_version": recommendation.engine_version,
                      "tzdata_version": recommendation.tzdata_version}
        return self._create(plan, when, "suggestion", suggestion, include_already_published)

    def _plan_snapshot(self, plan) -> dict | None:
        audience = self.audiences.get(plan.audience_id) if plan.audience_id is not None else None
        return audience.snapshot() if audience else None

    def _create(self, plan, when: ResolvedTime, source: str, suggestion: dict | None,
                include_already_published: bool) -> ScheduleView:
        history = [self._entry("scheduled", None, when.utc, source=source, zone=when.zone)]
        data = self._snapshot(when, source, suggestion, history)
        post_id = self.publishing.create_batch(plan, include_already_published, scheduled_at=when.utc,
                                               schedule_status="scheduled", schedule_json=data)
        return self.view(post_id)

    # --- transitions ---------------------------------------------------------------------

    def cancel(self, post_id: int) -> ScheduleView:
        """scheduled / missed -> cancelled. Jobs stay pending (held). Fails once released."""
        return self._transition(post_id, CANCELLABLE, "cancelled",
                                lambda data, at: (data, at, [self._entry("cancelled", at, None)]))

    def reschedule(self, post_id: int, local: datetime, zone: str | None = None) -> ScheduleView:
        """scheduled / missed / cancelled -> scheduled at a new time. Same jobs, history kept."""
        when = self.resolve_time(local, self.audiences.for_post(post_id), zone)

        def change(data, at):
            entry = self._entry("rescheduled", at, when.utc, source="manual", zone=when.zone)
            fresh = self._snapshot(when, "manual", None, [])
            return fresh, when.utc, [entry]

        return self._transition(post_id, RESCHEDULABLE, "scheduled", change)

    def reschedule_from_suggestion(self, post_id: int, recommendation: Recommendation, slot: TimeSlot) -> ScheduleView:
        """Reschedule to a V2.1 suggestion's ``start_utc`` exactly (never re-converted from local display)."""
        if slot not in recommendation.slots:
            raise SchedulingError("invalid_input", "That suggestion is not part of this recommendation.")
        start = slot.start_utc.astimezone(UTC)
        self._check_range(start, stale_code="stale_suggestion")
        zone, zone_source, notes = self.resolve_zone(self.audiences.for_post(post_id))
        local = start.astimezone(ZoneInfo(zone))
        overlap = _overlap(local)
        when = ResolvedTime(start, local, zone, zone_source, local.fold, overlap, notes)
        suggestion = {"start_utc": _iso(slot.start_utc), "end_utc": _iso(slot.end_utc), "score": slot.score,
                      "coverage": slot.coverage, "engine_version": recommendation.engine_version,
                      "tzdata_version": recommendation.tzdata_version}

        def change(data, at):
            entry = self._entry("rescheduled", at, when.utc, source="suggestion", zone=when.zone)
            return self._snapshot(when, "suggestion", suggestion, []), when.utc, [entry]

        return self._transition(post_id, RESCHEDULABLE, "scheduled", change)

    def publish_now(self, post_id: int) -> ScheduleView:
        """scheduled / missed -> released (after the caller's confirmation). The caller then runs the
        existing publishing flow (e.g. PublishingService.publish_batch); nothing is published here."""
        return self._transition(post_id, RELEASABLE, "released",
                                lambda data, at: (data, at, [self._entry("publish_now", at, None)]))

    def _transition(self, post_id: int, allowed: tuple[str, ...], target: str, change) -> ScheduleView:
        """Compare-and-swap on (schedule_status, schedule_json). ``change(data, scheduled_at)`` returns
        (new top-level data, new scheduled_at, entries to append)."""
        for _ in range(CAS_ATTEMPTS):
            status, raw, scheduled_at = self._state(post_id)
            if status not in allowed:
                raise SchedulingError("invalid_state", self._state_message(status, target), status)
            data = json.loads(raw) if raw else {}
            new, new_at, entries = change(data, scheduled_at)
            new = {**new, "history": list(data.get("history", [])) + entries}
            with self.database.session() as session:
                result = session.execute(
                    update(Post)
                    .where(Post.id == post_id, Post.schedule_status == status,
                           Post.schedule_json == raw if raw is not None else Post.schedule_json.is_(None))
                    .values(schedule_status=target, schedule_json=_dump(new),
                            scheduled_at=new_at.astimezone(UTC).replace(tzinfo=None) if new_at else None))
                session.commit()
            if result.rowcount == 1:
                return self.view(post_id)
        raise SchedulingError("conflict", "The schedule changed at the same time; try again.")

    def _state(self, post_id: int) -> tuple[str | None, str | None, datetime | None]:
        with self.database.session() as session:
            post = session.get(Post, post_id)
            if post is None:
                raise SchedulingError("not_found", f"Post {post_id} not found.")
            if post.schedule_status is None:
                raise SchedulingError("not_scheduled", f"Post {post_id} is not a scheduled post.")
            at = post.scheduled_at.replace(tzinfo=UTC) if post.scheduled_at else None
            return post.schedule_status, post.schedule_json, at

    @staticmethod
    def _state_message(status: str | None, target: str) -> str:
        if status == "released":
            return "This post has already been released for publishing; its schedule can no longer change."
        action = {"cancelled": "cancelled", "scheduled": "rescheduled", "released": "published now"}[target]
        return f"A {status} post cannot be {action}."

    # --- views ---------------------------------------------------------------------------

    def view(self, post_id: int) -> ScheduleView:
        with self.database.session() as session:
            post = session.get(Post, post_id)
            if post is None:
                raise SchedulingError("not_found", f"Post {post_id} not found.")
            status, raw, at = post.schedule_status, post.schedule_json, post.scheduled_at
            video = post.video.filename if post.video is not None else ""
            audience = json.loads(post.audience_json).get("name") if post.audience_json else None
        data = json.loads(raw) if raw else {}
        jobs = self.store.jobs_for_post(post_id)
        counts = dict(Counter(job.status for job in jobs))
        scheduled_at = at.replace(tzinfo=UTC) if at else None
        zone = data.get("zone")
        local = scheduled_at.astimezone(ZoneInfo(zone)) if scheduled_at and zone else None
        overlap, fold = bool(data.get("dst_overlap")), int(data.get("fold", 0))
        note = (OVERLAP_NOTE if fold == 0 else SECOND_OCCURRENCE_NOTE) if overlap else None
        return ScheduleView(post_id, status, scheduled_at, local, zone, data.get("zone_source"), data.get("source"),
                            fold, overlap, note,
                            data.get("suggestion"), tuple(data.get("history", [])), len(jobs), counts,
                            batch_status([job.status for job in jobs]), status == "released", video, audience)

    def next_scheduled(self) -> ScheduleView | None:
        """The soonest post still waiting (status scheduled), or None. One query + one view."""
        with self.database.session() as session:
            row = (session.query(Post.id).filter(Post.schedule_status == "scheduled")
                   .order_by(Post.scheduled_at, Post.id).first())
        return self.view(row[0]) if row else None

    def scheduled(self, statuses: tuple[str, ...] = ("scheduled", "missed")) -> list[ScheduleView]:
        """Scheduled posts in the given states, soonest first."""
        with self.database.session() as session:
            ids = [row[0] for row in session.query(Post.id).filter(Post.schedule_status.in_(statuses))
                   .order_by(Post.scheduled_at, Post.id)]
        return [self.view(post_id) for post_id in ids]


__all__ = ["ERROR_MESSAGES", "MAX_AHEAD", "MIN_LEAD", "OVERLAP_NOTE", "ResolvedTime", "ScheduleView",
           "SchedulingError", "SchedulingService", "all_zones", "friendly_error"]
