"""Platform-independent publishing engine.

Each destination is its own job and runs in isolation: one platform failing never stops or
rolls back another. No platform HTTP lives here; adapters implement PlatformPublisher.
"""

import json
import time
from collections.abc import Callable
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
    ):
        self.store = JobStore(database)
        self.account_manager = account_manager
        self.auth_manager = auth_manager
        self._publishers = publishers
        self.sleep = sleep
        self.retry_delays = retry_delays  # max retries == len(retry_delays)
        self.probe_media = probe_media

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
        """Publish every destination of a post independently and return the aggregate result."""
        post, video = self.store.get_post(post_id)
        result = PostResult(post_id)
        media, media_errors = self._media(video)
        for job in self.store.jobs_for_post(post_id):
            try:
                job_result = self._run_job(job, media, media_errors, post.caption or "", on_update)
            except Exception as e:  # noqa: BLE001 - isolate destinations: one bug must not stop the others
                job_result = self._fail(job, PublishError(f"Unexpected error: {type(e).__name__}", code="internal"),
                                        attempt_id=None, on_update=on_update)
            result.jobs.append(job_result)
        return result

    def retry_job(self, job_id: int, on_update: UpdateCallback | None = None) -> JobResult:
        """Manually retry a failed job (failed -> retrying -> run)."""
        job = self.store.get_job(job_id)
        if job.status != "failed":
            raise JobError(f"Only failed jobs can be retried (job is {job.status})")
        self.store.transition(job_id, "retrying", retry_count=0, next_retry_at=None)
        post, video = self.store.get_post(job.post_id)
        media, media_errors = self._media(video)
        return self._run_job(self.store.get_job(job_id), media, media_errors, post.caption or "", on_update)

    def resume_open_jobs(self, on_update: UpdateCallback | None = None) -> list[PostResult]:
        """Continue every post that still has pending/retrying/uploading/processing jobs."""
        return [self.publish_post(post_id, on_update) for post_id in self.store.open_post_ids()]

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
        on_update: UpdateCallback | None,
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
            elif current != "processing":
                self.store.transition(job.id, "processing")
            final = self.store.get_job(job.id)
            self._notify(on_update, final, final.status)
            return self._result(final)

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
