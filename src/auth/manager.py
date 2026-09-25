"""OAuth authentication manager."""

import asyncio
import webbrowser
from typing import Any

from src.accounts.manager import AccountManager
from src.auth.base import OAuthConfig
from src.auth.callback_server import OAuthCallbackServer
from src.auth.state import (
    OAuthStateStore,
)
from src.storage.database import Database
from src.storage.tokens import TokenEncryption


class AuthManager:
    """Manages OAuth authentication flows for all platforms."""

    PLATFORMS = {
        "instagram": "src.platforms.instagram.auth.InstagramAuth",
        "tiktok": "src.platforms.tiktok.auth.TikTokAuth",
        "youtube": "src.platforms.youtube.auth.YouTubeAuth",
    }

    def __init__(
        self,
        database: Database,
        encryption: TokenEncryption,
        account_manager: AccountManager,
        callback_host: str = "127.0.0.1",
        callback_port: int = 8080,
        callback_timeout: int = 300,
    ):
        self.database = database
        self.encryption = encryption
        self.account_manager = account_manager
        self.state_store = OAuthStateStore()
        self.callback_host = callback_host
        self.callback_port = callback_port
        self.callback_timeout = callback_timeout

        # Platform auth instances (lazy loaded)
        self._auth_adapters: dict[str, Any] = {}
        self._configs: dict[str, OAuthConfig] = {}

    def _get_auth_class(self, platform: str):
        """Dynamically import and return the platform auth class."""
        if platform == "instagram":
            from src.platforms.instagram.auth import InstagramAuth
            return InstagramAuth
        elif platform == "tiktok":
            from src.platforms.tiktok.auth import TikTokAuth
            return TikTokAuth
        elif platform == "youtube":
            from src.platforms.youtube.auth import YouTubeAuth
            return YouTubeAuth
        else:
            raise ValueError(f"Unsupported platform: {platform}")

    def configure_platform(
        self,
        platform: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        """Configure OAuth credentials for a platform."""
        if platform not in ["instagram", "tiktok", "youtube"]:
            raise ValueError(f"Unsupported platform: {platform}")

        # Generate redirect URI if not provided
        if redirect_uri is None:
            redirect_uri = f"http://{self.callback_host}:{self.callback_port}/callback/{platform}"

        # Create platform-specific config
        if platform == "instagram":
            from src.platforms.instagram.auth import InstagramAuth
            config = InstagramAuth.create_config(
                app_id=client_id,
                app_secret=client_secret,
                redirect_uri=redirect_uri,
            )
        elif platform == "tiktok":
            from src.platforms.tiktok.auth import TikTokAuth
            config = TikTokAuth.create_config(
                client_key=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
            )
        elif platform == "youtube":
            from src.platforms.youtube.auth import YouTubeAuth
            config = YouTubeAuth.create_config(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
            )
        else:
            raise ValueError(f"Unsupported platform: {platform}")

        self._configs[platform] = config

    def is_configured(self, platform: str) -> bool:
        """Check if platform is configured."""
        return platform in self._configs

    def get_configured_platforms(self) -> list[str]:
        """Get list of configured platforms."""
        return list(self._configs.keys())

    def get_missing_config_platforms(self) -> list[str]:
        """Get list of platforms missing configuration."""
        all_platforms = ["instagram", "tiktok", "youtube"]
        return [p for p in all_platforms if p not in self._configs]

    def _get_auth_adapter(self, platform: str):
        """Get or create auth adapter for platform."""
        if platform not in self._auth_adapters:
            config = self._configs.get(platform)
            if not config:
                raise ValueError(f"Platform {platform} not configured")

            auth_class = self._get_auth_class(platform)
            adapter = auth_class(
                config=config,
                state_store=self.state_store,
                token_encryption=self.encryption,
                account_manager=self.account_manager,
            )
            self._auth_adapters[platform] = adapter

        return self._auth_adapters[platform]

    async def connect_account(self, platform: str) -> dict[str, Any]:
        """Initiate OAuth flow for a platform."""
        if not self.is_configured(platform):
            raise ValueError(f"Platform {platform} is not configured. Set credentials in .env first.")

        auth = self._get_auth_adapter(platform)

        # Validate configuration
        auth.validate_configuration()

        # Generate PKCE pair
        pkce_verifier, pkce_challenge = auth.generate_pkce_pair()

        # Create OAuth state
        oauth_state = self.state_store.create_state(
            platform=platform,
            pkce_verifier=pkce_verifier,
        )

        # Generate authorization URL
        auth_url = auth.get_authorization_url(oauth_state.state, pkce_challenge)

        # Start callback server
        callback_server = OAuthCallbackServer(
            host="127.0.0.1",
            port=self.callback_port,
            state_store=self.state_store,
            timeout=300,
        )

        callback_data: dict[str, Any] = {}
        callback_received = asyncio.Event()

        def handle_callback(data: dict[str, Any]) -> None:
            callback_data.update(data)
            callback_received.set()

        # Start callback server
        callback_server.start(handle_callback)

        # Open browser for authorization
        webbrowser.open(auth_url)

        try:
            # Wait for callback
            try:
                await asyncio.wait_for(callback_received.wait(), timeout=300)
            except asyncio.TimeoutError:
                raise TimeoutError("OAuth authorization timed out")

            # Extract callback data
            code = callback_data.get("code")
            if not code:
                error = callback_data.get("error")
                error_desc = callback_data.get("error_description")
                raise Exception(f"Authorization failed: {error} - {error_desc}")

            # Exchange code for tokens
            auth = self._get_auth_adapter(platform)
            pkce_verifier = callback_data.get("pkce_verifier")
            token_result = await auth.exchange_code(code, pkce_verifier)

            # Get account identity
            account_identity = await auth.get_account_identity(token_result.access_token)

            # Create or update account
            account = self.account_manager.create_account(
                platform=platform,
                platform_account_id=account_identity["platform_account_id"],
                username=account_identity["username"],
                access_token=token_result.access_token,
                refresh_token=token_result.refresh_token,
                expires_in=token_result.expires_in,
                display_name=account_identity.get("display_name"),
                status="active",
            )

            return {
                "success": True,
                "account": {
                    "id": account.id,
                    "platform": platform,
                    "username": account.username,
                    "display_name": account.display_name,
                },
            }

        finally:
            callback_server.stop()

    def disconnect_account(self, account_id: int) -> bool:
        """Disconnect an account (revoke tokens and mark as disconnected)."""
        account = self.account_manager.get_account(account_id)

        # Try to revoke tokens on platform
        try:
            auth = self._get_auth_adapter(account.platform)
            access_token = account.get_access_token(self.encryption)
            asyncio.run(auth.revoke_tokens(access_token))
        except Exception:
            pass  # Ignore revocation errors

        # Disconnect in account manager
        self.account_manager.disconnect_account(account_id)
        return True

    def get_account_tokens(self, account_id: int) -> tuple[str, str | None]:
        """Get decrypted tokens for an account (internal use)."""
        account = self.account_manager.get_account(account_id)
        access_token = account.get_access_token(self.encryption)
        refresh_token = account.get_refresh_token(self.encryption)
        return access_token, refresh_token

    def refresh_account_tokens(self, account_id: int) -> bool:
        """Refresh tokens for an account."""
        account = self.account_manager.get_account(account_id)
        auth = self._get_auth_adapter(account.platform)

        refresh_token = account.get_refresh_token(self.encryption)
        if not refresh_token:
            return False

        try:
            auth_adapter = self._get_auth_adapter(account.platform)
            token_result = asyncio.run(auth_adapter.refresh_tokens(refresh_token))

            # Update account with new tokens
            self.account_manager.update_account(
                account_id,
                access_token=token_result.access_token,
                refresh_token=token_result.refresh_token,
                expires_in=token_result.expires_in,
            )
            return True
        except Exception:
            return False


def create_auth_manager(
    database: Database,
    encryption: TokenEncryption,
    account_manager: AccountManager,
    callback_host: str = "127.0.0.1",
    callback_port: int = 8080,
) -> "AuthManager":
    """Factory function to create AuthManager with configuration from environment."""
    import os

    auth_manager = AuthManager(
        database=database,
        encryption=encryption,
        account_manager=account_manager,
        callback_host=os.environ.get("OAUTH_CALLBACK_HOST", "127.0.0.1"),
        callback_port=int(os.environ.get("OAUTH_CALLBACK_PORT", "8080")),
    )

    # Configure platforms from environment
    if os.environ.get("INSTAGRAM_APP_ID") and os.environ.get("INSTAGRAM_APP_SECRET"):
        auth_manager.configure_platform(
            platform="instagram",
            client_id=os.environ["INSTAGRAM_APP_ID"],
            client_secret=os.environ["INSTAGRAM_APP_SECRET"],
        )

    if os.environ.get("TIKTOK_CLIENT_KEY") and os.environ.get("TIKTOK_CLIENT_SECRET"):
        auth_manager.configure_platform(
            platform="tiktok",
            client_id=os.environ["TIKTOK_CLIENT_KEY"],
            client_secret=os.environ["TIKTOK_CLIENT_SECRET"],
        )

    if os.environ.get("YOUTUBE_CLIENT_ID") and os.environ.get("YOUTUBE_CLIENT_SECRET"):
        auth_manager.configure_platform(
            platform="youtube",
            client_id=os.environ["YOUTUBE_CLIENT_ID"],
            client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        )

    return auth_manager