"""Tests for AuthManager: dynamic loopback redirect, state/platform binding, token persistence.

All provider HTTP is mocked; the loopback callback server is real.
"""

import asyncio
import logging
import os
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from src.accounts.manager import AccountManager
from src.auth.base import OAuthTokenResult
from src.auth.errors import OAuthCallbackError, OAuthConfigurationError
from src.auth.manager import AuthManager, create_auth_manager
from src.storage.database import Database
from src.storage.tokens import TokenEncryption, generate_key


@pytest.fixture
def services():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    encryption = TokenEncryption(generate_key().encode())
    db = Database(f"sqlite:///{path}", encryption=encryption)
    db.init()
    accounts = AccountManager(db, encryption)
    manager = AuthManager(db, encryption, accounts, callback_timeout=5)
    manager.configure_platform("instagram", "ig_id", "IG_SECRET", redirect_uri=None)
    manager.configure_platform("tiktok", "tt_key", "TT_SECRET")
    manager.configure_platform("youtube", "yt_id", "YT_SECRET")
    yield manager, accounts, encryption
    db.engine.dispose()
    try:
        os.unlink(path)
    except PermissionError:
        pass


def _browser(callback_url_for):
    """Fake webbrowser.open: 'the provider' redirects to the loopback callback."""
    seen = {}

    def open_(auth_url):
        seen["auth_url"] = auth_url
        q = {k: v[0] for k, v in parse_qs(urlparse(auth_url).query).items()}
        seen["query"] = q
        url = callback_url_for(q)

        def hit():
            try:
                urllib.request.urlopen(url, timeout=5).read()
            except urllib.error.HTTPError as e:
                seen["status"] = e.code
            else:
                seen["status"] = 200

        threading.Thread(target=hit, daemon=True).start()
        return True

    return open_, seen


def _mock_adapter(manager, platform, token_result, identity):
    adapter = manager._get_auth_adapter(platform)
    adapter.exchange_code = AsyncMock(return_value=token_result)
    adapter.get_account_identity = AsyncMock(return_value=identity)
    return adapter


class TestDynamicLoopbackPort:
    def test_youtube_flow_uses_os_assigned_port(self, services):
        manager, accounts, encryption = services
        assert manager.callback_port == 0
        adapter = _mock_adapter(
            manager, "youtube",
            OAuthTokenResult(access_token="AT", refresh_token="RT", expires_in=3599),
            {"platform_account_id": "UC123", "username": "chan", "display_name": "Chan"},
        )
        open_, seen = _browser(lambda q: f"{q['redirect_uri']}?code=THE_CODE&state={q['state']}")

        with patch("src.auth.manager.webbrowser.open", open_):
            result = asyncio.run(manager.connect_account("youtube"))

        redirect = urlparse(seen["query"]["redirect_uri"])
        assert redirect.hostname == "127.0.0.1"
        assert redirect.path == "/callback/youtube"
        assert redirect.port not in (None, 0, 8080)
        # token exchange used the same redirect URI as the authorization request
        assert adapter.config.redirect_uri == seen["query"]["redirect_uri"]
        adapter.exchange_code.assert_awaited_once()
        assert adapter.exchange_code.await_args.args[0] == "THE_CODE"

        assert result["success"] is True
        account = accounts.get_account(result["account"]["id"])
        assert account.platform_account_id == "UC123"
        assert account.get_access_token(encryption) == "AT"
        assert account.get_refresh_token(encryption) == "RT"
        expires = account.expires_at.replace(tzinfo=timezone.utc)
        assert timedelta(seconds=3500) < expires - datetime.now(timezone.utc) <= timedelta(seconds=3599)

    def test_each_flow_gets_fresh_port_and_server_is_closed(self, services):
        manager, _, _ = services
        _mock_adapter(
            manager, "tiktok",
            OAuthTokenResult(access_token="AT", refresh_token="RT", expires_in=86400),
            {"platform_account_id": "open_id", "username": "tt"},
        )
        ports = []
        for _ in range(2):
            open_, seen = _browser(lambda q: f"{q['redirect_uri']}?code=c&state={q['state']}")
            with patch("src.auth.manager.webbrowser.open", open_):
                asyncio.run(manager.connect_account("tiktok"))
            port = urlparse(seen["query"]["redirect_uri"]).port
            ports.append(port)
            with pytest.raises(OSError):
                urllib.request.urlopen(f"http://127.0.0.1:{port}/callback/tiktok", timeout=1)
        assert all(p not in (0, 8080) for p in ports)

    def test_fixed_redirect_uri_is_honoured(self, services):
        manager, _, _ = services
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        fixed = f"http://127.0.0.1:{free}/callback/youtube"
        manager.configure_platform("youtube", "yt_id", "YT_SECRET", redirect_uri=fixed)
        _mock_adapter(
            manager, "youtube",
            OAuthTokenResult(access_token="AT", expires_in=10),
            {"platform_account_id": "UC1", "username": "c"},
        )
        open_, seen = _browser(lambda q: f"{q['redirect_uri']}?code=c&state={q['state']}")
        with patch("src.auth.manager.webbrowser.open", open_):
            asyncio.run(manager.connect_account("youtube"))
        assert seen["query"]["redirect_uri"] == fixed

    def test_fixed_redirect_uri_must_target_platform_callback(self, services):
        manager, _, _ = services
        with pytest.raises(OAuthConfigurationError):
            manager.configure_platform("youtube", "a", "b", redirect_uri="http://127.0.0.1:9000/callback/tiktok")

    def test_env_default_port_is_dynamic(self, services, monkeypatch):
        manager, accounts, encryption = services
        monkeypatch.delenv("OAUTH_CALLBACK_PORT", raising=False)
        built = create_auth_manager(manager.database, encryption, accounts)
        assert built.callback_port == 0
        assert built.callback_host == "127.0.0.1"


class TestStatePlatformBinding:
    def test_instagram_state_on_tiktok_callback_is_rejected(self, services):
        manager, accounts, _ = services
        adapter = _mock_adapter(
            manager, "instagram", OAuthTokenResult(access_token="AT"), {"platform_account_id": "1", "username": "x"}
        )
        open_, seen = _browser(
            lambda q: q["redirect_uri"].replace("/callback/instagram", "/callback/tiktok") + f"?code=c&state={q['state']}"
        )
        with patch("src.auth.manager.webbrowser.open", open_), pytest.raises(OAuthCallbackError):
            asyncio.run(manager.connect_account("instagram"))
        adapter.exchange_code.assert_not_awaited()
        assert seen["status"] == 400
        assert accounts.list_accounts() == []

    def test_tiktok_state_on_instagram_callback_is_rejected(self, services):
        manager, _, _ = services
        adapter = _mock_adapter(
            manager, "tiktok", OAuthTokenResult(access_token="AT"), {"platform_account_id": "1", "username": "x"}
        )
        open_, _ = _browser(
            lambda q: q["redirect_uri"].replace("/callback/tiktok", "/callback/instagram") + f"?code=c&state={q['state']}"
        )
        with patch("src.auth.manager.webbrowser.open", open_), pytest.raises(OAuthCallbackError):
            asyncio.run(manager.connect_account("tiktok"))
        adapter.exchange_code.assert_not_awaited()


class TestTokenPersistence:
    def test_tiktok_refresh_persists_rotated_refresh_token(self, services):
        manager, accounts, encryption = services
        account = accounts.create_account(
            platform="tiktok", platform_account_id="oid", username="tt",
            access_token="OLD_AT", refresh_token="OLD_RT",
        )
        adapter = manager._get_auth_adapter("tiktok")
        adapter.refresh_tokens = AsyncMock(return_value=OAuthTokenResult(
            access_token="NEW_AT", refresh_token="NEW_RT", expires_in=86400, refresh_expires_in=31536000,
        ))

        assert manager.refresh_account_tokens(account.id) is True
        adapter.refresh_tokens.assert_awaited_once_with("OLD_RT")

        stored = accounts.get_account(account.id)
        assert stored.get_access_token(encryption) == "NEW_AT"
        assert stored.get_refresh_token(encryption) == "NEW_RT"
        assert stored.expires_at is not None

    def test_instagram_refresh_reexchanges_access_token(self, services):
        manager, accounts, encryption = services
        account = accounts.create_account(
            platform="instagram", platform_account_id="1789", username="ig", access_token="OLD_LONG",
        )
        adapter = manager._get_auth_adapter("instagram")
        adapter.refresh_tokens = AsyncMock(return_value=OAuthTokenResult(access_token="NEW_LONG", expires_in=5184000))

        assert manager.refresh_account_tokens(account.id) is True
        adapter.refresh_tokens.assert_awaited_once_with("OLD_LONG")
        stored = accounts.get_account(account.id)
        assert stored.get_access_token(encryption) == "NEW_LONG"
        assert stored.get_refresh_token(encryption) is None

    def test_reconnect_updates_existing_account(self, services):
        manager, accounts, encryption = services
        existing = accounts.create_account(
            platform="instagram", platform_account_id="1789", username="old", access_token="OLD",
        )
        _mock_adapter(
            manager, "instagram",
            OAuthTokenResult(access_token="NEW", expires_in=5183944),
            {"platform_account_id": "1789", "username": "new"},
        )
        open_, _ = _browser(lambda q: f"{q['redirect_uri']}?code=c&state={q['state']}")
        with patch("src.auth.manager.webbrowser.open", open_):
            result = asyncio.run(manager.connect_account("instagram"))
        assert result["account"]["id"] == existing.id
        stored = accounts.get_account(existing.id)
        assert stored.username == "new"
        assert stored.get_access_token(encryption) == "NEW"


def test_no_secrets_in_logs_or_errors(services, caplog):
    manager, _, _ = services
    _mock_adapter(
        manager, "tiktok",
        OAuthTokenResult(access_token="ACCESS_SECRET", refresh_token="REFRESH_SECRET", expires_in=1),
        {"platform_account_id": "oid", "username": "tt"},
    )
    open_, _ = _browser(lambda q: f"{q['redirect_uri']}?code=CODE_SECRET&state={q['state']}")
    with caplog.at_level(logging.DEBUG), patch("src.auth.manager.webbrowser.open", open_):
        result = asyncio.run(manager.connect_account("tiktok"))
    text = caplog.text + repr(result)
    for secret in ("ACCESS_SECRET", "REFRESH_SECRET", "CODE_SECRET", "TT_SECRET"):
        assert secret not in text
