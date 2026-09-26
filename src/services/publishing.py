"""Publishing service: the UI's only way to plan, create, run and inspect publishing batches.

Thin layer over the frozen engine (PublisherEngine / JobStore / adapters). It never talks to a
platform API, a tunnel or a worker pool itself; it only calls the engine and turns engine callbacks
into small UI events. Nothing here exposes tokens, temporary media URLs or container details.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.core.jobs import JobInfo
from src.core.publisher import (
    JobResult,
    PublisherEngine,
    batch_status,
    max_concurrent_publishes,
)
from src.core.validation import jpeg_info, validate_video_file
from src.platforms.base import redact

ACTIVE = ("uploading", "processing")
OPEN = ("pending", "retrying", "uploading", "processing")
PROVIDER_NAMES = {"cloudflare_tunnel": "Cloudflare Quick Tunnel", "tempfile": "TempFile.org", "s3": "S3",
                  "0x0": "0x0.st"}

# Technical error code -> what a user needs to know. The exact error stays available as "details".
FRIENDLY_ERRORS = {
    "processing_failed": "{platform} could not finish processing this video.",
    "media_storage": "The video could not be delivered to {platform} (media delivery failed).",
    "validation_failed": "The video, cover or options don't meet {platform}'s requirements.",
    "token_expired": "The account's login expired. Reconnect the account.",
    "unauthorized": "The account's login is no longer valid. Reconnect the account.",
    "insufficient_scope": "The account is missing a required permission. Reconnect and grant it.",
    "account_inactive": "The account is disconnected. Reconnect or enable it.",
    "forbidden": "{platform} refused the request (permission).",
    "quota_exceeded": "{platform}'s daily limit was reached. Try again later.",
    "rate_limited": "{platform} is rate limiting requests. Try again later.",
    "timeout": "{platform} did not answer in time.",
    "network": "Network problem while talking to {platform}.",
    "interrupted": "The upload was interrupted; its outcome is unknown. Check the account first.",
    "internal": "An unexpected error stopped this job.",
}


def friendly_error(platform: str, code: str | None, message: str | None) -> str:
    template = FRIENDLY_ERRORS.get(code or "")
    if template is None and message and "Cloudflare" in message:
        template = FRIENDLY_ERRORS["media_storage"]
    return (template or "Publishing failed.").format(platform=platform.title() if platform != "youtube" else "YouTube")


# ----------------------------------------------------------------------------------------------
# View models (plain data for the UI)
# ----------------------------------------------------------------------------------------------

@dataclass
class VideoInfo:
    path: str
    name: str
    size_bytes: int
    mime_type: str | None
    duration: float | None
    resolution: str | None
    ok: bool
    errors: list[str]
    warnings: list[str]


@dataclass
class CoverInfo:
    path: str
    name: str
    size_bytes: int
    width: int | None
    height: int | None
    ok: bool
    errors: list[str]


@dataclass
class DestinationView:
    account_id: int
    platform: str
    account_label: str
    ready: bool
    errors: list[str]
    notes: list[str]
    already_published: bool = False


@dataclass
class BatchPlan:
    video_path: str
    caption: str
    cover_path: str | None
    destinations: list[DestinationView]
    options: dict[int, dict]
    size_bytes: int = 0
    concurrency: int = 5
    provider: str | None = None           # media provider for Instagram (internal name)
    shared_tunnel: bool = False
    retry_delay: float = 5.0
    auto_retry: bool = True

    @property
    def valid(self) -> list[DestinationView]:
        return [d for d in self.destinations if d.ready]

    @property
    def invalid(self) -> list[DestinationView]:
        return [d for d in self.destinations if not d.ready]

    @property
    def instagram_jobs(self) -> int:
        return sum(1 for d in self.valid if d.platform == "instagram")

    @property
    def initial_active(self) -> int:
        return min(self.concurrency, self.instagram_jobs)

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAMES.get(self.provider or "", self.provider or "none available")

    @property
    def transfer_bytes(self) -> int:
        cover = Path(self.cover_path).stat().st_size if self.cover_path and Path(self.cover_path).is_file() else 0
        return (self.size_bytes + cover) * self.instagram_jobs

    def count(self, platform: str) -> int:
        return sum(1 for d in self.valid if d.platform == platform)


@dataclass
class JobView:
    job_id: int
    platform: str
    account_label: str
    status: str
    attempts: int
    media_id: str | None
    url: str | None
    error: str | None           # friendly
    error_details: str | None   # exact (redacted) technical error
    auto_retried: bool
    cover_status: str | None


@dataclass
class BatchView:
    post_id: int
    video: str
    cover: str | None
    caption: str
    platforms: list[str]
    created_at: datetime | None
    counts: dict[str, int]
    status: str
    jobs: list[JobView] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def done(self) -> int:
        return self.counts.get("published", 0) + self.counts.get("failed", 0)

    @property
    def progress(self) -> float:
        return self.done / self.total if self.total else 0.0


@dataclass
class BatchEvent:
    """batch_started | job_uploading | job_processing | job_published | job_failed | job_retrying |
    retry_started | batch_finished"""

    kind: str
    job_id: int | None = None
    platform: str | None = None
    account: str | None = None
    status: str | None = None
    message: str | None = None            # friendly, safe to show
    details: str | None = None            # redacted technical detail (on request)
    summary: dict | None = None


# ----------------------------------------------------------------------------------------------

class PublishingService:
    def __init__(self, engine: PublisherEngine, account_manager):
        self.engine = engine
        self.accounts = account_manager
        self.store = engine.store

    # --- inputs --------------------------------------------------------------------------

    @staticmethod
    def inspect_video(path: str) -> VideoInfo:
        clean = path.strip().strip('"')
        check = validate_video_file(clean)
        media = check.media
        resolution = f"{media.width}×{media.height}" if media and media.width and media.height else None
        return VideoInfo(clean, Path(clean).name, media.size_bytes if media else 0, media.mime_type if media else None,
                         media.duration_seconds if media else None, resolution, check.ok, list(check.errors),
                         list(check.warnings))

    @staticmethod
    def inspect_cover(path: str) -> CoverInfo:
        clean = path.strip().strip('"')
        p = Path(clean)
        if p.suffix.lower() not in (".jpg", ".jpeg"):
            errors, dims = [f"{p.name}: covers must be JPEG (.jpg)"], None
        else:
            errors, dims = jpeg_info(p, 8 * 1024 * 1024)
        size = p.stat().st_size if p.is_file() else 0
        return CoverInfo(str(p.resolve()) if p.is_file() else clean, p.name, size, dims[0] if dims else None,
                         dims[1] if dims else None, not errors, errors)

    # --- plan / create / run ------------------------------------------------------------------

    def plan(self, video_path: str, caption: str, cover_path: str | None, account_ids: list[int],
             platform_options: dict[str, dict] | None = None) -> BatchPlan:
        """Validate everything locally (no network, no jobs, no tunnel). The same cover for every account."""
        from src.media_storage import delivery_provider

        platform_options = platform_options or {}
        options: dict[int, dict] = {}
        for account_id in account_ids:
            account = self.accounts.get_account(account_id)
            opts = dict(platform_options.get(account.platform, {}))
            adapter = self.engine.publishers.get(account.platform)
            if cover_path and adapter is not None and adapter.cover_plan(cover_path)[0] == "upload":
                opts["cover_path"] = cover_path  # one local file for every account; never copied
            options[account_id] = opts
        items = self.engine.plan_destinations(video_path, caption, [(a, options[a]) for a in account_ids])
        destinations = []
        for account_id, item in zip(account_ids, items):
            destinations.append(DestinationView(
                account_id, item.platform, item.account_label, item.ready, list(item.errors), list(item.notes),
                already_published=self.store.already_published(video_path, account_id) if Path(video_path).is_file() else False))
        size = Path(video_path).stat().st_size if Path(video_path).is_file() else 0
        provider = delivery_provider(size, bool(cover_path)) if any(d.platform == "instagram" for d in destinations) else None
        return BatchPlan(video_path, caption, cover_path, destinations, options, size,
                         self.engine.max_concurrent or max_concurrent_publishes(), provider,
                         provider == "cloudflare_tunnel", PublisherEngine._retry_delay())

    def create_batch(self, plan: BatchPlan, include_already_published: bool = False) -> int:
        """One post (= batch) with one job per valid destination. Duplicates are skipped unless asked."""
        destinations = [(d.account_id, plan.options[d.account_id]) for d in plan.valid
                        if include_already_published or not d.already_published]
        post_id = self.store.create_post(plan.video_path, plan.caption, destinations, auto_retry=plan.auto_retry)
        for job in self.store.jobs_for_post(post_id):
            if job.options.get("cover_path"):
                self.store.set_cover_status(job.id, "pending")
        return post_id

    def publish_batch(self, post_id: int, on_event: Callable[[BatchEvent], None] | None = None,
                      retry_job_id: int | None = None) -> BatchView:
        """Run a batch (blocking; call it from a worker thread). Events come from engine worker threads."""
        emit = on_event or (lambda event: None)
        lock = threading.Lock()
        before = {job.id: job.status for job in self.store.jobs_for_post(post_id)}
        state = {"retry_started": False, "retry_ids": set()}
        emit(BatchEvent("batch_started", summary=self._counts(before)))

        def on_update(result: JobResult) -> None:
            with lock:
                before[result.job_id] = result.status
                if result.status == "retrying" and result.error == "automatic retry":
                    state["retry_ids"].add(result.job_id)
                    if not state["retry_started"]:
                        state["retry_started"] = True
                        initial = self._counts(before)
                        initial["retrying"] = initial.get("retrying", 0)
                        emit(BatchEvent("retry_started", summary={
                            "published": initial.get("published", 0),
                            "failed": initial.get("failed", 0) + initial.get("retrying", 0)}))
                emit(self._job_event(result))

        if retry_job_id is not None:
            self.engine.retry_job(retry_job_id, on_update=on_update)
        else:
            self.engine.publish_post(post_id, on_update=on_update)
        view = self.batch(post_id)
        links = view.counts.get("published", 0)
        saved = sum(1 for job in view.jobs if job.status == "published" and job.url)
        recovered = sum(1 for job in view.jobs if job.job_id in state["retry_ids"] and job.status == "published")
        emit(BatchEvent("batch_finished", summary={
            "published": view.counts.get("published", 0), "failed": view.counts.get("failed", 0),
            "processing": sum(view.counts.get(s, 0) for s in OPEN), "links_saved": saved,
            "published_total": links, "retried": len(state["retry_ids"]), "recovered": recovered,
            "still_failed": len(state["retry_ids"]) - recovered, "status": view.status}))
        return view

    def retry_job(self, job_id: int, on_event: Callable[[BatchEvent], None] | None = None) -> BatchView:
        """Explicit manual retry (the engine then never retries this job automatically)."""
        job = self.store.get_job(job_id)
        return self.publish_batch(job.post_id, on_event, retry_job_id=job_id)

    def resume_open(self, on_event: Callable[[BatchEvent], None] | None = None) -> list[BatchView]:
        post_ids = sorted(set(self.store.open_post_ids()) | set(self.store.retry_owed_post_ids(("instagram",))))
        return [self.publish_batch(post_id, on_event) for post_id in post_ids]

    # --- reads -----------------------------------------------------------------------------

    def batch(self, post_id: int) -> BatchView:
        post, video = self.store.get_post(post_id)
        jobs = self.store.jobs_for_post(post_id)
        links = self._links_by_media_id()
        views = [self._job_view(job, links) for job in jobs]
        covers = {Path(j.options["cover_path"]).name for j in jobs if j.options.get("cover_path")}
        counts = Counter(job.status for job in jobs)
        return BatchView(post_id, video.filename, ", ".join(sorted(covers)) or None, post.caption or "",
                         sorted({j.platform for j in jobs}), post.created_at, dict(counts),
                         batch_status([j.status for j in jobs]), views)

    def batches(self, limit: int = 30) -> list[BatchView]:
        post_ids = []
        for job in self.store.recent_jobs(limit=2000):
            if job.post_id not in post_ids:
                post_ids.append(job.post_id)
            if len(post_ids) >= limit:
                break
        return [self.batch(post_id) for post_id in post_ids]

    def history(self, platform: str | None = None, status: str | None = None, search: str = "",
                limit: int = 500) -> list[dict]:
        rows = []
        videos: dict[int, str] = {}
        needle = search.strip().lower()
        for job in self.store.recent_jobs(limit=limit):
            if platform and job.platform != platform:
                continue
            if status and job.status != status:
                continue
            if job.post_id not in videos:
                _, video = self.store.get_post(job.post_id)
                videos[job.post_id] = video.filename
            row = {"job_id": job.id, "post_id": job.post_id, "video": videos[job.post_id], "platform": job.platform,
                   "account": job.account_label, "status": job.status, "date": self._job_date(job.id)}
            if needle and needle not in " ".join(str(v) for v in row.values()).lower():
                continue
            rows.append(row)
        return rows

    def delete_history(self, job_ids: list[int]) -> int:
        """Delete FINISHED jobs (published/failed) and their attempts from history; batches left empty go too.

        Open jobs (pending/uploading/processing/retrying) are never deleted. Posts on the platforms and the
        permanent links in content/published_links stay. Deleted published jobs no longer trigger the
        "already published to this account" warning.
        """
        from sqlalchemy import func

        from src.storage.database import Post, PublishJob

        deleted = 0
        with self.store.database.session() as session:
            posts = set()
            for job_id in job_ids:
                job = session.get(PublishJob, job_id)
                if job is None or job.status not in ("published", "failed"):
                    continue
                posts.add(job.post_id)
                session.delete(job)  # attempts are deleted with it (cascade)
                deleted += 1
            session.flush()
            for post_id in posts:
                if not session.query(func.count(PublishJob.id)).filter_by(post_id=post_id).scalar():
                    post = session.get(Post, post_id)
                    if post is not None:
                        session.delete(post)
            session.commit()
        return deleted

    def queue_counts(self) -> dict[str, int]:
        counts = Counter(job.status for job in self.store.recent_jobs(limit=100_000))
        return {s: counts.get(s, 0) for s in ("pending", "uploading", "processing", "retrying", "published", "failed")}

    def recent_activity(self, limit: int = 8) -> list[JobView]:
        links = self._links_by_media_id()
        return [self._job_view(job, links, attempts=False) for job in self.store.recent_jobs(limit=limit)]

    # --- helpers -----------------------------------------------------------------------------

    @staticmethod
    def _counts(statuses: dict[int, str]) -> dict[str, int]:
        return dict(Counter(statuses.values()))

    def _job_event(self, result: JobResult) -> BatchEvent:
        kind = {"uploading": "job_uploading", "processing": "job_processing", "published": "job_published",
                "failed": "job_failed", "retrying": "job_retrying"}.get(result.status, "job_" + result.status)
        message = details = None
        if result.status == "failed":
            error = self.store.last_error(result.job_id)
            message = friendly_error(result.platform, error.get("code"), result.error)
            details = redact(result.error or error.get("message") or "") or None
        return BatchEvent(kind, result.job_id, result.platform, result.account_label, result.status, message, details)

    def _job_view(self, job: JobInfo, links: dict, attempts: bool = True) -> JobView:
        error = details = None
        if job.status == "failed":
            last = self.store.last_error(job.id)
            error = friendly_error(job.platform, last.get("code"), job.error_message)
            details = redact(job.error_message or last.get("message") or "") or None
        return JobView(job.id, job.platform, job.account_label, job.status,
                       len(self.store.attempts(job.id)) if attempts else 0, job.platform_media_id,
                       links.get((job.platform, job.platform_media_id)), error, details, job.auto_retry_used,
                       job.cover_status)

    def _links_by_media_id(self) -> dict:
        links = self.engine.links
        if links is None:
            return {}
        return {(p, r.get("provider_id")): r.get("url") for p in ("instagram", "youtube", "tiktok")
                for r in links.records(p)}

    def _job_date(self, job_id: int):
        from src.storage.database import PublishJob

        with self.store.database.session() as session:
            row = session.get(PublishJob, job_id)
            return row.published_at or row.created_at if row else None
