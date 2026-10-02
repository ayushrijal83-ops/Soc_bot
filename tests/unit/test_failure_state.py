# ruff: noqa: F811 - pytest fixtures imported from other test modules
"""Engine failure-state hardening: every failure keeps its structured error (code, message, retryable,
uncertain), including failures that happen before any provider call (no attempt row existed before), and an
unknown outcome is never re-uploaded automatically.
"""

import json

from sqlalchemy import update

from src.core.jobs import JobStore
from src.core.publisher import PublisherEngine
from src.platforms.base import PublishError
from src.services.publishing import PublishingService
from src.storage.database import PublishAttempt, PublishJob
from tests.unit.test_publishing_engine import (  # noqa: F401
    OPTIONS,
    FakePublisher,
    env,
    ok,
)


def make(env, platforms, auto_retry=False):
    db, _, ids, video = env
    return JobStore(db).create_post(video, "Hello", [(ids[p], OPTIONS[p]) for p in platforms], auto_retry=auto_retry)


def build(env, publishers, **kw):
    db, accounts, _, _ = env
    return PublisherEngine(db, accounts, publishers=publishers, sleep=lambda s: None,
                           retry_delays=kw.pop("retry_delays", (1, 2, 3)), probe_media=False, **kw)


def only_job(env, post_id):
    (job,) = JobStore(env[0]).jobs_for_post(post_id)
    return job


def attempt_rows(env, job_id):
    with env[0].session() as session:
        return [(a.status, json.loads(a.error_json) if a.error_json else None)
                for a in session.query(PublishAttempt).filter_by(job_id=job_id).order_by(PublishAttempt.attempt_number)]


def crashed_mid_upload(env, post_id):
    """State left by a process that died while uploading: the job is 'uploading'."""
    assert JobStore(env[0]).claim(only_job(env, post_id).id)


# --- CASE A / E: interrupted, outcome unknown ----------------------------------------------------------

def test_interrupted_failure_keeps_code_and_uncertain_flag(env):
    fake = FakePublisher("youtube", ok(), restart_safe=False)
    post_id = make(env, ("youtube",))
    crashed_mid_upload(env, post_id)
    eng = build(env, {"youtube": fake})
    eng.publish_post(post_id)
    job = only_job(env, post_id)
    error = eng.store.last_error(job.id)
    assert job.status == "failed" and fake.calls == []
    assert error["code"] == "interrupted" and error["uncertain"] is True and error["retryable"] is False
    assert "unknown" in error["message"] and "unknown" in job.error_message
    assert attempt_rows(env, job.id) == [("failed", error)]  # one record of the failure itself


def test_interrupted_instagram_like_job_is_never_auto_reuploaded(env):
    """The latent case: a restart-UNSAFE adapter on the automatic-retry platform. Before the fix the retry round
    re-uploaded it because last_error() was empty."""
    fake = FakePublisher("instagram", ok("again"), restart_safe=False)
    post_id = make(env, ("instagram",), auto_retry=True)
    crashed_mid_upload(env, post_id)
    eng = build(env, {"instagram": fake})
    eng.publish_post(post_id)
    job = only_job(env, post_id)
    assert job.status == "failed" and fake.calls == [] and job.auto_retry_used
    assert eng.store.last_error(job.id)["uncertain"] is True
    assert eng.store.retry_owed_post_ids(("instagram",)) == []
    eng.publish_post(post_id)  # running the batch again (e.g. Resume) never re-uploads it either
    assert fake.calls == []


def test_friendly_error_reports_unknown_outcome(env):
    fake = FakePublisher("youtube", ok(), restart_safe=False)
    post_id = make(env, ("youtube",))
    crashed_mid_upload(env, post_id)
    eng = build(env, {"youtube": fake})
    eng.publish_post(post_id)
    (view,) = PublishingService(eng, env[1]).batch(post_id).jobs
    assert view.error == "The upload was interrupted; its outcome is unknown. Check the account first."
    assert "unknown" in view.error_details


def test_interrupted_job_can_still_be_retried_manually(env):
    fake = FakePublisher("youtube", ok("V9"), restart_safe=False)
    post_id = make(env, ("youtube",))
    crashed_mid_upload(env, post_id)
    eng = build(env, {"youtube": fake})
    eng.publish_post(post_id)
    job = only_job(env, post_id)
    result = eng.retry_job(job.id)  # the user checked the account and chose to retry
    assert result.status == "published" and len(fake.calls) == 1
    assert only_job(env, post_id).id == job.id and [s for s, _ in attempt_rows(env, job.id)] == ["failed", "success"]


# --- other failures before any provider call --------------------------------------------------------

def test_validation_failure_is_recorded_and_not_retried(env):
    fake = FakePublisher("instagram", ok(), validate_errors=["caption too long"])
    post_id = make(env, ("instagram",), auto_retry=True)
    eng = build(env, {"instagram": fake})
    eng.publish_post(post_id)
    job = only_job(env, post_id)
    error = eng.store.last_error(job.id)
    assert job.status == "failed" and fake.calls == []
    assert error["code"] == "validation_failed" and error["uncertain"] is False and "caption too long" in error["message"]
    (view,) = PublishingService(eng, env[1]).batch(post_id).jobs
    assert view.error == "The video, cover or options don't meet Instagram's requirements."


def test_internal_error_and_missing_publisher_are_recorded(env):
    class Crashing(FakePublisher):
        def publish(self, ctx, on_progress):
            raise RuntimeError("adapter bug")

    crash_post = make(env, ("tiktok",))
    eng = build(env, {"tiktok": Crashing("tiktok")})
    eng.publish_post(crash_post)
    assert eng.store.last_error(only_job(env, crash_post).id)["code"] == "internal"
    no_adapter = make(env, ("youtube",))
    build(env, {}).publish_post(no_adapter)
    assert eng.store.last_error(only_job(env, no_adapter).id)["code"] == "unsupported"


def test_failed_job_without_any_recorded_error_is_not_auto_retried(env):
    """Jobs failed before this fix (no error record) are an unknown outcome: never re-uploaded automatically."""
    fake = FakePublisher("instagram", ok("again"))
    post_id = make(env, ("instagram",), auto_retry=True)
    job = only_job(env, post_id)
    with env[0].session() as session:
        session.execute(update(PublishJob).where(PublishJob.id == job.id).values(status="failed",
                                                                                  error_message="legacy"))
        session.commit()
    eng = build(env, {"instagram": fake})
    assert eng.store.last_error(job.id) == {}  # legacy row: still readable
    eng.publish_post(post_id)
    assert fake.calls == [] and only_job(env, post_id).auto_retry_used and only_job(env, post_id).status == "failed"


def test_published_job_gets_no_failure_record(env):
    fake = FakePublisher("tiktok", ok("T1"))
    post_id = make(env, ("tiktok",))
    eng = build(env, {"tiktok": fake})
    eng.publish_post(post_id)
    job = only_job(env, post_id)
    eng._fail(job, PublishError("late duplicate signal", code="internal"), None, None)  # e.g. a racing worker
    assert only_job(env, post_id).status == "published"
    assert [s for s, _ in attempt_rows(env, job.id)] == ["success"]


# --- CASE B / C / D: unchanged retry semantics ------------------------------------------------------

def test_retryable_failure_unchanged(env):
    fake = FakePublisher("tiktok", PublishError("reset", code="network", retryable=True), ok("T1"))
    post_id = make(env, ("tiktok",))
    eng = build(env, {"tiktok": fake})
    eng.publish_post(post_id)
    job = only_job(env, post_id)
    assert job.status == "published" and job.retry_count == 1 and len(fake.calls) == 2
    assert [s for s, _ in attempt_rows(env, job.id)] == ["failed", "success"]


def test_non_retryable_failure_unchanged(env):
    fake = FakePublisher("tiktok", PublishError("rejected", code="forbidden"), ok("never"))
    post_id = make(env, ("tiktok",))
    eng = build(env, {"tiktok": fake})
    eng.publish_post(post_id)
    job = only_job(env, post_id)
    assert job.status == "failed" and job.retry_count == 0 and len(fake.calls) == 1
    assert [s for s, _ in attempt_rows(env, job.id)] == ["failed"]  # no extra record for provider failures
    assert eng.store.last_error(job.id)["code"] == "forbidden"


def test_instagram_certain_failure_still_gets_its_one_automatic_retry(env):
    fake = FakePublisher("instagram", PublishError("processing failed", code="processing_failed"), ok("IG1"))
    post_id = make(env, ("instagram",), auto_retry=True)
    eng = build(env, {"instagram": fake})
    eng.publish_post(post_id)
    job = only_job(env, post_id)
    assert job.status == "published" and job.auto_retry_used and len(fake.calls) == 2


def test_manual_retry_unchanged_same_job(env):
    fake = FakePublisher("tiktok", PublishError("rejected", code="forbidden"), ok("T2"))
    post_id = make(env, ("tiktok",))
    eng = build(env, {"tiktok": fake})
    eng.publish_post(post_id)
    job = only_job(env, post_id)
    assert eng.retry_job(job.id).status == "published"
    assert only_job(env, post_id).id == job.id and len(JobStore(env[0]).jobs_for_post(post_id)) == 1


# --- security: stored errors stay redacted ------------------------------------------------------------

def test_recorded_errors_never_contain_secrets(env):
    class Leaky(FakePublisher):
        def publish(self, ctx, on_progress):
            raise PublishError("bad request access_token=SECRET_TOKEN_123 client_secret=XYZ", code="forbidden")

    post_id = make(env, ("tiktok",))
    eng = build(env, {"tiktok": Leaky("tiktok")})
    eng.publish_post(post_id)
    job = only_job(env, post_id)
    stored = json.dumps(attempt_rows(env, job.id)) + (job.error_message or "")
    assert "SECRET_TOKEN_123" not in stored and "[REDACTED]" in stored
