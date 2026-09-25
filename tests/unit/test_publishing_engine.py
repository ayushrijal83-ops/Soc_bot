"""Publishing engine tests: jobs, state machine, retries, isolation, idempotency, tokens, dry-run.

Uses a real temporary SQLite database. Provider HTTP is mocked (httpx.MockTransport) or replaced
by scripted fake publishers. No real provider is contacted.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from src.accounts.manager import AccountManager
from src.core.jobs import JobError, JobStore
from src.core.publisher import PublisherEngine
from src.core.validation import MediaInfo, validate_video_file
from src.platforms.base import PlatformPublisher, PublishError, PublishOutcome
from src.platforms.instagram.publisher import InstagramPublisher
from src.platforms.tiktok.publisher import TikTokPublisher
from src.platforms.youtube.publisher import YouTubePublisher
from src.storage.database import Database, PublishAttempt
from src.storage.tokens import TokenEncryption, generate_key

SECRET = "ACCESS_TOKEN_SECRET_XYZ"


@pytest.fixture
def env(tmp_path):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    encryption = TokenEncryption(generate_key().encode())
    db = Database(f"sqlite:///{path}", encryption=encryption)
    db.init()
    accounts = AccountManager(db, encryption)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    ids = {}
    for platform, pid in (("instagram", "17841400000"), ("tiktok", "open_1"), ("youtube", "UC1")):
        ids[platform] = accounts.create_account(
            platform=platform, platform_account_id=pid, username=f"{platform}_user",
            access_token=f"{SECRET}_{platform}", refresh_token="RT_SECRET", expires_at=future,
        ).id
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v" * 2000)
    yield db, accounts, ids, str(video)
    db.engine.dispose()
    try:
        os.unlink(path)
    except PermissionError:
        pass


OPTIONS = {
    "instagram": {"video_url": "https://cdn.example.com/clip.mp4"},
    "tiktok": {"privacy_level": "SELF_ONLY"},
    "youtube": {"title": "T", "privacy_status": "private"},
}


class FakePublisher(PlatformPublisher):
    """Scripted adapter: each publish() call pops the next action."""

    def __init__(self, platform, *actions, validate_errors=None, restart_safe=True):
        super().__init__(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        self.PLATFORM = platform
        self.actions = list(actions)
        self.calls = []
        self.validate_errors = validate_errors or []
        self.RESTART_SAFE = restart_safe

    def validate(self, caption, options, media):
        return list(self.validate_errors)

    def publish(self, ctx, on_progress):
        self.calls.append(ctx)
        action = self.actions.pop(0)
        if isinstance(action, PublishError):
            if action.code == "after_ref":
                on_progress("processing", {"ref": "R1"})
            raise action
        on_progress("uploading", {"ref": "R1"})
        return action


def ok(media_id="M1"):
    return PublishOutcome("published", media_id, {"ref": "R1"})


def engine(env, publishers, **kw):
    db, accounts, _, _ = env
    return PublisherEngine(db, accounts, publishers=publishers, sleep=lambda s: None,
                           retry_delays=kw.pop("retry_delays", (1, 2, 3)), probe_media=False, **kw)


def make_post(env, platforms=("instagram", "tiktok", "youtube"), caption="Hello"):
    db, _, ids, video = env
    return JobStore(db).create_post(video, caption, [(ids[p], OPTIONS[p]) for p in platforms])


def job_by_platform(env, post_id):
    return {j.platform: j for j in JobStore(env[0]).jobs_for_post(post_id)}


# ---------------------------------------------------------------------------------------------
# Jobs and state machine
# ---------------------------------------------------------------------------------------------

class TestJobStore:
    def test_create_post_one_pending_job_per_destination(self, env):
        post_id = make_post(env)
        jobs = JobStore(env[0]).jobs_for_post(post_id)
        assert [j.platform for j in jobs] == ["instagram", "tiktok", "youtube"]
        assert all(j.status == "pending" for j in jobs)
        assert jobs[1].options == {"privacy_level": "SELF_ONLY"}

    def test_video_deduplicated_by_checksum(self, env):
        store = JobStore(env[0])
        a, b = make_post(env, ("tiktok",)), make_post(env, ("tiktok",))
        assert store.get_post(a)[1].id == store.get_post(b)[1].id

    def test_rejects_duplicate_destination_and_missing_inputs(self, env):
        db, _, ids, video = env
        store = JobStore(db)
        with pytest.raises(JobError, match="once"):
            store.create_post(video, "c", [(ids["tiktok"], {}), (ids["tiktok"], {})])
        with pytest.raises(JobError, match="not found"):
            store.create_post(video, "c", [(9999, {})])
        with pytest.raises(JobError, match="not found"):
            store.create_post(video + ".missing.mp4", "c", [(ids["tiktok"], {})])
        with pytest.raises(JobError, match="at least one"):
            store.create_post(video, "c", [])

    def test_state_machine_rejects_invalid_transitions(self, env):
        store = JobStore(env[0])
        job = store.jobs_for_post(make_post(env, ("tiktok",)))[0]
        with pytest.raises(JobError, match="invalid transition"):
            store.transition(job.id, "published")  # pending -> published is not allowed
        store.transition(job.id, "uploading")
        store.transition(job.id, "processing")
        store.transition(job.id, "published")
        for target in ("pending", "uploading", "failed", "retrying"):
            with pytest.raises(JobError):
                store.transition(job.id, target)  # published is terminal

    def test_claim_is_atomic_single_owner(self, env):
        store = JobStore(env[0])
        job = store.jobs_for_post(make_post(env, ("tiktok",)))[0]
        assert store.claim(job.id) is True
        assert store.claim(job.id) is False

    def test_already_published_detects_same_file_same_account(self, env):
        db, _, ids, video = env
        store = JobStore(db)
        job = store.jobs_for_post(make_post(env, ("tiktok",)))[0]
        assert store.already_published(video, ids["tiktok"]) is False
        store.transition(job.id, "uploading")
        store.transition(job.id, "published")
        assert store.already_published(video, ids["tiktok"]) is True
        assert store.already_published(video, ids["youtube"]) is False


# ---------------------------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------------------------

class TestEngine:
    def test_success_path_and_provider_id_persisted(self, env):
        fake = FakePublisher("tiktok", ok("TT_MEDIA"))
        post_id = make_post(env, ("tiktok",))
        updates = []
        result = engine(env, {"tiktok": fake}).publish_post(post_id, updates.append)

        job = job_by_platform(env, post_id)["tiktok"]
        assert job.status == "published" and job.platform_media_id == "TT_MEDIA"
        assert [u.status for u in updates] == ["uploading", "published"]
        assert result.count("published") == 1
        attempts = JobStore(env[0]).attempts(job.id)
        assert len(attempts) == 1 and attempts[0].status == "success"
        assert attempts[0].completed_at is not None
        assert json.loads(attempts[0].response_json) == {"provider_state": {"ref": "R1"}}
        assert fake.calls[0].access_token == f"{SECRET}_tiktok"

    def test_failure_isolation(self, env):
        """Instagram = SUCCESS, TikTok = FAILURE, YouTube = SUCCESS."""
        pubs = {
            "instagram": FakePublisher("instagram", ok("IG1")),
            "tiktok": FakePublisher("tiktok", PublishError("rejected", code="publish_failed")),
            "youtube": FakePublisher("youtube", ok("YT1")),
        }
        post_id = make_post(env)
        result = engine(env, pubs).publish_post(post_id)
        jobs = job_by_platform(env, post_id)
        assert jobs["instagram"].status == "published"
        assert jobs["tiktok"].status == "failed"
        assert jobs["youtube"].status == "published"
        assert (result.count("published"), result.count("failed")) == (2, 1)

    def test_unexpected_exception_in_one_adapter_does_not_stop_others(self, env):
        class Boom(FakePublisher):
            def publish(self, ctx, on_progress):
                raise RuntimeError(f"bug with {ctx.access_token}")

        pubs = {"instagram": Boom("instagram"), "tiktok": FakePublisher("tiktok", ok()),
                "youtube": FakePublisher("youtube", ok())}
        post_id = make_post(env)
        result = engine(env, pubs).publish_post(post_id)
        jobs = job_by_platform(env, post_id)
        assert jobs["instagram"].status == "failed"
        assert SECRET not in (jobs["instagram"].error_message or "")
        assert jobs["tiktok"].status == jobs["youtube"].status == "published"
        assert result.count("failed") == 1

    def test_retryable_error_is_retried_then_succeeds(self, env):
        sleeps = []
        fake = FakePublisher("tiktok", PublishError("5xx", code="provider_error", retryable=True, http_status=503), ok())
        post_id = make_post(env, ("tiktok",))
        eng = engine(env, {"tiktok": fake})
        eng.sleep = sleeps.append
        updates = []
        eng.publish_post(post_id, updates.append)
        job = job_by_platform(env, post_id)["tiktok"]
        assert job.status == "published" and job.retry_count == 1
        assert sleeps == [1]
        assert "retrying" in [u.status for u in updates]
        attempts = JobStore(env[0]).attempts(job.id)
        assert [a.status for a in attempts] == ["failed", "success"]
        assert json.loads(attempts[0].error_json)["http_status"] == 503

    def test_retries_are_bounded(self, env):
        err = PublishError("timeout", code="timeout", retryable=True)
        fake = FakePublisher("tiktok", err, err, err, err, err)
        post_id = make_post(env, ("tiktok",))
        engine(env, {"tiktok": fake}).publish_post(post_id)
        job = job_by_platform(env, post_id)["tiktok"]
        assert job.status == "failed"
        assert len(fake.calls) == 4  # 1 attempt + 3 retries
        assert len(JobStore(env[0]).attempts(job.id)) == 4

    @pytest.mark.parametrize("code", ["unauthorized", "insufficient_scope", "permission_denied", "validation_failed"])
    def test_non_retryable_errors_fail_immediately(self, env, code):
        fake = FakePublisher("tiktok", PublishError("no", code=code))
        post_id = make_post(env, ("tiktok",))
        engine(env, {"tiktok": fake}).publish_post(post_id)
        assert job_by_platform(env, post_id)["tiktok"].status == "failed"
        assert len(fake.calls) == 1

    def test_retry_resumes_with_saved_provider_state(self, env):
        fake = FakePublisher("instagram", PublishError("x", code="after_ref", retryable=True), ok())
        post_id = make_post(env, ("instagram",))
        engine(env, {"instagram": fake}).publish_post(post_id)
        assert fake.calls[0].state == {}
        assert fake.calls[1].state == {"ref": "R1"}  # second attempt resumes, doesn't restart

    def test_published_job_is_never_published_again(self, env):
        fake = FakePublisher("tiktok", ok())
        post_id = make_post(env, ("tiktok",))
        eng = engine(env, {"tiktok": fake})
        eng.publish_post(post_id)
        eng.publish_post(post_id)
        assert len(fake.calls) == 1

    def test_processing_job_is_resumed_not_restarted(self, env):
        fake = FakePublisher("youtube", PublishOutcome("processing", "V1", {"video_id": "V1"}), ok("V1"))
        post_id = make_post(env, ("youtube",))
        eng = engine(env, {"youtube": fake})
        eng.publish_post(post_id)
        assert job_by_platform(env, post_id)["youtube"].status == "processing"
        eng.resume_open_jobs()
        assert job_by_platform(env, post_id)["youtube"].status == "published"
        assert fake.calls[1].state == {"video_id": "V1"}

    def test_interrupted_upload_not_restarted_when_unsafe(self, env):
        """Crash mid-upload (job left 'uploading') on a platform that could have created the video."""
        fake = FakePublisher("youtube", ok(), restart_safe=False)
        post_id = make_post(env, ("youtube",))
        store = JobStore(env[0])
        job = job_by_platform(env, post_id)["youtube"]
        store.claim(job.id)  # simulates the dead process
        engine(env, {"youtube": fake}).publish_post(post_id)
        job = store.get_job(job.id)
        assert job.status == "failed" and "unknown" in job.error_message
        assert fake.calls == []

    def test_interrupted_upload_restarted_when_safe(self, env):
        fake = FakePublisher("tiktok", ok())
        post_id = make_post(env, ("tiktok",))
        store = JobStore(env[0])
        store.claim(job_by_platform(env, post_id)["tiktok"].id)
        engine(env, {"tiktok": fake}).publish_post(post_id)
        assert job_by_platform(env, post_id)["tiktok"].status == "published"

    def test_job_claimed_by_other_worker_is_skipped(self, env):
        fake = FakePublisher("tiktok", ok())
        post_id = make_post(env, ("tiktok",))
        eng = engine(env, {"tiktok": fake})
        eng.store.claim = lambda job_id: False
        eng.publish_post(post_id)
        assert fake.calls == []

    def test_manual_retry_of_failed_job(self, env):
        fake = FakePublisher("tiktok", PublishError("no", code="unauthorized"), ok())
        post_id = make_post(env, ("tiktok",))
        eng = engine(env, {"tiktok": fake})
        eng.publish_post(post_id)
        job = job_by_platform(env, post_id)["tiktok"]
        assert job.status == "failed"
        assert eng.retry_job(job.id).status == "published"
        with pytest.raises(JobError):
            eng.retry_job(job.id)

    def test_validation_failure_is_recorded_and_not_sent(self, env):
        fake = FakePublisher("tiktok", ok(), validate_errors=["bad caption"])
        post_id = make_post(env, ("tiktok",))
        engine(env, {"tiktok": fake}).publish_post(post_id)
        job = job_by_platform(env, post_id)["tiktok"]
        assert job.status == "failed" and "bad caption" in job.error_message
        assert fake.calls == []

    def test_missing_video_fails_pending_jobs(self, env):
        fake = FakePublisher("tiktok", ok())
        post_id = make_post(env, ("tiktok",))
        os.remove(env[3])
        engine(env, {"tiktok": fake}).publish_post(post_id)
        job = job_by_platform(env, post_id)["tiktok"]
        assert job.status == "failed" and "not found" in job.error_message
        assert fake.calls == []

    def test_disconnected_account_fails(self, env):
        _db, accounts, ids, _ = env
        fake = FakePublisher("tiktok", ok())
        post_id = make_post(env, ("tiktok",))
        accounts.disconnect_account(ids["tiktok"])
        engine(env, {"tiktok": fake}).publish_post(post_id)
        job = job_by_platform(env, post_id)["tiktok"]
        assert job.status == "failed" and "disconnected" in job.error_message
        assert fake.calls == []

    def test_attempt_records_never_contain_tokens(self, env):
        pubs = {"tiktok": FakePublisher("tiktok", PublishError(f"got Bearer {SECRET}_tiktok", code="x"))}
        post_id = make_post(env, ("tiktok",))
        engine(env, pubs).publish_post(post_id)
        with env[0].session() as s:
            rows = s.query(PublishAttempt).all()
            blob = " ".join((a.response_json or "") + (a.error_json or "") for a in rows)
        job = job_by_platform(env, post_id)["tiktok"]
        for secret in (SECRET, "RT_SECRET"):
            assert secret not in blob
            assert secret not in (job.error_message or "")


class TestTokenHandling:
    def _expire(self, env, platform, delta):
        _db, accounts, ids, _ = env
        accounts.update_account(ids[platform], expires_at=datetime.now(timezone.utc) + delta)

    def test_expiring_token_is_renewed_before_publishing(self, env):
        _db, accounts, ids, _ = env
        self._expire(env, "tiktok", timedelta(seconds=30))
        auth = MagicMock()
        auth.is_configured.return_value = True

        def refresh(account_id):
            accounts.update_account(account_id, access_token="NEW_TOKEN",
                                    expires_at=datetime.now(timezone.utc) + timedelta(days=1))
            return True

        auth.refresh_account_tokens.side_effect = refresh
        fake = FakePublisher("tiktok", ok())
        post_id = make_post(env, ("tiktok",))
        engine(env, {"tiktok": fake}, auth_manager=auth).publish_post(post_id)
        auth.refresh_account_tokens.assert_called_once_with(ids["tiktok"])
        assert fake.calls[0].access_token == "NEW_TOKEN"

    def test_fresh_token_is_not_renewed(self, env):
        auth = MagicMock()
        fake = FakePublisher("tiktok", ok())
        engine(env, {"tiktok": fake}, auth_manager=auth).publish_post(make_post(env, ("tiktok",)))
        auth.refresh_account_tokens.assert_not_called()

    def test_expired_token_that_cannot_be_renewed_fails_clearly(self, env):
        self._expire(env, "tiktok", timedelta(seconds=-10))
        auth = MagicMock()
        auth.is_configured.return_value = True
        auth.refresh_account_tokens.return_value = False
        fake = FakePublisher("tiktok", ok())
        post_id = make_post(env, ("tiktok",))
        engine(env, {"tiktok": fake}, auth_manager=auth).publish_post(post_id)
        job = job_by_platform(env, post_id)["tiktok"]
        assert job.status == "failed" and "reconnect" in job.error_message
        assert fake.calls == []

    def test_instagram_renews_early_but_proceeds_if_renewal_not_allowed_yet(self, env):
        # Within Instagram's 7-day margin; re-exchange refused (e.g. token < 24h old) but still valid.
        self._expire(env, "instagram", timedelta(days=3))
        auth = MagicMock()
        auth.is_configured.return_value = True
        auth.refresh_account_tokens.return_value = False
        fake = FakePublisher("instagram", ok())
        fake.TOKEN_REFRESH_MARGIN = InstagramPublisher.TOKEN_REFRESH_MARGIN
        post_id = make_post(env, ("instagram",))
        engine(env, {"instagram": fake}, auth_manager=auth).publish_post(post_id)
        auth.refresh_account_tokens.assert_called_once()
        assert job_by_platform(env, post_id)["instagram"].status == "published"


class TestDryRun:
    def test_plan_makes_no_provider_calls_and_no_changes(self, env):
        def forbid(request):
            raise AssertionError("dry-run contacted a provider")

        client = httpx.Client(transport=httpx.MockTransport(forbid))
        pubs = {"instagram": InstagramPublisher(client=client), "tiktok": TikTokPublisher(client=client),
                "youtube": YouTubePublisher(client=client)}
        auth = MagicMock()
        post_id = make_post(env)
        plan = engine(env, pubs, auth_manager=auth).plan_post(post_id)
        assert [p.platform for p in plan] == ["instagram", "tiktok", "youtube"]
        assert all(p.ready for p in plan)
        assert all(j.status == "pending" for j in JobStore(env[0]).jobs_for_post(post_id))
        auth.refresh_account_tokens.assert_not_called()
        assert JobStore(env[0]).attempts(JobStore(env[0]).jobs_for_post(post_id)[0].id) == []

    def test_plan_reports_problems(self, env):
        _db, accounts, ids, video = env
        pubs = {"tiktok": TikTokPublisher(), "instagram": InstagramPublisher()}
        accounts.update_account(ids["instagram"], expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        plan = engine(env, pubs).plan_destinations(video, "c", [(ids["tiktok"], {}), (ids["instagram"], OPTIONS["instagram"])])
        assert not plan[0].ready and "privacy_level" in plan[0].errors[0]
        assert plan[1].ready and "expired" in plan[1].notes[0]


class TestEndToEndWithMockedProviders:
    """Real adapters + real engine + mocked HTTP: IG ok, TikTok rejected, YouTube ok."""

    def test_isolation_through_real_adapters(self, env):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url).split("?")[0]
            assert SECRET not in str(request.url)
            if url.endswith("/media_publish"):
                return httpx.Response(200, json={"id": "IG_MEDIA"})
            if url.endswith("/17841400000/media"):
                return httpx.Response(200, json={"id": "C1"})
            if url.endswith("/C1"):
                return httpx.Response(200, json={"status_code": "FINISHED"})
            if "creator_info" in url:
                return httpx.Response(401, json={"data": {}, "error": {"code": "scope_not_authorized", "message": "no"}})
            if request.method == "POST" and "upload/youtube" in url:
                return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=S"})
            if request.method == "PUT" and "upload/youtube" in url:
                return httpx.Response(201, json={"id": "YT_VIDEO"})
            if "youtube/v3/videos" in url:
                return httpx.Response(200, json={"items": [{"status": {"uploadStatus": "processed"}}]})
            raise AssertionError(url)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        kw = {"client": client, "sleep": lambda s: None, "poll_interval": 0}
        pubs = {"instagram": InstagramPublisher(**kw), "tiktok": TikTokPublisher(**kw), "youtube": YouTubePublisher(**kw)}
        post_id = make_post(env)
        result = engine(env, pubs).publish_post(post_id)
        jobs = job_by_platform(env, post_id)
        assert jobs["instagram"].status == "published" and jobs["instagram"].platform_media_id == "IG_MEDIA"
        assert jobs["tiktok"].status == "failed" and "scope_not_authorized" in jobs["tiktok"].error_message
        assert jobs["youtube"].status == "published" and jobs["youtube"].platform_media_id == "YT_VIDEO"
        assert (result.count("published"), result.count("failed")) == (2, 1)


class TestValidation:
    def test_generic_checks(self, tmp_path):
        assert "not found" in validate_video_file(tmp_path / "nope.mp4").errors[0]
        empty = tmp_path / "e.mp4"
        empty.write_bytes(b"")
        assert "empty" in validate_video_file(empty).errors[0]
        txt = tmp_path / "a.txt"
        txt.write_text("x")
        assert "Unsupported" in validate_video_file(txt).errors[0]
        assert "regular file" in validate_video_file(tmp_path).errors[0]
        good = tmp_path / "g.MOV"
        good.write_bytes(b"x")
        result = validate_video_file(good, probe=False)
        assert result.ok and result.media.mime_type == "video/quicktime"

    def test_missing_ffprobe_is_a_warning_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.core.validation.shutil.which", lambda name: None)
        good = tmp_path / "g.mp4"
        good.write_bytes(b"x")
        result = validate_video_file(good)
        assert result.ok and result.warnings
        assert result.media.duration_seconds is None


def test_media_info_is_plain_data():
    assert MediaInfo("p", 1, "video/mp4").duration_seconds is None


def test_cover_failure_is_stored_separately_and_video_stays_published(env):
    fake = FakePublisher("youtube", PublishOutcome("published", "V1", {"video_id": "V1"},
                                                   cover_status="failed", cover_error="forbidden (HTTP 403)"))
    post_id = make_post(env, ("youtube",))
    engine(env, {"youtube": fake}).publish_post(post_id)
    job = job_by_platform(env, post_id)["youtube"]
    assert job.status == "published" and job.platform_media_id == "V1"
    assert job.cover_status == "failed" and "403" in job.cover_error
    assert job.error_message is None
