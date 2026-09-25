"""Publishing job persistence: post/job creation, the job state machine, and attempt records.

State machine (publish_jobs.status):

    pending ──► uploading ──► processing ──► published
       │            │  │           │  ▲
       │            │  └──────────►│  └── processing (still working; re-checked later)
       ▼            ▼              ▼
     failed ◄──── failed ◄──── failed
       │
       └──► retrying ──► uploading            (bounded; uploading/processing ──► retrying too)

``published`` is terminal. Transitions are checked and applied with a conditional UPDATE
(``WHERE status = <current>``) so two workers can never both move a job forward.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, update

from src.core.validation import file_checksum, validate_video_file
from src.storage.database import (
    Account,
    Database,
    Post,
    PublishAttempt,
    PublishJob,
    Video,
)

TRANSITIONS: dict[str, set[str]] = {
    "pending": {"uploading", "failed"},
    "retrying": {"uploading", "failed"},
    "uploading": {"processing", "published", "failed", "retrying"},
    "processing": {"processing", "published", "failed", "retrying"},
    "failed": {"retrying"},
    "published": set(),
}


class JobError(Exception):
    """Invalid job operation (bad transition, missing post/video/account)."""


@dataclass
class JobInfo:
    """Detached, token-free snapshot of a job and its destination."""

    id: int
    post_id: int
    account_id: int
    platform: str
    account_label: str
    account_status: str
    status: str
    retry_count: int
    platform_media_id: str | None
    error_message: str | None
    options: dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobStore:
    """All database access for posts, publish jobs and publish attempts."""

    def __init__(self, database: Database):
        self.database = database

    # --- creation ------------------------------------------------------------

    def create_post(self, video_path: str, caption: str, destinations: list[tuple[int, dict[str, Any]]]) -> int:
        """Create video (deduplicated by checksum), post and one pending job per destination account."""
        if not destinations:
            raise JobError("Select at least one destination account")
        account_ids = [account_id for account_id, _ in destinations]
        if len(set(account_ids)) != len(account_ids):
            raise JobError("Each account can only be selected once per post")

        check = validate_video_file(video_path, probe=False)
        if not check.ok:
            raise JobError("; ".join(check.errors))
        media = check.media
        checksum = file_checksum(media.path)

        with self.database.session() as session:
            for account_id in account_ids:
                if session.get(Account, account_id) is None:
                    raise JobError(f"Account {account_id} not found")

            video = session.query(Video).filter_by(checksum=checksum).first()
            if video is None:
                video = Video(
                    filename=media.path.replace("\\", "/").rsplit("/", 1)[-1],
                    path=media.path,
                    size_bytes=media.size_bytes,
                    mime_type=media.mime_type,
                    checksum=checksum,
                )
                session.add(video)
                session.flush()

            post = Post(video_id=video.id, caption=caption)
            session.add(post)
            session.flush()
            for account_id, options in destinations:
                session.add(PublishJob(
                    post_id=post.id,
                    account_id=account_id,
                    status="pending",
                    options_json=json.dumps(options or {}),
                ))
            session.commit()
            return post.id

    # --- reads ---------------------------------------------------------------

    def get_post(self, post_id: int) -> tuple[Post, Video]:
        with self.database.session() as session:
            post = session.get(Post, post_id)
            if post is None:
                raise JobError(f"Post {post_id} not found")
            video = session.get(Video, post.video_id)
            if video is None:
                raise JobError(f"Video for post {post_id} not found")
            return post, video

    def jobs_for_post(self, post_id: int) -> list[JobInfo]:
        with self.database.session() as session:
            rows = (
                session.query(PublishJob, Account)
                .join(Account, PublishJob.account_id == Account.id)
                .filter(PublishJob.post_id == post_id)
                .order_by(PublishJob.id)
                .all()
            )
            return [self._info(job, account) for job, account in rows]

    def get_job(self, job_id: int) -> JobInfo:
        with self.database.session() as session:
            row = (
                session.query(PublishJob, Account)
                .join(Account, PublishJob.account_id == Account.id)
                .filter(PublishJob.id == job_id)
                .first()
            )
            if row is None:
                raise JobError(f"Job {job_id} not found")
            return self._info(*row)

    def recent_jobs(self, limit: int = 30) -> list[JobInfo]:
        with self.database.session() as session:
            rows = (
                session.query(PublishJob, Account)
                .join(Account, PublishJob.account_id == Account.id)
                .order_by(PublishJob.id.desc())
                .limit(limit)
                .all()
            )
            return [self._info(job, account) for job, account in rows]

    def open_post_ids(self) -> list[int]:
        """Posts with at least one job that is not published or failed."""
        with self.database.session() as session:
            rows = (
                session.query(PublishJob.post_id)
                .filter(PublishJob.status.in_(("pending", "retrying", "uploading", "processing")))
                .distinct()
                .order_by(PublishJob.post_id)
                .all()
            )
            return [r[0] for r in rows]

    def already_published(self, video_path: str, account_id: int) -> bool:
        """True if this exact file (by checksum) was already published to this account."""
        checksum = file_checksum(video_path)
        with self.database.session() as session:
            return (
                session.query(PublishJob.id)
                .join(Post, PublishJob.post_id == Post.id)
                .join(Video, Post.video_id == Video.id)
                .filter(Video.checksum == checksum, PublishJob.account_id == account_id,
                        PublishJob.status == "published")
                .first()
                is not None
            )

    @staticmethod
    def _info(job: PublishJob, account: Account) -> JobInfo:
        return JobInfo(
            id=job.id,
            post_id=job.post_id,
            account_id=account.id,
            platform=account.platform,
            account_label=account.display_name or account.username,
            account_status=account.status,
            status=job.status,
            retry_count=job.retry_count,
            platform_media_id=job.platform_media_id,
            error_message=job.error_message,
            options=json.loads(job.options_json) if job.options_json else {},
        )

    # --- state machine -------------------------------------------------------

    def transition(self, job_id: int, new_status: str, **fields: Any) -> None:
        """Move a job to ``new_status`` if the state machine allows it (atomic, conditional)."""
        current = self.get_job(job_id).status
        if new_status not in TRANSITIONS[current]:
            raise JobError(f"Job {job_id}: invalid transition {current} -> {new_status}")
        values = {"status": new_status, **fields}
        if new_status == "published":
            values.setdefault("published_at", _now())
        with self.database.session() as session:
            result = session.execute(
                update(PublishJob)
                .where(PublishJob.id == job_id, PublishJob.status == current)
                .values(**values)
            )
            session.commit()
        if result.rowcount != 1:
            raise JobError(f"Job {job_id} changed concurrently (was {current})")

    def claim(self, job_id: int) -> bool:
        """Atomically move a pending/retrying job to uploading. False if someone else owns it."""
        with self.database.session() as session:
            result = session.execute(
                update(PublishJob)
                .where(PublishJob.id == job_id, PublishJob.status.in_(("pending", "retrying")))
                .values(status="uploading", error_message=None)
            )
            session.commit()
        return result.rowcount == 1

    # --- attempts ------------------------------------------------------------

    def start_attempt(self, job_id: int) -> int:
        with self.database.session() as session:
            last = session.query(func.max(PublishAttempt.attempt_number)).filter_by(job_id=job_id).scalar()
            attempt = PublishAttempt(job_id=job_id, attempt_number=(last or 0) + 1, status="started", started_at=_now())
            session.add(attempt)
            session.commit()
            return attempt.id

    def update_attempt(
        self,
        attempt_id: int,
        status: str,
        provider_state: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        completed: bool = False,
    ) -> None:
        """Record progress. Only token-free data goes in: provider IDs, status, safe error info."""
        with self.database.session() as session:
            attempt = session.get(PublishAttempt, attempt_id)
            attempt.status = status
            if provider_state is not None:
                attempt.response_json = json.dumps({"provider_state": provider_state})
            if error is not None:
                attempt.error_json = json.dumps(error)
            if completed:
                attempt.completed_at = _now()
            session.commit()

    def provider_state(self, job_id: int) -> dict[str, Any]:
        """Provider references saved by the most recent attempt that recorded any."""
        with self.database.session() as session:
            attempts = (
                session.query(PublishAttempt)
                .filter(PublishAttempt.job_id == job_id, PublishAttempt.response_json.isnot(None))
                .order_by(PublishAttempt.attempt_number.desc())
                .first()
            )
            if attempts is None:
                return {}
            return json.loads(attempts.response_json).get("provider_state", {})

    def attempts(self, job_id: int) -> list[PublishAttempt]:
        with self.database.session() as session:
            return (
                session.query(PublishAttempt)
                .filter_by(job_id=job_id)
                .order_by(PublishAttempt.attempt_number)
                .all()
            )
