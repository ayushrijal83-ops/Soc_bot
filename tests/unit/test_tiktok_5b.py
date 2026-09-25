"""Phase 5B: TikTok end-to-end with a mocked TikTok API (no real provider calls).

Covers real OAuth plumbing (loopback callback, state, hex PKCE, token persistence, scopes),
creator-info preflight, Direct Post publishing through the real adapter + engine + content
intake, retries, crash/resume, duplicates, lifecycle, history and the VERIFY CLI.
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlparse

import httpx
import pytest

from src.accounts.manager import AccountManager
from src.auth.manager import AuthManager
from src.cli.content_menu import run_history, verify_and_publish
from src.content.intake import ContentIntake
from src.content.profile import Profile, ProfileStore
from src.core.publisher import PublisherEngine
from src.platforms.tiktok.publisher import TikTokPublisher
from src.storage.database import Database
from src.storage.tokens import TokenEncryption, generate_key
from tests.unit.test_auth_manager import _browser
from tests.unit.test_content import make_package

API = "https://open.tiktokapis.com/v2"
UPLOAD_URL = "https://open-upload.tiktokapis.com/upload/?upload_id=U1&upload_token=SIGNED_SECRET"
ACCESS = "act.REAL_LOOKING_ACCESS_TOKEN"


class FakeTikTok:
    """Scriptable stand-in for open.tiktokapis.com + the upload host."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.creator = {"creator_nickname": "Test Creator", "creator_username": "test_creator",
                        "privacy_level_options": ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                        "max_video_post_duration_sec": 600, "comment_disabled": False}
        self.init_responses: list = []
        self.statuses = ["PROCESSING_UPLOAD", "PUBLISH_COMPLETE"]
        self.status_hook = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        if url.endswith("/creator_info/query/"):
            return self.ok(self.creator)
        if url.endswith("/video/init/"):
            if self.init_responses:
                item = self.init_responses.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            return self.ok({"publish_id": "v_pub_url~v2.123", "upload_url": UPLOAD_URL})
        if url.startswith("https://open-upload.tiktokapis.com/upload/"):
            return httpx.Response(201)
        if url.endswith("/status/fetch/"):
            if self.status_hook:
                self.status_hook()
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            data = {"status": status}
            if status == "FAILED":
                data["fail_reason"] = "video_pull_failed"
            return self.ok(data)
        raise AssertionError(f"unexpected {request.method} {url}")

    @staticmethod
    def ok(data):
        return httpx.Response(200, json={"data": data, "error": {"code": "ok", "message": "", "log_id": "L"}})

    @staticmethod
    def err(status, code):
        return httpx.Response(status, json={"data": {}, "error": {"code": code, "message": "m", "log_id": "L"}})

    def count(self, suffix):
        return sum(1 for r in self.requests if str(r.url).split("?")[0].endswith(suffix))

    def uploads(self):
        return [r for r in self.requests if str(r.url).startswith("https://open-upload")]


@pytest.fixture
def env(tmp_path):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    enc = TokenEncryption(generate_key().encode())
    db = Database(f"sqlite:///{path}", encryption=enc)
    db.init()
    accounts = AccountManager(db, enc)
    account = accounts.create_account(
        platform="tiktok", platform_account_id="open_id_1", username="Test Creator", access_token=ACCESS,
        refresh_token="rft.REFRESH", expires_at=datetime.now(timezone.utc) + timedelta(hours=20),
        meta_json=json.dumps({"scopes": ["user.info.basic", "video.publish"]}),
    )
    fake = FakeTikTok()
    publisher = TikTokPublisher(client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
                                sleep=lambda s: None, poll_interval=0)
    engine = PublisherEngine(db, accounts, publishers={"tiktok": publisher}, sleep=lambda s: None,
                             retry_delays=(0, 0, 0), probe_media=False)
    intake = ContentIntake(db, accounts, engine, root=tmp_path / "content", stability=0,
                           sleep=lambda s: None, probe_media=False)
    ProfileStore(db).save(Profile(accounts={"instagram": [], "tiktok": [account.id], "youtube": []},
                                  tiktok_privacy_level="SELF_ONLY", cover_enabled=False, mode="verify"))
    yield db, accounts, account, fake, engine, intake, tmp_path / "content"
    db.engine.dispose()
    try:
        os.unlink(path)
    except PermissionError:
        pass


def package(env, **kw):
    kw.setdefault("name", "tiktok_test")
    kw.setdefault("cover", None)
    make_package(env[6], **kw)
    (entry,) = [e for e in env[5].scan() if e.package.content_id == kw["name"]]
    return entry


# ---------------------------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------------------------

class FakeTikTokAuthHTTP:
    """Mocks httpx.AsyncClient inside src.platforms.tiktok.auth (token + user info)."""

    def __init__(self, scope="user.info.basic,video.publish"):
        self.scope = scope
        self.token_form = None

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, headers=None):
        assert url == "https://open.tiktokapis.com/v2/oauth/token/"
        self.token_form = data
        return httpx.Response(200, json={
            "open_id": "open_id_new", "scope": self.scope, "access_token": "act.NEW_SECRET", "expires_in": 86400,
            "refresh_token": "rft.NEW_SECRET", "refresh_expires_in": 31536000, "token_type": "Bearer"},
            request=httpx.Request("POST", url))

    async def get(self, url, params=None, headers=None):
        assert headers["Authorization"] == "Bearer act.NEW_SECRET"
        return httpx.Response(200, json={"data": {"user": {"open_id": "open_id_new", "display_name": "New Creator"}},
                                         "error": {"code": "ok"}}, request=httpx.Request("GET", url))


class TestTikTokOAuth:
    def connect(self, env, scope="user.info.basic,video.publish"):
        db, accounts, *_ = env
        manager = AuthManager(db, db.encryption, accounts, callback_timeout=5)
        manager.configure_platform("tiktok", "fake_client_key", "FAKE_CLIENT_SECRET")
        http = FakeTikTokAuthHTTP(scope)
        open_, seen = _browser(lambda q: f"{q['redirect_uri']}?code=AUTH_CODE_SECRET&state={q['state']}&scopes={scope}")
        with patch("src.auth.manager.webbrowser.open", open_), patch("src.platforms.tiktok.auth.httpx.AsyncClient", http):
            import asyncio

            result = asyncio.run(manager.connect_account("tiktok"))
        return result, seen, http

    def test_full_connect_flow(self, env):
        db, accounts, *_ = env
        result, seen, http = self.connect(env)
        q = seen["query"]
        assert seen["auth_url"].startswith("https://www.tiktok.com/v2/auth/authorize/?")
        assert q["client_key"] == "fake_client_key" and q["scope"] == "user.info.basic,video.publish"
        assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 64  # hex SHA-256
        redirect = urlparse(q["redirect_uri"])
        assert redirect.hostname == "127.0.0.1" and redirect.path == "/callback/tiktok" and redirect.port
        # token exchange: same redirect, PKCE verifier matches the challenge
        form = http.token_form
        assert form["code"] == "AUTH_CODE_SECRET" and form["redirect_uri"] == q["redirect_uri"]
        assert hashlib.sha256(form["code_verifier"].encode()).hexdigest() == q["code_challenge"]
        assert form["client_key"] == "fake_client_key" and form["grant_type"] == "authorization_code"
        # stored encrypted + scopes recorded, no secrets in result
        acc = accounts.get_account(result["account"]["id"])
        assert acc.platform == "tiktok" and acc.status == "active" and acc.platform_account_id == "open_id_new"
        assert acc.get_access_token(db.encryption) == "act.NEW_SECRET"
        assert "act.NEW_SECRET" not in (acc.access_token_enc or "") and acc.refresh_token_enc
        assert json.loads(acc.meta_json)["scopes"] == ["user.info.basic", "video.publish"]
        assert result["account"]["scopes"] == ["user.info.basic", "video.publish"]
        blob = repr(result)
        for secret in ("act.NEW_SECRET", "rft.NEW_SECRET", "FAKE_CLIENT_SECRET", "AUTH_CODE_SECRET"):
            assert secret not in blob
        assert (acc.expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)) > timedelta(hours=23)

    def test_missing_publish_scope_is_recorded_and_blocks(self, env):
        db, _accounts, _, fake, _engine, _intake, _root = env
        result, *_ = self.connect(env, scope="user.info.basic")
        new_id = result["account"]["id"]
        from src.cli.account_menu import _missing_publish_scopes

        assert _missing_publish_scopes("tiktok", result["account"]["scopes"]) == ["video.publish"]
        ProfileStore(db).save(Profile(accounts={"instagram": [], "tiktok": [new_id], "youtube": []},
                                      tiktok_privacy_level="SELF_ONLY"))
        entry = package(env)
        assert entry.status == "BLOCKED"
        assert any("video.publish" in e for d in entry.destinations for e in d.errors)
        assert fake.requests == []


# ---------------------------------------------------------------------------------------------
# Creator info preflight (VERIFY screen only)
# ---------------------------------------------------------------------------------------------

class TestCreatorInfoPreflight:
    def test_live_plan_shows_creator_and_privacy(self, env):
        *_, fake, _engine, intake, _root = env
        entry = intake.live_entry(package(env))
        (dest,) = entry.destinations
        assert dest.ready and entry.status == "READY"
        assert "creator: Test Creator (@test_creator)" in dest.notes
        assert "privacy: SELF_ONLY" in dest.notes and any("max duration: 600s" in n for n in dest.notes)
        assert dest.cover_status == "none"
        assert fake.count("/creator_info/query/") == 1 and fake.count("/video/init/") == 0

    def test_scan_and_dry_run_never_query_creator_info(self, env):
        fake = env[3]
        package(env)
        assert fake.requests == []

    def test_privacy_not_allowed_blocks_before_confirmation(self, env):
        fake, intake = env[3], env[5]
        fake.creator["privacy_level_options"] = ["PUBLIC_TO_EVERYONE"]
        entry = intake.live_entry(package(env))
        assert entry.status == "BLOCKED"
        assert "SELF_ONLY is not allowed" in entry.destinations[0].errors[0]

    def test_malformed_creator_info(self, env):
        fake, intake = env[3], env[5]
        fake.creator = {"creator_nickname": "x"}  # no privacy_level_options
        entry = intake.live_entry(package(env))
        assert entry.status == "BLOCKED" and "malformed" in entry.destinations[0].errors[0]

    def test_preflight_provider_error_blocks(self, env):
        fake, intake = env[3], env[5]
        fake.handler_orig = fake.handler
        fake.creator = None

        def bad(request):
            fake.requests.append(request)
            return FakeTikTok.err(401, "access_token_invalid")

        env[4].publishers["tiktok"].client = httpx.Client(transport=httpx.MockTransport(bad))
        entry = intake.live_entry(package(env))
        assert entry.status == "BLOCKED" and "access_token_invalid" in entry.destinations[0].errors[0]


# ---------------------------------------------------------------------------------------------
# Publishing through content intake
# ---------------------------------------------------------------------------------------------

class TestTikTokPublishing:
    def test_self_only_publish_lifecycle_and_history(self, env, capsys):
        db, _accounts, _account, fake, _engine, intake, root = env
        entry = package(env)
        assert entry.status == "READY"
        result = intake.publish(entry.package)
        assert result.outcome == "published" and result.stage == "published"
        assert (root / "published" / "tiktok_test" / "video.mp4").exists()
        (job,) = result.jobs
        assert job.status == "published" and job.platform_media_id == "v_pub_url~v2.123"  # private: publish_id
        assert job.cover_status is None  # no cover in package
        init = json.loads(next(r for r in fake.requests if str(r.url).endswith("/video/init/")).content)
        assert init["post_info"]["privacy_level"] == "SELF_ONLY"
        assert init["source_info"]["source"] == "FILE_UPLOAD"
        (upload,) = fake.uploads()
        size = (root / "published" / "tiktok_test" / "video.mp4").stat().st_size
        assert upload.headers["Content-Range"] == f"bytes 0-{size - 1}/{size}"
        assert upload.headers["Content-Length"] == str(size) and len(upload.content) == size
        assert "Authorization" not in upload.headers  # signed URL, no bearer token
        # signed upload URL never persisted
        with db.session() as s:
            dump = " ".join(str(r) for r in s.execute(__import__("sqlalchemy").text(
                "select response_json, error_json from publish_attempts")).fetchall())
        assert "SIGNED_SECRET" not in dump and ACCESS not in dump
        with patch("src.cli.content_menu.clear_screen"):
            run_history(intake)
        out = capsys.readouterr().out
        assert "Tiktok Test Creator — VIDEO: PUBLISHED" in out

    def test_cover_in_package_reported_not_supported(self, env):
        db, _, account, fake, _engine, intake, _root = env
        ProfileStore(db).save(Profile(accounts={"instagram": [], "tiktok": [account.id], "youtube": []},
                                      tiktok_privacy_level="SELF_ONLY", cover_enabled=True))
        entry = package(env, cover="cover.jpg")
        assert entry.destinations[0].cover_status == "not_supported"
        result = intake.publish(entry.package)
        (job,) = result.jobs
        assert job.status == "published" and job.cover_status == "not_supported"
        assert all(b"cover" not in r.content for r in fake.requests if str(r.url).endswith("/video/init/"))

    def test_second_publish_reports_already_published(self, env):
        *_, fake, engine, intake, root = env
        intake.publish(package(env).package)
        before = len(fake.requests)
        make_package(root, name="tiktok_test_again", cover=None)
        (entry,) = [e for e in intake.scan() if e.package.content_id == "tiktok_test_again"]
        assert entry.status == "PUBLISHED"
        result = intake.publish(entry.package)
        assert result.outcome == "skipped" and "Already published" in result.message
        assert len(fake.requests) == before and len(engine.store.recent_jobs()) == 1

    def test_crash_after_upload_resumes_by_polling_only(self, env):
        """Upload completed, process died before the final status: restart must not upload again."""
        *_, fake, engine, intake, _root = env

        def die():
            fake.status_hook = None
            raise KeyboardInterrupt  # process killed mid-poll

        fake.status_hook = die
        with pytest.raises(KeyboardInterrupt):
            intake.publish(package(env).package)
        assert (fake.count("/video/init/"), len(fake.uploads())) == (1, 1)
        assert engine.store.recent_jobs()[0].status == "processing"

        (entry,) = intake.scan()
        assert entry.status == "RESUME"
        result = intake.publish(entry.package)
        assert result.outcome == "published"
        assert (fake.count("/video/init/"), len(fake.uploads())) == (1, 1)  # no second init/upload

    def test_still_processing_is_not_failed(self, env):
        *_, fake, engine, intake, root = env
        fake.statuses = ["PROCESSING_UPLOAD"]
        engine.publishers["tiktok"].POLL_ATTEMPTS = 3
        result = intake.publish(package(env).package)
        assert result.outcome == "in_progress" and result.jobs[0].status == "processing"
        assert (root / "publishing" / "tiktok_test").is_dir()

    def test_failed_processing_moves_to_failed_without_retry(self, env):
        *_, fake, _engine, intake, root = env
        fake.statuses = ["FAILED"]
        result = intake.publish(package(env).package)
        assert result.outcome == "failed" and (root / "failed" / "tiktok_test").is_dir()
        assert "video_pull_failed" in result.jobs[0].error_message
        assert fake.count("/video/init/") == 1  # rejected content is not retried

    @pytest.mark.parametrize("response,retried", [
        (FakeTikTok.err(429, "rate_limit_exceeded"), True),
        (FakeTikTok.err(500, "internal_error"), True),
        (httpx.ReadTimeout("slow"), True),
        (FakeTikTok.err(401, "access_token_invalid"), False),
        (FakeTikTok.err(401, "scope_not_authorized"), False),
        (FakeTikTok.err(403, "privacy_level_option_mismatch"), False),
        (FakeTikTok.err(403, "spam_risk_too_many_posts"), False),
    ])
    def test_engine_retry_rules(self, env, response, retried):
        *_, fake, _engine, intake, _root = env
        fake.init_responses = [response]
        result = intake.publish(package(env).package)
        if retried:
            assert result.outcome == "published" and fake.count("/video/init/") == 2
            assert result.jobs[0].retry_count == 1
        else:
            assert result.outcome == "failed" and fake.count("/video/init/") == 1


class TestVerifyCli:
    def test_one_confirmation_with_creator_info(self, env, capsys):
        *_, _fake, _engine, intake, _root = env
        entry = package(env)
        with patch("src.cli.content_menu.confirm", return_value=True) as confirm:
            result = verify_and_publish(intake, entry)
        assert confirm.call_count == 1 and result.outcome == "published"
        out = capsys.readouterr().out
        assert "creator: Test Creator" in out and "privacy: SELF_ONLY" in out
        assert "✓ Test Creator" in out
        assert ACCESS not in out and "SIGNED_SECRET" not in out
