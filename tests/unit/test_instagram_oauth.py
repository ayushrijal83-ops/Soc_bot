"""Instagram OAuth (Business Login for Instagram) end to end, with a mocked Meta API. No real calls.

Covers the authorize URL, both redirect modes (registered loopback / registered https + paste),
callback validation, token exchange and long-lived exchange, identity, persistence, reconnect,
and the Phase-5C diagnosis ("Invalid platform app" = Meta App ID used instead of Instagram App ID).
"""

import asyncio
import json
import os
import socket
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.accounts.manager import AccountManager
from src.auth.errors import (
    OAuthCallbackError,
    OAuthConfigurationError,
    OAuthTokenExchangeError,
)
from src.auth.manager import AuthManager, create_auth_manager
from src.storage.database import Database
from src.storage.tokens import TokenEncryption, generate_key
from tests.unit.test_auth_manager import _browser

APP_ID = "1111222233334444"
APP_SECRET = "IG_APP_SECRET_VALUE"
PAGES_REDIRECT = "https://ayushrijal83-ops.github.io/Soc_bot/oauth/instagram-callback.html"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeMeta:
    """Stands in for httpx.AsyncClient in src.platforms.instagram.auth."""

    def __init__(self, token_status=200, token_body=None, permissions=("instagram_business_basic",
                                                                       "instagram_business_content_publish")):
        self.token_status = token_status
        self.token_body = token_body
        self.permissions = list(permissions)
        self.calls = []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, **kw):
        self.calls.append(("POST", url, data))
        assert url == "https://api.instagram.com/oauth/access_token"
        # Real responses are flat, with permissions as a JSON list and user_id as a number.
        body = self.token_body or {"access_token": "IGAA_SHORT_SECRET", "user_id": 17841400000000000,
                                   "permissions": self.permissions}
        return httpx.Response(self.token_status, json=body, request=httpx.Request("POST", url))

    async def get(self, url, params=None, headers=None, **kw):
        self.calls.append(("GET", url, params))
        if url == "https://graph.instagram.com/access_token":
            assert params["grant_type"] == "ig_exchange_token" and params["access_token"] == "IGAA_SHORT_SECRET"
            return httpx.Response(200, json={"access_token": "IGAA_LONG_SECRET", "token_type": "bearer",
                                             "expires_in": 5183944}, request=httpx.Request("GET", url))
        assert url == "https://graph.instagram.com/v25.0/me"
        assert headers["Authorization"] == "Bearer IGAA_LONG_SECRET"
        return httpx.Response(200, json={"id": "APP_SCOPED_1", "user_id": "17841400000000000",
                                         "username": "nopex_12b", "name": "Nopex"},
                              request=httpx.Request("GET", url))


@pytest.fixture
def env():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    enc = TokenEncryption(generate_key().encode())
    db = Database(f"sqlite:///{path}", encryption=enc)
    db.init()
    accounts = AccountManager(db, enc)
    yield db, accounts
    db.engine.dispose()
    try:
        os.unlink(path)
    except PermissionError:
        pass


def manager(env, redirect_uri):
    db, accounts = env
    m = AuthManager(db, db.encryption, accounts, callback_timeout=5)
    m.configure_platform("instagram", APP_ID, APP_SECRET, redirect_uri=redirect_uri)
    return m


def run(m, meta, browser):
    with patch("src.auth.manager.webbrowser.open", browser), patch("src.platforms.instagram.auth.httpx.AsyncClient", meta):
        return asyncio.run(m.connect_account("instagram"))


def paste_browser(seen):
    def open_(url):
        seen["auth_url"] = url
        seen["query"] = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        return True

    return open_


# ---------------------------------------------------------------------------------------------
# Configuration / URL
# ---------------------------------------------------------------------------------------------

class TestConfiguration:
    def test_dynamic_port_refused_before_browser_opens(self, env):
        m = manager(env, None)
        opened = []
        with pytest.raises(OAuthConfigurationError, match="INSTAGRAM_REDIRECT_URI"):
            run(m, FakeMeta(), opened.append)
        assert opened == []

    def test_https_non_local_redirect_allowed_only_for_instagram(self, env):
        manager(env, PAGES_REDIRECT)  # ok
        db, accounts = env
        m = AuthManager(db, db.encryption, accounts)
        with pytest.raises(OAuthConfigurationError):
            m.configure_platform("youtube", "a", "b", redirect_uri=PAGES_REDIRECT)
        with pytest.raises(OAuthConfigurationError):
            m.configure_platform("tiktok", "a", "b", redirect_uri=PAGES_REDIRECT)
        with pytest.raises(OAuthConfigurationError, match="https"):
            m.configure_platform("instagram", "a", "b", redirect_uri="http://example.com/cb")

    def test_env_redirect_uri_is_used(self, env, monkeypatch):
        db, accounts = env
        monkeypatch.setenv("INSTAGRAM_APP_ID", APP_ID)
        monkeypatch.setenv("INSTAGRAM_APP_SECRET", APP_SECRET)
        monkeypatch.setenv("INSTAGRAM_REDIRECT_URI", PAGES_REDIRECT)
        auth = create_auth_manager(db, db.encryption, accounts)
        assert auth._fixed_redirect_uris["instagram"] == PAGES_REDIRECT

    def test_authorization_url(self, env):
        m = manager(env, PAGES_REDIRECT)
        m.redirect_prompt = lambda msg: None
        seen = {}
        with pytest.raises(OAuthCallbackError):
            run(m, FakeMeta(), paste_browser(seen))
        url = urlparse(seen["auth_url"])
        assert f"{url.scheme}://{url.netloc}{url.path}" == "https://www.instagram.com/oauth/authorize"
        q = seen["query"]
        assert q["client_id"] == APP_ID
        assert q["redirect_uri"] == PAGES_REDIRECT
        assert q["response_type"] == "code"
        assert q["scope"] == "instagram_business_basic,instagram_business_content_publish"
        assert len(q["state"]) >= 32
        assert "code_challenge" not in q and "code_challenge_method" not in q  # no PKCE documented
        assert APP_SECRET not in seen["auth_url"]


# ---------------------------------------------------------------------------------------------
# Loopback mode (registered http://127.0.0.1:<port>/callback/instagram)
# ---------------------------------------------------------------------------------------------

class TestLoopbackMode:
    def test_full_connect(self, env):
        db, accounts = env
        redirect = f"http://127.0.0.1:{free_port()}/callback/instagram"
        m = manager(env, redirect)
        meta = FakeMeta()
        open_, seen = _browser(lambda q: f"{q['redirect_uri']}?code=AQCODE_SECRET&state={q['state']}")
        result = run(m, meta, open_)
        assert seen["query"]["redirect_uri"] == redirect
        form = meta.calls[0][2]
        assert form == {"client_id": APP_ID, "client_secret": APP_SECRET, "grant_type": "authorization_code",
                        "redirect_uri": redirect, "code": "AQCODE_SECRET"}
        acc = accounts.get_account(result["account"]["id"])
        assert (acc.platform, acc.platform_account_id, acc.username, acc.display_name) == (
            "instagram", "17841400000000000", "nopex_12b", "Nopex")
        assert acc.get_access_token(db.encryption) == "IGAA_LONG_SECRET"
        assert "IGAA_LONG_SECRET" not in acc.access_token_enc and acc.refresh_token_enc is None
        assert json.loads(acc.meta_json)["scopes"] == ["instagram_business_basic", "instagram_business_content_publish"]
        left = acc.expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
        assert timedelta(days=59) < left < timedelta(days=61)


# ---------------------------------------------------------------------------------------------
# Paste mode (registered https page, e.g. GitHub Pages)
# ---------------------------------------------------------------------------------------------

class TestPasteMode:
    def connect(self, env, make_paste, meta=None):
        m = manager(env, PAGES_REDIRECT)
        seen = {}
        m.redirect_prompt = lambda message: make_paste(seen["query"])
        return run(m, meta or FakeMeta(), paste_browser(seen)), m

    def test_valid_paste_connects(self, env):
        result, _ = self.connect(env, lambda q: f"{PAGES_REDIRECT}?code=AQCODE_SECRET&state={q['state']}#_")
        assert result["success"] and result["account"]["username"] == "nopex_12b"
        blob = repr(result)
        for secret in ("AQCODE_SECRET", "IGAA_LONG_SECRET", "IGAA_SHORT_SECRET", APP_SECRET):
            assert secret not in blob

    def test_hash_underscore_is_not_part_of_code(self, env):
        meta = FakeMeta()
        # Some clients keep "#_" encoded into the code value itself.
        self.connect(env, lambda q: f"{PAGES_REDIRECT}?state={q['state']}&code=AQCODE%23_", meta)
        assert meta.calls[0][2]["code"] == "AQCODE"

    def test_trailing_slash_difference_tolerated(self, env):
        result, _ = self.connect(env, lambda q: f"{PAGES_REDIRECT}/?code=C&state={q['state']}")
        assert result["success"]

    @pytest.mark.parametrize("make_paste,error", [
        (lambda q: "https://evil.example.com/cb?code=C&state=" + q["state"], "wrong_url"),
        (lambda q: f"{PAGES_REDIRECT}?state={q['state']}", "missing_code"),
        (lambda q: f"{PAGES_REDIRECT}?code=C", "missing_state"),
        (lambda q: f"{PAGES_REDIRECT}?code=C&state=not-the-state", "invalid_state"),
        (lambda q: f"{PAGES_REDIRECT}?error=access_denied&error_reason=user_denied&state={q['state']}", "access_denied"),
        (lambda q: "garbage", "wrong_url"),
        (lambda q: "", "wrong_url"),
    ])
    def test_bad_pastes_rejected_without_exchange(self, env, make_paste, error):
        meta = FakeMeta()
        with pytest.raises(OAuthCallbackError) as exc:
            self.connect(env, make_paste, meta)
        assert exc.value.error == error
        assert meta.calls == []  # never exchanges a code from a bad callback
        assert "AQCODE" not in str(exc.value)

    def test_expired_state_rejected(self, env):
        m = manager(env, PAGES_REDIRECT)
        seen = {}

        def paste(message):
            m.state_store.get_state(seen["query"]["state"]).expires_at = time.time() - 1
            return f"{PAGES_REDIRECT}?code=C&state={seen['query']['state']}"

        m.redirect_prompt = paste
        meta = FakeMeta()
        with pytest.raises(OAuthCallbackError) as exc:
            run(m, meta, paste_browser(seen))
        assert exc.value.error == "invalid_state" and meta.calls == []

    def test_wrong_platform_state_rejected(self, env):
        m = manager(env, PAGES_REDIRECT)
        tiktok_state = m.state_store.create_state("tiktok").state
        m.redirect_prompt = lambda message: f"{PAGES_REDIRECT}?code=C&state={tiktok_state}"
        meta = FakeMeta()
        with pytest.raises(OAuthCallbackError) as exc:
            run(m, meta, paste_browser({}))
        assert exc.value.error == "invalid_state" and meta.calls == []

    def test_state_is_single_use(self, env):
        m = manager(env, PAGES_REDIRECT)
        seen, pastes = {}, []

        def paste(message):
            pastes.append(f"{PAGES_REDIRECT}?code=C&state={seen['query']['state']}")
            return pastes[0]

        m.redirect_prompt = paste
        run(m, FakeMeta(), paste_browser(seen))
        assert m._read_pasted_redirect("instagram", PAGES_REDIRECT)["error"] == "invalid_state"

    def test_paste_mode_requires_interactive_prompt(self, env):
        m = manager(env, PAGES_REDIRECT)
        opened = []
        with pytest.raises(OAuthCallbackError, match="interactive"):
            run(m, FakeMeta(), opened.append)
        assert opened == []

    def test_invalid_platform_app_error_explains_the_fix(self, env):
        meta = FakeMeta(token_status=400, token_body={"error_type": "OAuthException", "code": 400,
                                                      "error_message": "Invalid platform app"})
        with pytest.raises(OAuthTokenExchangeError) as exc:
            self.connect(env, lambda q: f"{PAGES_REDIRECT}?code=AQCODE_SECRET&state={q['state']}", meta)
        text = str(exc.value)
        assert "Invalid platform app" in text and "Instagram* App ID" in text
        assert APP_SECRET not in text and "AQCODE_SECRET" not in text

    def test_other_token_exchange_failure(self, env):
        meta = FakeMeta(token_status=400, token_body={"error_type": "OAuthException", "code": 400,
                                                      "error_message": "Error validating verification code"})
        with pytest.raises(OAuthTokenExchangeError, match="Error validating verification code"):
            self.connect(env, lambda q: f"{PAGES_REDIRECT}?code=C&state={q['state']}", meta)

    def test_reconnect_updates_existing_account(self, env):
        _, accounts = env
        first, _ = self.connect(env, lambda q: f"{PAGES_REDIRECT}?code=C1&state={q['state']}")
        second, _ = self.connect(env, lambda q: f"{PAGES_REDIRECT}?code=C2&state={q['state']}")
        assert first["account"]["id"] == second["account"]["id"]
        assert len([a for a in accounts.list_accounts() if a.platform == "instagram"]) == 1


def test_permissions_string_form_also_supported(env):
    meta = FakeMeta(token_body={"data": [{"access_token": "IGAA_SHORT_SECRET", "user_id": "178",
                                          "permissions": "instagram_business_basic,instagram_business_content_publish"}]})
    m = manager(env, PAGES_REDIRECT)
    seen = {}
    m.redirect_prompt = lambda message: f"{PAGES_REDIRECT}?code=C&state={seen['query']['state']}"
    result = run(m, meta, paste_browser(seen))
    assert result["account"]["scopes"] == ["instagram_business_basic", "instagram_business_content_publish"]



def test_authorization_url_forces_login_so_another_account_can_be_added():
    from src.platforms.instagram.auth import InstagramAuth

    config = InstagramAuth.create_config("app", "secret", "https://example.github.io/cb")
    # PlatformAuth.get_authorization_url merges additional_params into the authorize URL.
    assert config.additional_params == {"force_reauth": "true"}
