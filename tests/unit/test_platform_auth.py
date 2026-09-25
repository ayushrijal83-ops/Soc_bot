"""Tests for platform auth adapters (all HTTP mocked — no real provider calls)."""

import asyncio
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.auth.base import (
    OAuthTokenResult,
    PlatformAuth,
    expires_at_from,
    is_token_expiring,
)
from src.auth.errors import OAuthConfigurationError, OAuthTokenExchangeError
from src.platforms.instagram.auth import InstagramAuth
from src.platforms.tiktok.auth import TikTokAuth
from src.platforms.youtube.auth import YouTubeAuth


def _adapter(cls, config):
    return cls(config=config, state_store=MagicMock(), token_encryption=MagicMock(), account_manager=MagicMock())


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


class FakeClient:
    """Stands in for httpx.AsyncClient; records requests and returns canned responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _respond(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    async def get(self, url, **kwargs):
        return await self._respond("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._respond("POST", url, **kwargs)

    async def delete(self, url, **kwargs):
        return await self._respond("DELETE", url, **kwargs)


def _patch_http(module: str, responses):
    fake = FakeClient(responses)
    return fake, patch(f"{module}.httpx.AsyncClient", fake)


class TestInstagramAuth:
    """Business Login for Instagram (Instagram API with Instagram Login)."""

    def setup_method(self):
        self.config = InstagramAuth.create_config(
            app_id="test_app_id",
            app_secret="test_app_secret",
            redirect_uri="http://127.0.0.1:8080/callback/instagram",
        )
        self.auth = _adapter(InstagramAuth, self.config)

    def test_create_config(self):
        config = self.config
        assert config.platform == "instagram"
        assert config.client_id == "test_app_id"
        assert config.client_secret == "test_app_secret"
        assert config.authorization_url == "https://www.instagram.com/oauth/authorize"
        assert config.token_url == "https://api.instagram.com/oauth/access_token"
        # Instagram Login does not document PKCE
        assert config.pkce_required is False

    def test_default_scopes_are_instagram_login_scopes(self):
        assert self.config.scopes == ["instagram_business_basic", "instagram_business_content_publish"]
        for legacy in (
            "instagram_basic",
            "instagram_graph_user_profile",
            "instagram_graph_user_media",
            "pages_show_list",
            "pages_read_engagement",
        ):
            assert legacy not in self.config.scopes

    def test_validate_configuration_valid(self):
        self.auth.validate_configuration()

    def test_validate_configuration_missing_client_id(self):
        config = InstagramAuth.create_config(app_id="", app_secret="s", redirect_uri="http://127.0.0.1:1/callback/instagram")
        with pytest.raises(OAuthConfigurationError):
            _adapter(InstagramAuth, config).validate_configuration()

    def test_get_authorization_url(self):
        url = self.auth.get_authorization_url("test_state", "ignored_challenge")
        assert url.startswith("https://www.instagram.com/oauth/authorize?")
        assert "facebook.com" not in url
        q = _query(url)
        assert q["client_id"] == "test_app_id"
        assert q["redirect_uri"] == "http://127.0.0.1:8080/callback/instagram"
        assert q["response_type"] == "code"
        assert q["scope"] == "instagram_business_basic,instagram_business_content_publish"
        assert q["state"] == "test_state"
        assert "code_challenge" not in q

    def test_exchange_code_short_then_long_lived(self):
        fake, p = _patch_http("src.platforms.instagram.auth", [
            (200, {"data": [{"access_token": "SHORT", "user_id": "1789", "permissions": "instagram_business_basic"}]}),
            (200, {"access_token": "LONG", "token_type": "bearer", "expires_in": 5183944}),
        ])
        with p:
            result = asyncio.run(self.auth.exchange_code("CODE", None))

        (m1, url1, kw1), (m2, url2, kw2) = fake.calls
        assert (m1, url1) == ("POST", "https://api.instagram.com/oauth/access_token")
        assert kw1["data"] == {
            "client_id": "test_app_id",
            "client_secret": "test_app_secret",
            "grant_type": "authorization_code",
            "redirect_uri": "http://127.0.0.1:8080/callback/instagram",
            "code": "CODE",
        }
        assert (m2, url2) == ("GET", "https://graph.instagram.com/access_token")
        assert kw2["params"] == {"grant_type": "ig_exchange_token", "client_secret": "test_app_secret", "access_token": "SHORT"}

        assert result.access_token == "LONG"
        assert result.refresh_token is None
        assert result.expires_in == 5183944
        assert result.platform_account_id == "1789"
        assert result.raw_response is None

    def test_exchange_code_accepts_flat_response(self):
        _, p = _patch_http("src.platforms.instagram.auth", [
            (200, {"access_token": "SHORT", "user_id": 1789}),
            (200, {"access_token": "LONG", "expires_in": 100}),
        ])
        with p:
            result = asyncio.run(self.auth.exchange_code("CODE"))
        assert result.access_token == "LONG"
        assert result.platform_account_id == "1789"

    def test_long_lived_failure_raises_instead_of_falling_back(self):
        _, p = _patch_http("src.platforms.instagram.auth", [
            (200, {"access_token": "SHORT", "user_id": "1"}),
            (400, {"error": {"message": "bad"}}),
        ])
        with p, pytest.raises(OAuthTokenExchangeError) as exc:
            asyncio.run(self.auth.exchange_code("CODE"))
        assert "SHORT" not in str(exc.value)

    def test_token_exchange_error_does_not_leak_secret_or_code(self):
        _, p = _patch_http("src.platforms.instagram.auth", [(400, {"error_message": "Invalid code"})])
        with p, pytest.raises(OAuthTokenExchangeError) as exc:
            asyncio.run(self.auth.exchange_code("SECRET_CODE"))
        assert "SECRET_CODE" not in str(exc.value)
        assert "test_app_secret" not in str(exc.value)

    def test_refresh_uses_ig_refresh_token_and_returns_new_token(self):
        assert InstagramAuth.REFRESH_USES_ACCESS_TOKEN is True
        fake, p = _patch_http("src.platforms.instagram.auth", [
            (200, {"access_token": "NEW_LONG", "token_type": "bearer", "expires_in": 5184000}),
        ])
        with p:
            result = asyncio.run(self.auth.refresh_tokens("OLD_LONG"))
        method, url, kw = fake.calls[0]
        assert (method, url) == ("GET", "https://graph.instagram.com/refresh_access_token")
        assert kw["params"] == {"grant_type": "ig_refresh_token", "access_token": "OLD_LONG"}
        assert result.access_token == "NEW_LONG"
        assert result.expires_in == 5184000
        assert result.refresh_token is None

    def test_get_account_identity_uses_instagram_me_user_id(self):
        fake, p = _patch_http("src.platforms.instagram.auth", [
            (200, {"id": "APP_SCOPED", "user_id": "17841400000", "username": "acct", "name": "Acct"}),
        ])
        with p:
            identity = asyncio.run(self.auth.get_account_identity("TOKEN"))
        _, url, kw = fake.calls[0]
        assert url == "https://graph.instagram.com/v25.0/me"
        assert "user_id" in kw["params"]["fields"]
        assert "instagram_business_account" not in kw["params"]["fields"]
        # token goes in a header, not the URL
        assert kw["headers"]["Authorization"] == "Bearer TOKEN"
        assert "access_token" not in kw["params"]
        assert identity["platform_account_id"] == "17841400000"
        assert identity["username"] == "acct"

    def test_revoke_makes_no_request_and_reports_unsupported(self):
        fake, p = _patch_http("src.platforms.instagram.auth", [])
        with p:
            assert asyncio.run(self.auth.revoke_tokens("TOKEN")) is False
        assert fake.calls == []


class TestTikTokAuth:
    """TikTok Login Kit for Desktop."""

    def setup_method(self):
        self.config = TikTokAuth.create_config(
            client_key="test_client_key",
            client_secret="test_client_secret",
            redirect_uri="http://127.0.0.1:8080/callback/tiktok",
        )
        self.auth = _adapter(TikTokAuth, self.config)

    def test_create_config(self):
        assert self.config.platform == "tiktok"
        assert self.config.client_id == "test_client_key"
        assert self.config.authorization_url == "https://www.tiktok.com/v2/auth/authorize/"
        assert self.config.token_url == "https://open.tiktokapis.com/v2/oauth/token/"
        assert self.config.pkce_required is True

    def test_pkce_challenge_is_hex_sha256(self):
        verifier, challenge = self.auth.generate_pkce_pair()
        assert challenge == hashlib.sha256(verifier.encode()).hexdigest()
        assert len(challenge) == 64
        assert all(c in "0123456789abcdef" for c in challenge)
        b64 = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        assert challenge != b64

    def test_get_authorization_url(self):
        verifier, challenge = self.auth.generate_pkce_pair()
        url = self.auth.get_authorization_url("test_state", challenge)
        assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?")
        q = _query(url)
        assert q["client_key"] == "test_client_key"
        assert q["redirect_uri"] == "http://127.0.0.1:8080/callback/tiktok"
        assert q["response_type"] == "code"
        assert q["scope"] == "video.upload,video.publish,user.info.basic"
        assert q["state"] == "test_state"
        assert q["code_challenge"] == hashlib.sha256(verifier.encode()).hexdigest()
        assert q["code_challenge_method"] == "S256"

    def test_exchange_code_respects_expires_in(self):
        _, p = _patch_http("src.platforms.tiktok.auth", [(200, {
            "open_id": "oid", "scope": "user.info.basic", "access_token": "AT", "expires_in": 86400,
            "refresh_token": "RT", "refresh_expires_in": 31536000, "token_type": "Bearer",
        })])
        before = datetime.now(timezone.utc)
        with p:
            result = asyncio.run(self.auth.exchange_code("CODE", "VERIFIER"))
        assert result.expires_in == 86400
        assert result.refresh_expires_in == 31536000
        assert result.expires_at.tzinfo is not None
        assert before + timedelta(seconds=86400) <= result.expires_at <= datetime.now(timezone.utc) + timedelta(seconds=86400)

    def test_refresh_returns_rotated_refresh_token(self):
        fake, p = _patch_http("src.platforms.tiktok.auth", [(200, {
            "access_token": "NEW_AT", "expires_in": 86400, "refresh_token": "NEW_RT", "refresh_expires_in": 31536000,
        })])
        with p:
            result = asyncio.run(self.auth.refresh_tokens("OLD_RT"))
        _, url, kw = fake.calls[0]
        assert url == "https://open.tiktokapis.com/v2/oauth/token/"
        assert kw["data"]["grant_type"] == "refresh_token"
        assert kw["data"]["refresh_token"] == "OLD_RT"
        assert result.access_token == "NEW_AT"
        assert result.refresh_token == "NEW_RT"


class TestYouTubeAuth:
    """Google OAuth 2.0 for installed apps."""

    def setup_method(self):
        self.config = YouTubeAuth.create_config(
            client_id="test_client_id",
            client_secret="test_client_secret",
            redirect_uri="http://127.0.0.1:53123/callback/youtube",
        )
        self.auth = _adapter(YouTubeAuth, self.config)

    def test_create_config(self):
        assert self.config.platform == "youtube"
        assert self.config.authorization_url == "https://accounts.google.com/o/oauth2/v2/auth"
        assert self.config.token_url == "https://oauth2.googleapis.com/token"
        assert self.config.pkce_required is True
        assert self.config.additional_params == {"access_type": "offline", "prompt": "consent"}

    def test_pkce_challenge_is_rfc7636_base64url(self):
        verifier, challenge = self.auth.generate_pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        assert challenge == expected

    def test_get_authorization_url(self):
        _, challenge = self.auth.generate_pkce_pair()
        url = self.auth.get_authorization_url("test_state", challenge)
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        q = _query(url)
        assert q["client_id"] == "test_client_id"
        assert q["redirect_uri"] == "http://127.0.0.1:53123/callback/youtube"
        assert q["scope"] == " ".join(YouTubeAuth.DEFAULT_SCOPES)
        assert q["code_challenge"] == challenge
        assert q["code_challenge_method"] == "S256"
        assert q["access_type"] == "offline"
        assert q["prompt"] == "consent"

    def test_refresh_keeps_refresh_token_when_google_omits_it(self):
        _, p = _patch_http("src.platforms.youtube.auth", [(200, {"access_token": "NEW_AT", "expires_in": 3599})])
        with p:
            result = asyncio.run(self.auth.refresh_tokens("RT"))
        assert result.refresh_token == "RT"
        assert result.expires_in == 3599


class TestExpiryModel:
    def test_expires_at_from_uses_provider_value(self):
        now = datetime(2026, 9, 25, tzinfo=timezone.utc)
        assert expires_at_from(86400, now) == now + timedelta(days=1)
        assert expires_at_from(None, now) is None

    def test_token_result_without_expires_in_has_no_invented_expiry(self):
        assert OAuthTokenResult(access_token="x").expires_at is None

    def test_is_token_expiring(self):
        now = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
        assert is_token_expiring(now + timedelta(seconds=60), margin_seconds=300, now=now) is True
        assert is_token_expiring(now + timedelta(hours=2), margin_seconds=300, now=now) is False
        assert is_token_expiring(now - timedelta(seconds=1), margin_seconds=0, now=now) is True
        # SQLite returns naive datetimes: treated as UTC
        assert is_token_expiring((now + timedelta(hours=2)).replace(tzinfo=None), now=now) is False
        assert is_token_expiring(None, now=now) is False


def test_adapters_share_platform_auth_base():
    for cls in (InstagramAuth, TikTokAuth, YouTubeAuth):
        assert issubclass(cls, PlatformAuth)
