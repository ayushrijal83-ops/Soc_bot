"""OAuth authentication manager."""

import asyncio
import json
import os
import webbrowser
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.accounts.manager import AccountManager, AccountNotFoundError
from src.auth.base import OAuthConfig, parse_scopes
from src.auth.callback_server import CALLBACK_PATH_PREFIX, OAuthCallbackServer
from src.auth.errors import OAuthCallbackError, OAuthConfigurationError, OAuthStateError
from src.auth.state import OAuthStateStore
from src.storage.database import Database
from src.storage.tokens import TokenEncryption

SUPPORTED_PLATFORMS = ("instagram", "tiktok", "youtube")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def is_loopback_redirect(uri: str) -> bool:
    return (urlparse(uri).hostname or "") in LOOPBACK_HOSTS


class AuthManager:
    """Manages OAuth authentication flows for all platforms."""

    def __init__(
        self,
        database: Database,
        encryption: TokenEncryption,
        account_manager: AccountManager,
        callback_host: str = "127.0.0.1",
        callback_port: int = 0,
        callback_timeout: int = 300,
    ):
        self.database = database
        self.encryption = encryption
        self.account_manager = account_manager
        self.state_store = OAuthStateStore()
        self.callback_host = callback_host
        # 0 = OS picks a free loopback port per flow (Google installed-app guidance).
        self.callback_port = callback_port
        self.callback_timeout = callback_timeout

        # Platform auth instances (lazy loaded)
        self._auth_adapters: dict[str, Any] = {}
        self._configs: dict[str, OAuthConfig] = {}
        # Platforms whose redirect URI is fixed (pre-registered with the provider).
        self._fixed_redirect_uris: dict[str, str] = {}
        # Paste mode (https redirect that isn't this machine): called with instructions, returns the
        # URL the browser ended on. Set by the CLI; None means paste mode can't be used.
        self.redirect_prompt: Callable[[str], str | None] | None = None

    def _get_auth_class(self, platform: str):
        """Dynamically import and return the platform auth class."""
        if platform == "instagram":
            from src.platforms.instagram.auth import InstagramAuth
            return InstagramAuth
        if platform == "tiktok":
            from src.platforms.tiktok.auth import TikTokAuth
            return TikTokAuth
        if platform == "youtube":
            from src.platforms.youtube.auth import YouTubeAuth
            return YouTubeAuth
        raise ValueError(f"Unsupported platform: {platform}")

    def configure_platform(
        self,
        platform: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        """Configure OAuth credentials for a platform.

        With no ``redirect_uri`` the callback server binds a dynamic loopback port for each
        flow and the redirect URI is built from the actual port. A fixed ``redirect_uri``
        must point at this machine's loopback callback server: ``http://<host>:<port>/callback/<platform>``.
        """
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported platform: {platform}")

        auth_class = self._get_auth_class(platform)
        if redirect_uri:
            parsed = urlparse(redirect_uri)
            paste_mode = getattr(auth_class, "REQUIRES_REGISTERED_REDIRECT", False) and not is_loopback_redirect(redirect_uri)
            if paste_mode:
                # A registered https page (e.g. the project's GitHub Pages callback page): the user pastes
                # the URL the browser lands on back into Soc_bot.
                if parsed.scheme != "https" or not parsed.hostname:
                    raise OAuthConfigurationError("A non-local redirect URI must be https://", platform=platform)
            elif parsed.path.rstrip("/") != f"{CALLBACK_PATH_PREFIX}{platform}" or not parsed.port:
                raise OAuthConfigurationError(
                    f"Redirect URI must be http://<host>:<port>{CALLBACK_PATH_PREFIX}{platform}",
                    platform=platform,
                )
            self._fixed_redirect_uris[platform] = redirect_uri

        if platform == "instagram":
            config = auth_class.create_config(
                app_id=client_id, app_secret=client_secret, redirect_uri=redirect_uri, scopes=scopes
            )
        elif platform == "tiktok":
            config = auth_class.create_config(
                client_key=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scopes=scopes
            )
        else:
            config = auth_class.create_config(
                client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scopes=scopes
            )

        self._configs[platform] = config
        self._auth_adapters.pop(platform, None)

    def is_configured(self, platform: str) -> bool:
        """Check if platform is configured."""
        return platform in self._configs

    def get_configured_platforms(self) -> list[str]:
        """Get list of configured platforms."""
        return list(self._configs.keys())

    def get_missing_config_platforms(self) -> list[str]:
        """Get list of platforms missing configuration."""
        return [p for p in SUPPORTED_PLATFORMS if p not in self._configs]

    def _get_auth_adapter(self, platform: str):
        """Get or create auth adapter for platform."""
        if platform not in self._auth_adapters:
            config = self._configs.get(platform)
            if not config:
                raise ValueError(f"Platform {platform} not configured")

            auth_class = self._get_auth_class(platform)
            self._auth_adapters[platform] = auth_class(
                config=config,
                state_store=self.state_store,
                token_encryption=self.encryption,
                account_manager=self.account_manager,
            )

        return self._auth_adapters[platform]

    def _start_callback_server(self, platform: str) -> tuple[OAuthCallbackServer, str]:
        """Start the loopback callback server; return it and the redirect URI to use."""
        fixed = self._fixed_redirect_uris.get(platform)
        if fixed:
            parsed = urlparse(fixed)
            server = OAuthCallbackServer(
                host=parsed.hostname, port=parsed.port, state_store=self.state_store, timeout=self.callback_timeout
            )
            server.start()
            return server, fixed

        server = OAuthCallbackServer(
            host=self.callback_host, port=self.callback_port, state_store=self.state_store, timeout=self.callback_timeout
        )
        server.start()
        return server, server.redirect_uri(platform)

    async def connect_account(self, platform: str) -> dict[str, Any]:
        """Run the OAuth flow for a platform and store the account."""
        if not self.is_configured(platform):
            raise ValueError(f"Platform {platform} is not configured. Set credentials in .env first.")

        auth = self._get_auth_adapter(platform)
        fixed = self._fixed_redirect_uris.get(platform)
        if getattr(auth, "REQUIRES_REGISTERED_REDIRECT", False) and not fixed:
            raise OAuthConfigurationError(
                f"{platform.title()} only redirects to a URI registered exactly in the app dashboard. "
                f"Set {platform.upper()}_REDIRECT_URI in .env to that URI (see docs/API_INTEGRATIONS.md).",
                platform=platform,
            )
        paste_mode = bool(fixed) and not is_loopback_redirect(fixed)
        if paste_mode:
            if self.redirect_prompt is None:
                raise OAuthCallbackError("This redirect URI needs the interactive CLI (paste mode)", platform=platform)
            callback_server, redirect_uri = None, fixed
        else:
            callback_server, redirect_uri = self._start_callback_server(platform)

        try:
            # The authorization URL and the token exchange must use the same redirect URI.
            auth.config.redirect_uri = redirect_uri
            auth.validate_configuration()

            pkce_verifier, pkce_challenge = auth.generate_pkce_pair()
            oauth_state = self.state_store.create_state(platform=platform, pkce_verifier=pkce_verifier)
            auth_url = auth.get_authorization_url(oauth_state.state, pkce_challenge)

            webbrowser.open(auth_url)

            if paste_mode:
                callback_data = await asyncio.to_thread(self._read_pasted_redirect, platform, redirect_uri)
            else:
                # The HTTP server runs in its own thread; wait on its threading.Event off the loop.
                callback_data = await asyncio.to_thread(callback_server.wait_for_callback)

            code = callback_data.get("code")
            if not code or callback_data.get("error"):
                raise OAuthCallbackError(
                    f"Authorization failed: {callback_data.get('error')} - {callback_data.get('error_description')}",
                    platform=platform,
                    error=callback_data.get("error"),
                    error_description=callback_data.get("error_description"),
                )
            # The handler already bound state to the callback path; re-check against this flow.
            if callback_data.get("platform") != platform:
                raise OAuthStateError("OAuth state was issued for a different platform", platform=platform)

            token_result = await auth.exchange_code(code, callback_data.get("pkce_verifier"))
            identity = await auth.get_account_identity(token_result.access_token)

            account = self._store_account(platform, identity, token_result)

            return {
                "success": True,
                "account": {
                    "id": account.id,
                    "platform": platform,
                    "username": account.username,
                    "display_name": account.display_name,
                    "scopes": parse_scopes(token_result.scope),
                },
            }
        finally:
            if callback_server is not None:
                callback_server.stop()

    def _read_pasted_redirect(self, platform: str, redirect_uri: str) -> dict[str, Any]:
        """Paste mode: validate the URL the browser landed on, like the callback server does.

        Same checks: registered base URI, provider error, code present, state single-use and issued
        for this platform. Never echoes the code.
        """
        pasted = (self.redirect_prompt(
            "After approving in the browser you land on the Soc_bot callback page. "
            "Copy the FULL address from the browser's address bar and paste it here"
        ) or "").strip()
        parsed = urlparse(pasted)
        registered = urlparse(redirect_uri)
        data: dict[str, Any] = {"code": None, "state": None, "error": None, "error_description": None,
                                "platform": None, "pkce_verifier": None}
        same_base = (parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/")) == (
            registered.scheme, registered.netloc.lower(), registered.path.rstrip("/"))
        if not same_base:
            data["error"], data["error_description"] = "wrong_url", "The pasted address is not the registered redirect URI."
            return data
        query = parse_qs(parsed.query)
        data["code"] = (query.get("code", [None])[0] or "").removesuffix("#_") or None
        data["state"] = query.get("state", [None])[0]
        if query.get("error"):
            data["error"] = query["error"][0]
            data["error_description"] = query.get("error_description", query.get("error_reason", [""]))[0]
            return data
        if not data["code"]:
            data["error"], data["error_description"] = "missing_code", "Missing authorization code in the pasted address"
            return data
        if not data["state"]:
            data["error"], data["error_description"] = "missing_state", "Missing state in the pasted address"
            return data
        try:
            state = self.state_store.consume_state(data["state"], platform=platform)
        except OAuthStateError as e:
            data["error"], data["error_description"] = "invalid_state", str(e)
            return data
        data["platform"], data["pkce_verifier"] = state.platform, state.pkce_verifier
        return data

    def _store_account(self, platform: str, identity: dict[str, Any], token_result) -> Any:
        """Create the account, or update tokens/identity if it is already connected."""
        expires_at = token_result.expires_at
        # Granted scopes (never tokens) so publishing can report a missing permission precisely.
        scopes = parse_scopes(token_result.scope)
        meta_json = json.dumps({"scopes": scopes}) if scopes else None
        try:
            existing = self.account_manager.find_account(platform, identity["platform_account_id"])
        except AccountNotFoundError:
            return self.account_manager.create_account(
                platform=platform,
                platform_account_id=identity["platform_account_id"],
                username=identity["username"],
                access_token=token_result.access_token,
                refresh_token=token_result.refresh_token,
                expires_at=expires_at,
                display_name=identity.get("display_name"),
                status="active",
                meta_json=meta_json,
            )

        return self.account_manager.update_account(
            existing.id,
            username=identity["username"],
            display_name=identity.get("display_name"),
            status="active",
            access_token=token_result.access_token,
            refresh_token=token_result.refresh_token,
            expires_at=expires_at,
            meta_json=meta_json,
        )

    def disconnect_account(self, account_id: int) -> bool:
        """Disconnect an account (revoke tokens where the platform supports it, mark disconnected)."""
        account = self.account_manager.get_account(account_id)

        try:
            auth = self._get_auth_adapter(account.platform)
            access_token = account.get_access_token(self.encryption)
            asyncio.run(auth.revoke_tokens(access_token))
        except Exception:  # noqa: BLE001, S110 - local disconnect must succeed even if revocation fails
            pass

        self.account_manager.disconnect_account(account_id)
        return True

    def get_account_tokens(self, account_id: int) -> tuple[str, str | None]:
        """Get decrypted tokens for an account (internal use)."""
        account = self.account_manager.get_account(account_id)
        access_token = account.get_access_token(self.encryption)
        refresh_token = account.get_refresh_token(self.encryption)
        return access_token, refresh_token

    def refresh_account_tokens(self, account_id: int) -> bool:
        """Refresh an account's access token and persist the result (including a rotated refresh token)."""
        account = self.account_manager.get_account(account_id)
        auth = self._get_auth_adapter(account.platform)

        # Instagram re-exchanges the long-lived access token itself; others use the refresh token.
        if getattr(auth, "REFRESH_USES_ACCESS_TOKEN", False):
            token = account.get_access_token(self.encryption)
        else:
            token = account.get_refresh_token(self.encryption)
        if not token:
            return False

        try:
            token_result = asyncio.run(auth.refresh_tokens(token))
        except Exception:  # noqa: BLE001 - caller only needs success/failure; errors carry no tokens
            return False

        self.account_manager.update_account(
            account_id,
            access_token=token_result.access_token,
            refresh_token=token_result.refresh_token,  # None = provider has none; keeps the stored one
            expires_at=token_result.expires_at,
        )
        return True


def create_auth_manager(
    database: Database,
    encryption: TokenEncryption,
    account_manager: AccountManager,
) -> AuthManager:
    """Create an AuthManager configured from environment variables."""
    auth_manager = AuthManager(
        database=database,
        encryption=encryption,
        account_manager=account_manager,
        callback_host=os.environ.get("OAUTH_CALLBACK_HOST", "127.0.0.1"),
        callback_port=int(os.environ.get("OAUTH_CALLBACK_PORT", "0")),
    )

    env = {
        "instagram": ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET", "INSTAGRAM_REDIRECT_URI"),
        "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI"),
        "youtube": ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REDIRECT_URI"),
    }
    for platform, (id_var, secret_var, redirect_var) in env.items():
        if os.environ.get(id_var) and os.environ.get(secret_var):
            auth_manager.configure_platform(
                platform=platform,
                client_id=os.environ[id_var],
                client_secret=os.environ[secret_var],
                redirect_uri=os.environ.get(redirect_var) or None,
            )

    return auth_manager
