"""Due scheduler (V2.2 phase 3): one pass over scheduled posts. No thread, no timer, no UI.

    scheduled ── now > scheduled_at + grace ──────────────► missed     (never published automatically)
    scheduled ── scheduled_at <= now <= scheduled_at + grace ──► released ──► PublishingService.publish_batch

Works only in UTC (``posts.scheduled_at`` is naive UTC). Every transition is ONE conditional UPDATE that
also checks the status, the time window and the current ``schedule_json`` (compare-and-swap), so a
concurrent cancel / reschedule / publish-now or another scheduler run can never be overwritten, and a post
is published only by the run that won its release. Due posts are released and published one at a time,
oldest first (scheduled_at, id). Once released, publishing outcomes belong to the normal job state machine:
a publishing failure never puts the post back to ``scheduled``.

The whole pass (missed marking, releases and publishing) runs under the process-level publishing lock
(lock first, then database work). If another Soc_bot window holds it, the run returns at once with
``busy=True`` and changes nothing: no post is marked missed, released or published.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from src.core.publish_lock import (
    BUSY_MESSAGE,
    PublishLock,
    PublishLockBusy,
    default_lock,
)
from src.platforms.base import redact
from src.services.scheduling import _dump, _iso
from src.storage.database import Post

UTC = timezone.utc
MISSED_GRACE = timedelta(minutes=60)
MAX_POSTS_PER_RUN = 1000  # safety bound for one pass; anything left is handled by the next run
POLL_DEFAULT, POLL_MIN, POLL_MAX = 30, 10, 300  # SOC_BOT_SCHEDULER_POLL_SECONDS (TUI timer)
log = logging.getLogger("soc_bot.scheduler")


def scheduler_poll_seconds() -> int:
    """SOC_BOT_SCHEDULER_POLL_SECONDS: whole seconds 10..300; missing -> 30; anything else -> 30 (logged)."""
    raw = (os.environ.get("SOC_BOT_SCHEDULER_POLL_SECONDS") or "").strip()
    if not raw:
        return POLL_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or not POLL_MIN <= value <= POLL_MAX:
        log.warning("SOC_BOT_SCHEDULER_POLL_SECONDS=%r is not a whole number from %s to %s; using %s.",
                    raw, POLL_MIN, POLL_MAX, POLL_DEFAULT)
        return POLL_DEFAULT
    return value


@dataclass(frozen=True)
class DueEvent:
    """missed | released | publishing_started | published | failed | skipped | error | busy"""

    kind: str
    post_id: int | None = None
    scheduled_at: datetime | None = None   # aware UTC
    status: str | None = None              # batch status after publishing
    message: str | None = None


@dataclass
class DueRunResult:
    started_at: datetime
    finished_at: datetime | None = None
    released: list[int] = field(default_factory=list)
    published: list[int] = field(default_factory=list)    # batch completed
    failed: list[int] = field(default_factory=list)       # batch failed / partly failed / error while publishing
    missed: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)      # lost a race: another actor changed the post first
    errors: list[DueEvent] = field(default_factory=list)  # malformed rows, publish exceptions, callback errors
    events: list[DueEvent] = field(default_factory=list)
    busy: bool = False                                    # publishing lock held elsewhere: nothing was touched


class DueScheduler:
    def __init__(self, publishing, clock: Callable[[], datetime] | None = None, grace: timedelta = MISSED_GRACE,
                 max_posts: int = MAX_POSTS_PER_RUN, lock: PublishLock | None = None):
        self.publishing = publishing          # PublishingService: the only way this scheduler publishes
        self.database = publishing.store.database
        # The same lock PublishingService uses (re-entrant for this thread, so publish_batch can nest).
        self.lock = lock or getattr(publishing, "lock", None) or default_lock()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.grace = grace
        self.max_posts = max_posts

    def has_work(self, now: datetime | None = None) -> bool:
        """Read-only, no lock: is any scheduled post due or overdue at ``now``? Lets a polling caller skip
        taking the publishing lock (and so never block a manual publish) when there is nothing to do."""
        high = self._naive(self._utc(now if now is not None else self.clock()))
        with self.database.session() as session:
            return session.query(Post.id).filter(Post.schedule_status == "scheduled", Post.scheduled_at.isnot(None),
                                                 Post.scheduled_at <= high).first() is not None

    def run_due(self, now: datetime | None = None, on_event: Callable[[DueEvent], None] | None = None) -> DueRunResult:
        now = self._utc(now if now is not None else self.clock())
        result = DueRunResult(started_at=now)

        def emit(event: DueEvent) -> None:
            result.events.append(event)
            _log_event(event)
            if event.kind == "error":
                result.errors.append(event)
            if on_event is None:
                return
            try:
                on_event(event)
            except Exception as e:  # noqa: BLE001 - informational callback; state changes already happened
                result.errors.append(DueEvent("error", event.post_id, message=f"event callback failed: {type(e).__name__}"))

        try:
            self.lock.acquire()
        except PublishLockBusy:
            result.busy = True
            emit(DueEvent("busy", message=BUSY_MESSAGE))
            result.finished_at = self._utc(self.clock())
            return result
        log.info("Scheduler pass started (%s UTC).", f"{now:%Y-%m-%d %H:%M:%S}")
        try:
            self._report_malformed(emit)
            self._mark_missed(now, result, emit)
            attempted: set[int] = set()
            while len(attempted) < self.max_posts:
                candidate = self._next_due(now, attempted)
                if candidate is None:
                    break
                post_id, scheduled_at, raw = candidate
                attempted.add(post_id)
                self._release_and_publish(post_id, scheduled_at, raw, now, result, emit)
        except KeyboardInterrupt:
            log.warning("Scheduler pass interrupted (Ctrl+C); unfinished jobs stay open for a later resume.")
            raise
        finally:
            self.lock.release()
        result.finished_at = self._utc(self.clock())
        log.info("Scheduler pass finished: released %d, published %d, failed %d, missed %d, skipped %d, errors %d.",
                 len(result.released), len(result.published), len(result.failed), len(result.missed),
                 len(result.skipped), len(result.errors))
        return result

    # --- steps ---------------------------------------------------------------------------

    def _mark_missed(self, now: datetime, result: DueRunResult, emit) -> None:
        cutoff = self._naive(now - self.grace)
        with self.database.session() as session:
            rows = (session.query(Post.id, Post.scheduled_at, Post.schedule_json)
                    .filter(Post.schedule_status == "scheduled", Post.scheduled_at.isnot(None), Post.scheduled_at < cutoff)
                    .order_by(Post.scheduled_at, Post.id).limit(self.max_posts).all())
        for post_id, scheduled_at, raw in rows:
            at = scheduled_at.replace(tzinfo=UTC)
            outcome = self._swap(post_id, raw, "missed", now, at, Post.scheduled_at < cutoff, emit)
            if outcome == "won":
                result.missed.append(post_id)
                emit(DueEvent("missed", post_id, at, message="Missed its publishing window; not published automatically."))
            elif outcome == "lost":
                result.skipped.append(post_id)
                emit(DueEvent("skipped", post_id, at, message="Changed by someone else; left as it is."))

    def _next_due(self, now: datetime, exclude: set[int]):
        """Oldest scheduled post inside its due window (scheduled_at, id), or None."""
        low, high = self._naive(now - self.grace), self._naive(now)
        with self.database.session() as session:
            query = (session.query(Post.id, Post.scheduled_at, Post.schedule_json)
                     .filter(Post.schedule_status == "scheduled", Post.scheduled_at >= low, Post.scheduled_at <= high))
            if exclude:
                query = query.filter(Post.id.not_in(exclude))
            row = query.order_by(Post.scheduled_at, Post.id).first()
        return (row[0], row[1].replace(tzinfo=UTC), row[2]) if row else None

    def _release_and_publish(self, post_id: int, at: datetime, raw: str | None, now: datetime,
                             result: DueRunResult, emit) -> None:
        window = (Post.scheduled_at >= self._naive(now - self.grace)) & (Post.scheduled_at <= self._naive(now))
        outcome = self._swap(post_id, raw, "released", now, at, window, emit)
        if outcome == "malformed":
            return
        if outcome == "lost":
            result.skipped.append(post_id)
            emit(DueEvent("skipped", post_id, at, message="Changed by someone else before release; not published here."))
            return
        result.released.append(post_id)
        emit(DueEvent("released", post_id, at))
        emit(DueEvent("publishing_started", post_id, at))
        try:
            view = self.publishing.publish_batch(post_id)
        except Exception as e:  # noqa: BLE001 - one post's failure must not stop the run; post stays released
            result.failed.append(post_id)
            emit(DueEvent("error", post_id, at, message=f"Publishing stopped: {type(e).__name__}: {e}"))
            return
        if view.status == "completed":
            result.published.append(post_id)
            emit(DueEvent("published", post_id, at, status=view.status))
        else:
            result.failed.append(post_id)
            emit(DueEvent("failed", post_id, at, status=view.status))

    def _swap(self, post_id: int, raw: str | None, target: str, now: datetime, at: datetime, condition, emit) -> str:
        """scheduled -> target in ONE conditional UPDATE (status + time condition + unchanged schedule_json),
        appending a history entry. Returns "won", "lost" (another actor got there first) or "malformed"."""
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = None
        if not isinstance(data, dict):
            emit(DueEvent("error", post_id, at, message="Malformed schedule data; not changed and not published."))
            return "malformed"
        entry = {"at": _iso(now), "action": target, "from_utc": _iso(at), "to_utc": None}
        new = {**data, "history": list(data.get("history", [])) + [entry]}
        same_json = Post.schedule_json == raw if raw is not None else Post.schedule_json.is_(None)
        with self.database.session() as session:
            changed = session.execute(
                update(Post)
                .where(Post.id == post_id, Post.schedule_status == "scheduled", condition, same_json)
                .values(schedule_status=target, schedule_json=_dump(new))).rowcount
            session.commit()
        return "won" if changed == 1 else "lost"

    def _report_malformed(self, emit) -> None:
        """Scheduled rows without a time can never be due: report them, never touch or publish them."""
        with self.database.session() as session:
            ids = [r[0] for r in session.query(Post.id).filter(Post.schedule_status == "scheduled",
                                                                Post.scheduled_at.is_(None)).order_by(Post.id)]
        for post_id in ids:
            emit(DueEvent("error", post_id, message="Scheduled post has no scheduled time; not published."))

    # --- time ----------------------------------------------------------------------------

    @staticmethod
    def _utc(moment: datetime) -> datetime:
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return moment.astimezone(UTC)

    @staticmethod
    def _naive(moment: datetime) -> datetime:
        return moment.astimezone(UTC).replace(tzinfo=None)


def _log_event(event: DueEvent) -> None:
    """One concise, redacted log line per scheduler outcome (logs/soc_bot.log in the app and with --run-due)."""
    when = f" (scheduled {event.scheduled_at:%Y-%m-%d %H:%M} UTC)" if event.scheduled_at else ""
    post = f"post #{event.post_id}{when}" if event.post_id is not None else "scheduler"
    message = redact(event.message)
    if event.kind == "released":
        log.info("Scheduled %s released for publishing.", post)
    elif event.kind == "published":
        log.info("Scheduled %s published.", post)
    elif event.kind == "failed":
        log.warning("Scheduled %s finished with problems (%s).", post, event.status)
    elif event.kind == "missed":
        log.warning("Scheduled %s missed its window; not published automatically.", post)
    elif event.kind == "skipped":
        log.info("Scheduled %s skipped: %s", post, message)
    elif event.kind == "busy":
        log.info("Scheduler pass skipped: %s", message)
    elif event.kind == "error":
        log.error("Scheduler error, %s: %s", post, message or "unknown error")


__all__ = ["MAX_POSTS_PER_RUN", "MISSED_GRACE", "POLL_DEFAULT", "POLL_MAX", "POLL_MIN", "DueEvent", "DueRunResult",
           "DueScheduler", "scheduler_poll_seconds"]
