"""Platform-independent publishing engine.

Each destination is its own job and runs in isolation: one platform failing never stops or
rolls back another. No platform HTTP lives here; adapters implement PlatformPublisher.
"""

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.accounts.manager import AccountManager
from src.auth.base import is_token_expiring
from src.core.jobs import TRANSITIONS, JobError, JobInfo, JobStore
from src.core.validation import MediaInfo, validate_video_file
from src.platforms.base import PlatformPublisher, PublishContext, PublishError, redact
from src.storage.database import Database

UpdateCallback = Callable[["JobResult"], None]
log = logging.getLogger("soc_bot.engine")

# Platforms whose jobs of one post run concurrently (each job fully isolated: own media session,
# own container, own result). Everything else stays sequential.
PARALLEL_PLATFORMS = ("instagram",)
DEFAULT_MAX_CONCURRENT = 5
DEFAULT_RETRY_ROUND_DELAY = 5.0  # INSTAGRAM_FAILURE_RETRY_DELAY_SECONDS: pause before the automatic retry round
MAX_CONCURRENT_LIMIT = 20


def max_concurrent_publishes() -> int:
    """INSTAGRAM_MAX_CONCURRENT_PUBLISHES (default 5, allowed 1..20)."""
    try:
        value = int(os.environ.get("INSTAGRAM_MAX_CONCURRENT_PUBLISHES") or DEFAULT_MAX_CONCURRENT)
    except ValueError:
        return DEFAULT_MAX_CONCURRENT
    return value if 1 <= value <= MAX_CONCURRENT_LIMIT else DEFAULT_MAX_CONCURRENT


def granted_scopes(account) -> list[str] | None:
    """Scopes recorded at connect time (account.meta_json), or None if never recorded."""
    try:
        scopes = json.loads(account.meta_json or "{}").get("scopes")
    except (ValueError, AttributeError):
        return None
    return list(scopes) if isinstance(scopes, list) else None


def default_publishers() -> dict[str, PlatformPublisher]:
    from src.platforms.instagram.publisher import InstagramPublisher
    from src.platforms.tiktok.publisher import TikTokPublisher
    from src.platforms.youtube.publisher import YouTubePublisher

    return {"instagram": InstagramPublisher(), "tiktok": TikTokPublisher(), "youtube": YouTubePublisher()}


@dataclass
class JobResult:
    job_id: int
    platform: str
    account_label: str
    status: str
    platform_media_id: str | None = None
    error: str | None = None


@dataclass
class PostResult:
    post_id: int
    jobs: list[JobResult] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for j in self.jobs if j.status == status)

    @property
    def status(self) -> str:
        return batch_status([j.status for j in self.jobs])


def batch_status(statuses: list[str]) -> str:
    """Aggregate of a post's jobs (the batch is only a coordinator; jobs keep their own states)."""
    if not statuses or all(s == "pending" for s in statuses):
        return "pending"
    if any(s in ("pending", "retrying", "uploading", "processing") for s in statuses):
        return "running"
    if all(s == "published" for s in statuses):
        return "completed"
    if all(s == "failed" for s in statuses):
        return "failed"
    return "completed_with_failures"


@dataclass
class PlanItem:
    """Dry-run view of one destination: what would happen, with no network calls."""

    platform: str
    account_label: str
    account_status: str
    job_status: str
    errors: list[str]
    notes: list[str]

    @property
    def ready(self) -> bool:
        return not self.errors


class PublisherEngine:
    """Runs publish jobs through platform adapters with retries, token renewal and attempt records."""

    def __init__(
        self,
        database: Database,
        account_manager: AccountManager,
        auth_manager: Any = None,
        publishers: dict[str, PlatformPublisher] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retry_delays: tuple[float, ...] = (30, 60, 120),
        probe_media: bool = True,
        links=None,
        max_concurrent: int | None = None,
    ):
        self.store = JobStore(database)
        self.links = links  # PublishedLinks or None (off)
        self.account_manager = account_manager
        self.auth_manager = auth_manager
        self._publishers = publishers
        self.sleep = sleep
        self.retry_delays = retry_delays  # max retries == len(retry_delays)
        self.probe_media = probe_media
        self.max_concurrent = max_concurrent  # None: INSTAGRAM_MAX_CONCURRENT_PUBLISHES at publish time

    @property
    def publishers(self) -> dict[str, PlatformPublisher]:
        if self._publishers is None:
            self._publishers = default_publishers()
        return self._publishers

    # --- dry run ---------------------------------------------------------------

    def plan_post(self, post_id: int) -> list[PlanItem]:
        """Validate every destination of a saved post. Never contacts a provider or refreshes tokens."""
        post, video = self.store.get_post(post_id)
        check = validate_video_file(video.path, probe=self.probe_media)
        return [self._plan_item(job, check.media, check.errors, post.caption or "") for job in self.store.jobs_for_post(post_id)]

    def plan_destinations(self, video_path: str, caption: str, destinations: list[tuple[int, dict]]) -> list[PlanItem]:
        """Validate a post before it is saved (CLI preview). No network, no database writes."""
        check = validate_video_file(video_path, probe=self.probe_media)
        items = []
        for account_id, options in destinations:
            account = self.account_manager.get_account(account_id)
            job = JobInfo(0, 0, account.id, account.platform, account.display_name or account.username,
                          account.status, "pending", 0, None, None, options)
            items.append(self._plan_item(job, check.media, check.errors, caption))
        return items

    def _plan_item(self, job: JobInfo, media: MediaInfo | None, media_errors: list[str], caption: str) -> PlanItem:
        errors = list(media_errors)
        notes: list[str] = []
        if job.account_status != "active":
            errors.append(f"Account is {job.account_status}")
        adapter = self.publishers.get(job.platform)
        if adapter is None:
            errors.append(f"No publisher for platform {job.platform}")
        elif media is not None:
            errors.extend(adapter.validate(caption, job.options, media))
            notes.extend(adapter.delivery_notes(job.options, media))
            account = self.account_manager.get_account(job.account_id)
            missing = adapter.missing_scopes(granted_scopes(account))
            if missing:
                errors.append("Account is missing required permission: " + "; ".join(missing) + " (reconnect and grant it)")
            if is_token_expiring(account.expires_at, 0):
                notes.append("access token expired: would try to renew before publishing")
            elif is_token_expiring(account.expires_at, adapter.TOKEN_REFRESH_MARGIN):
                notes.append("access token expiring soon: would renew before publishing")
        if job.status not in ("pending", "retrying"):
            notes.append(f"job is {job.status}: would not be started again")
        return PlanItem(job.platform, job.account_label, job.account_status, job.status, errors, notes)

    def preflight(self, account_id: int, options: dict, media: MediaInfo, caption: str) -> tuple[list[str], list[str]]:
        """Read-only provider checks for one destination before confirmation (not used in dry-run).

        May renew an expiring token (same rules as publishing). Returns (notes, errors); never raises.
        """
        account = self.account_manager.get_account(account_id)
        adapter = self.publishers.get(account.platform)
        if adapter is None:
            return [], [f"No publisher for platform {account.platform}"]
        job = JobInfo(0, 0, account.id, account.platform, account.display_name or account.username,
                      account.status, "pending", 0, None, None, options)
        try:
            ctx = PublishContext(job_id=0, video_path=media.path, caption=caption, options=options,
                                 access_token=self._access_token(job, adapter),
                                 platform_account_id=account.platform_account_id, media=media)
            return adapter.preflight(ctx)
        except PublishError as e:
            return [], [str(e)]

    # --- publishing ------------------------------------------------------------

    def publish_post(self, post_id: int, on_update: UpdateCallback | None = None) -> PostResult:
        """Publish every destination of a post (= one batch) independently and return the aggregate result.

        Instagram (PARALLEL_PLATFORMS) jobs:
          1. share ONE media session for the whole batch (one Quick Tunnel, not one per account);
          2. run in a pool of at most ``max_concurrent`` workers (a freed slot is refilled immediately);
          3. when the initial round is fully drained, the batch's FAILED jobs get ONE automatic retry round
             (posts created with auto_retry), in the same pool and on the same media session;
          4. the session is closed once, after the retry round.
        Other platforms run one after another. Published/failed jobs are never started again, so calling
        this again resumes a batch (including a retry round that was owed when the app stopped).
        """
        post, video = self.store.get_post(post_id)
        media, media_errors = self._media(video)
        caption = post.caption or ""
        jobs = self.store.jobs_for_post(post_id)
        lock = threading.Lock()
        results: dict[int, JobResult] = {}

        def notify(update: JobResult) -> None:  # one line at a time from concurrent workers
            if on_update:
                with lock:
                    on_update(update)

        def run(job: JobInfo, media_provider=None) -> JobResult:
            try:
                return self._run_job(job, media, media_errors, caption, notify, media_provider)
            except Exception as e:  # noqa: BLE001 - isolate destinations: one bug must not stop the others
                return self._fail(job, PublishError(f"Unexpected error: {type(e).__name__}", code="internal"),
                                  attempt_id=None, on_update=notify)

        parallel = [job for job in jobs if job.platform in PARALLEL_PLATFORMS]
        parallel_ids = {job.id for job in parallel}
        for job in jobs:
            if job.id not in parallel_ids:
                results[job.id] = run(job)

        sessions = self._batch_sessions(parallel)
        try:
            initial = [job for job in parallel if job.status not in ("published", "failed")]
            if initial:
                log.info("INSTAGRAM BATCH #%s: %s job(s), at most %s active at a time, ONE shared media session "
                         "(started by the first job that needs it).", post_id, len(initial), self._limit())
            self._run_pool(initial, run, sessions, results)
            retry = self._retry_candidates(post_id)
            if retry:
                done = self.store.jobs_for_post(post_id)
                log.info("INITIAL ROUND COMPLETE: Published %s, Failed %s. Starting automatic retry round for %s "
                         "job(s) in %ss (same shared media session).",
                         sum(j.status == "published" for j in done if j.id in parallel_ids),
                         sum(j.status == "failed" for j in done if j.id in parallel_ids), len(retry), self._retry_delay())
                self.sleep(self._retry_delay())
                for job in retry:
                    self.store.mark_auto_retry_used(job.id)  # before running: a crash never earns a second one
                    self.store.transition(job.id, "retrying", next_retry_at=None)
                    self._notify(notify, job, "retrying", error="automatic retry")
                self._run_pool([self.store.get_job(job.id) for job in retry], run, sessions, results)
                final = {j.id: j.status for j in self.store.jobs_for_post(post_id)}
                recovered = sum(final[job.id] == "published" for job in retry)
                log.info("RETRY ROUND COMPLETE: Recovered %s, Still failed %s.", recovered, len(retry) - recovered)
        finally:
            for session in sessions.values():
                session.close()
        for job in parallel:
            if job.id not in results:
                results[job.id] = self._result(self.store.get_job(job.id))
        return PostResult(post_id, [results[job.id] for job in jobs])

    def _limit(self) -> int:
        return self.max_concurrent or max_concurrent_publishes()

    @staticmethod
    def _retry_delay() -> float:
        try:
            delay = float(os.environ.get("INSTAGRAM_FAILURE_RETRY_DELAY_SECONDS") or DEFAULT_RETRY_ROUND_DELAY)
        except ValueError:
            return DEFAULT_RETRY_ROUND_DELAY
        return delay if 0 <= delay <= 300 else DEFAULT_RETRY_ROUND_DELAY

    def _batch_sessions(self, jobs: list[JobInfo]) -> dict:
        """One shared media session per platform for this batch (lazy: nothing starts until a job needs it)."""
        sessions = {}
        for platform in {job.platform for job in jobs}:
            adapter = self.publishers.get(platform)
            session = adapter.batch_media() if adapter is not None else None
            if session is not None:
                sessions[platform] = session
        return sessions

    def _run_pool(self, jobs: list[JobInfo], run, sessions: dict, results: dict[int, JobResult]) -> None:
        """At most ``max_concurrent`` jobs at once; the next job starts as soon as any slot frees up."""
        if not jobs:
            return
        pool = ThreadPoolExecutor(max_workers=min(self._limit(), len(jobs)), thread_name_prefix="soc_bot-publish")
        try:
            futures = {pool.submit(run, job, sessions.get(job.platform)): job.id for job in jobs}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        except KeyboardInterrupt:
            # Queued jobs are not started (they stay pending/retrying: resume later). Running jobs finish their
            # current step; the shared media session is closed after them.
            log.warning("Stopping: queued jobs stay pending; waiting for active jobs to finish.")
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        finally:
            pool.shutdown(wait=True)

    def _retry_candidates(self, post_id: int) -> list[JobInfo]:
        """This batch's failed Instagram jobs that still have their one automatic retry.

        Failures whose outcome is uncertain (the provider may have received the post) are not retried
        automatically, they need a manual check; their automatic retry is marked as used.
        """
        if not self.store.post_auto_retry(post_id):
            return []
        candidates = []
        for job in self.store.jobs_for_post(post_id):
            if job.platform not in PARALLEL_PLATFORMS or job.status != "failed" or job.auto_retry_used:
                continue
            if self.store.last_error(job.id).get("uncertain"):
                log.warning("Job %s: outcome uncertain, not retried automatically (check the account first).", job.id)
                self.store.mark_auto_retry_used(job.id)
                continue
            candidates.append(job)
        return candidates

    def retry_job(self, job_id: int, on_update: UpdateCallback | None = None) -> JobResult:
        """Manually retry a failed job (failed -> retrying -> run).

        A manual retry takes over the job's automatic retry: automation never retries it afterwards.
        """
        job = self.store.get_job(job_id)
        if job.status != "failed":
            raise JobError(f"Only failed jobs can be retried (job is {job.status})")
        self.store.mark_auto_retry_used(job_id)
        self.store.transition(job_id, "retrying", retry_count=0, next_retry_at=None)
        post, video = self.store.get_post(job.post_id)
        media, media_errors = self._media(video)
        adapter = self.publishers.get(job.platform)
        session = adapter.batch_media() if adapter is not None else None
        try:
            return self._run_job(self.store.get_job(job_id), media, media_errors, post.caption or "", on_update,
                                 session)
        finally:
            if session is not None:
                session.close()

    def resume_open_jobs(self, on_update: UpdateCallback | None = None) -> list[PostResult]:
        """Continue every post with pending/retrying/uploading/processing jobs or an owed automatic retry round."""
        post_ids = sorted(set(self.store.open_post_ids()) | set(self.store.retry_owed_post_ids(PARALLEL_PLATFORMS)))
        return [self.publish_post(post_id, on_update) for post_id in post_ids]

    def _media(self, video) -> tuple[MediaInfo, list[str]]:
        """Validated media, or (recorded metadata, errors) when the file is gone or invalid.

        Jobs already at the provider (processing) can still be polled without the local file.
        """
        check = validate_video_file(video.path, probe=self.probe_media)
        if check.media is not None:
            return check.media, check.errors
        return MediaInfo(video.path, video.size_bytes, video.mime_type or "video/mp4"), check.errors

    def _run_job(
        self, job: JobInfo, media: MediaInfo, media_errors: list[str], caption: str,
        on_update: UpdateCallback | None, media_provider=None,
    ) -> JobResult:
        if job.status in ("published", "failed"):
            return self._result(job)  # terminal here; never republish

        adapter = self.publishers.get(job.platform)
        state = self.store.provider_state(job.id)

        if adapter is None:
            if job.status in ("pending", "retrying") and not self.store.claim(job.id):
                return self._result(self.store.get_job(job.id))
            return self._fail(job, PublishError(f"No publisher for platform {job.platform}", code="unsupported"),
                              None, on_update)

        # Validate before anything is sent. Validation failures are never retried.
        if job.status in ("pending", "retrying"):
            errors = list(media_errors) or adapter.validate(caption, job.options, media)
            if errors:
                if not self.store.claim(job.id):
                    return self._result(self.store.get_job(job.id))
                return self._fail(job, PublishError("; ".join(errors), code="validation_failed"), None, on_update)

        if job.status == "uploading" and not adapter.can_restart(state):
            # Found mid-upload (the process that owned it died). The upload may have completed.
            return self._fail(job, PublishError(
                "Upload was interrupted and its outcome is unknown; check the account before retrying",
                code="interrupted", uncertain=True), None, on_update)

        if job.status in ("pending", "retrying"):
            if not self.store.claim(job.id):
                return self._result(self.store.get_job(job.id))  # another worker owns it
            self._notify(on_update, job, "uploading")

        # job is now uploading (claimed or resumed) or processing (resume polling)
        while True:
            job = self.store.get_job(job.id)
            attempt_id = self.store.start_attempt(job.id)
            try:
                ctx = PublishContext(
                    job_id=job.id,
                    video_path=media.path,
                    caption=caption,
                    options=job.options,
                    access_token=self._access_token(job, adapter),
                    platform_account_id=self.account_manager.get_account(job.account_id).platform_account_id,
                    media=media,
                    state=state,
                    media_provider=media_provider,
                )
                outcome = adapter.publish(ctx, self._progress(job, attempt_id, on_update))
            except PublishError as e:
                state = self.store.provider_state(job.id)
                retry_number = job.retry_count + 1
                if e.retryable and retry_number <= len(self.retry_delays):
                    delay = self.retry_delays[retry_number - 1]
                    self.store.update_attempt(attempt_id, "failed", error=e.to_dict(), completed=True)
                    self.store.transition(
                        job.id, "retrying", retry_count=retry_number, error_message=str(e),
                        next_retry_at=datetime.now(timezone.utc) + timedelta(seconds=delay),
                    )
                    self._notify(on_update, job, "retrying", error=str(e))
                    self.sleep(delay)
                    if not self.store.claim(job.id):
                        return self._result(self.store.get_job(job.id))
                    self._notify(on_update, job, "uploading")
                    continue
                return self._fail(job, e, attempt_id, on_update)

            self.store.update_attempt(
                attempt_id, "success" if outcome.status == "published" else "processing",
                provider_state=outcome.state, completed=True,
            )
            if outcome.cover_status:
                self.store.set_cover_status(job.id, outcome.cover_status, outcome.cover_error)
            current = self.store.get_job(job.id).status
            if outcome.status == "published":
                self.store.transition(job.id, "published", platform_media_id=outcome.platform_media_id,
                                      error_message=None, next_retry_at=None)
                self._record_link(self.store.get_job(job.id), adapter, outcome.state, media.path)
            elif current != "processing":
                self.store.transition(job.id, "processing")
            final = self.store.get_job(job.id)
            self._notify(on_update, final, final.status)
            return self._result(final)

    def _record_link(self, job: JobInfo, adapter: PlatformPublisher, state: dict, video_path: str) -> bool:
        """Save the permanent URL of a confirmed publication. Never raises: a link problem can't fail a job."""
        if self.links is None or job.status != "published":
            return False
        try:
            url = adapter.published_url(job.platform_media_id, state)
            if not url:
                if state.get("permalink_missing"):
                    log.warning("Job %s is published but its permanent link could not be read; run Published Links "
                                "-> Import from publish history later.", job.id)
                return False
            from src.core.published_links import video_label

            return self.links.add(job.platform, video_label(video_path), job.account_label, url,
                                  job.platform_media_id or url, self._published_at(job.id))
        except Exception as e:  # noqa: BLE001 - bookkeeping only
            log.warning("Could not save the published link for job %s (%s).", job.id, type(e).__name__)
            return False

    def _published_at(self, job_id: int):
        from src.storage.database import PublishJob

        with self.store.database.session() as session:
            row = session.get(PublishJob, job_id)
            return row.published_at if row else None

    def import_published_links(self) -> dict[str, int]:
        """Add links for jobs published before this feature existed (idempotent; duplicates skipped).

        YouTube: from the stored video id. Instagram: the stored permalink, or the documented
        IG Media ``permalink`` field for the stored media id. TikTok: none (no documented URL).
        """
        added = {"youtube": 0, "instagram": 0, "tiktok": 0}
        if self.links is None:
            return added
        for job in self.store.recent_jobs(limit=10_000):
            adapter = self.publishers.get(job.platform)
            if job.status != "published" or adapter is None:
                continue
            state = dict(self.store.provider_state(job.id))
            if job.platform == "instagram" and not state.get("permalink") and job.platform_media_id:
                token, _ = self.account_manager.get_account_with_tokens(job.account_id)
                permalink = adapter.fetch_permalink(token, job.platform_media_id)
                if permalink:
                    state["permalink"] = permalink
            _, video = self.store.get_post(job.post_id)
            if self._record_link(job, adapter, state, video.path):
                added[job.platform] += 1
        return added

    def _progress(self, job: JobInfo, attempt_id: int, on_update: UpdateCallback | None):
        def on_progress(status: str, provider_state: dict[str, Any]) -> None:
            # Persist provider IDs immediately so a crash or retry can resume instead of re-posting.
            self.store.update_attempt(attempt_id, status, provider_state=dict(provider_state))
            current = self.store.get_job(job.id).status
            if current != status and status in TRANSITIONS[current]:  # never move a job backwards
                self.store.transition(job.id, status)
                self._notify(on_update, job, status)

        return on_progress

    def _access_token(self, job: JobInfo, adapter: PlatformPublisher) -> str:
        """Check the account and renew its token per the platform's documented mechanism if needed."""
        account = self.account_manager.get_account(job.account_id)
        if account.status != "active":
            raise PublishError(f"Account is {account.status}; reconnect it to publish", code="account_inactive")
        missing = adapter.missing_scopes(granted_scopes(account))
        if missing:
            raise PublishError("Account is missing required permission: " + "; ".join(missing)
                               + " (reconnect and grant it)", code="insufficient_scope")
        if is_token_expiring(account.expires_at, adapter.TOKEN_REFRESH_MARGIN):
            renewed = False
            if self.auth_manager is not None and self.auth_manager.is_configured(account.platform):
                try:
                    renewed = self.auth_manager.refresh_account_tokens(account.id)
                except Exception:  # noqa: BLE001 - renewal failure is judged by expiry below
                    renewed = False
            account = self.account_manager.get_account(job.account_id)
            if not renewed and is_token_expiring(account.expires_at, 0):
                raise PublishError("Access token expired and could not be renewed; reconnect the account",
                                   code="token_expired")
        access_token, _ = self.account_manager.get_account_with_tokens(account.id)
        return access_token

    def _fail(self, job: JobInfo, error: PublishError, attempt_id: int | None, on_update) -> JobResult:
        if attempt_id is not None:
            self.store.update_attempt(attempt_id, "failed", error=error.to_dict(), completed=True)
        current = self.store.get_job(job.id).status
        if current == "published":
            return self._result(self.store.get_job(job.id))  # never downgrade a published job
        if current in ("pending", "retrying"):
            self.store.claim(job.id)
        if current != "failed":
            self.store.transition(job.id, "failed", error_message=str(error), next_retry_at=None)
        final = self.store.get_job(job.id)
        self._notify(on_update, final, "failed", error=str(error))
        return self._result(final)

    @staticmethod
    def _result(job: JobInfo) -> JobResult:
        return JobResult(job.id, job.platform, job.account_label, job.status, job.platform_media_id,
                         redact(job.error_message) or None)

    @staticmethod
    def _notify(on_update: UpdateCallback | None, job: JobInfo, status: str, error: str | None = None) -> None:
        if on_update:
            on_update(JobResult(job.id, job.platform, job.account_label, status, job.platform_media_id, error))
