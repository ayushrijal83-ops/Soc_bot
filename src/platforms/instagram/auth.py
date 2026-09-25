"""Instagram (Meta) OAuth authentication."""

from typing import Any

import base64
import httpx

from src.auth.base import OAuthConfig, OAuthTokenResult
from src.auth.errors import (
    OAuthAccountIdentityError,
    OAuthConfigurationError,
    OAuthTokenExchangeError,
)


class InstagramAuth:
    """Instagram (Meta) OAuth authentication via Facebook Login for Instagram."""

    PLATFORM = "instagram"

    # OAuth endpoints
    AUTHORIZATION_URL = "https://www.facebook.com/v22.0/dialog/oauth"
    TOKEN_URL = "https://graph.facebook.com/v22.0/oauth/access_token"
    USER_INFO_URL = "https://graph.facebook.com/v22.0/me"

    # Default scopes for Instagram publishing
    DEFAULT_SCOPES = [
        "instagram_graph_user_profile",
        "instagram_graph_user_media",
        "pages_show_list",
        "pages_read_engagement",
    ]

    def __init__(
        self,
        config: OAuthConfig,
        state_store: "OAuthStateStore",
        token_encryption: "TokenEncryption",
        account_manager: "AccountManager",
    ):
        self.config = config
        self._state_store = state_store
        self.token_encryption = token_encryption
        self.account_manager = account_manager

    @property
    def platform(self) -> str:
        return self.PLATFORM

    def _get_state_store(self):
        return self._state_store

    def generate_pkce_pair(self) -> tuple[str, str]:
        """Generate PKCE verifier and challenge pair."""
        verifier = self._generate_pkce_verifier()
        challenge = self._generate_pkce_challenge(verifier)
        return verifier, challenge

    def _generate_pkce_verifier(self) -> str:
        import secrets
        return secrets.token_urlsafe(32)

    def _generate_pkce_challenge(self, verifier: str) -> str:
        import hashlib
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return challenge

    def get_authorization_url(self, state: str, pkce_challenge: str | None = None) -> str:
        """Generate the authorization URL for Instagram."""

        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.config.scopes),
            "state": state,
        }

        if self.config.pkce_required and pkce_challenge:
            params["code_challenge"] = pkce_challenge
            params["code_challenge_method"] = "S256"

        if self.config.additional_params:
            params.update(self.config.additional_params)

        return f"{self.AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, pkce_verifier: str) -> OAuthTokenResult:
        """Exchange authorization code for tokens."""

        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "redirect_uri": self.config.redirect_uri,
            "code": code,
            "code_verifier": pkce_verifier,
            "grant_type": "authorization_code",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.TOKEN_URL, data=data)

        if response.status_code != 200:
            raise OAuthTokenExchangeError(
                f"Token exchange failed: {response.text}",
                platform=self.PLATFORM,
                status_code=response.status_code,
                response_body=response.text,
            )

        data = response.json()

        # Exchange short-lived token for long-lived token (60 days)
        long_lived_token = await self._exchange_for_long_lived_token(data.get("access_token"))

        return OAuthTokenResult(
            access_token=long_lived_token or data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
            platform_account_id=None,  # Will be populated by get_account_identity
            raw_response=data,
        )

    async def _exchange_for_long_lived_token(self, short_lived_token: str) -> str | None:
        """Exchange short-lived token for long-lived token (60 days)."""

        url = "https://graph.facebook.com/v22.0/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "fb_exchange_token": short_lived_token,
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)

        if response.status_code == 200:
            data = response.json()
            return data.get("access_token")
        return None

    async def get_account_identity(self, access_token: str) -> dict[str, Any]:
        """Get Instagram account identity from access token."""

        # Get user's Instagram accounts (requires page access)
        url = f"{self.USER_INFO_URL}"
        params = {
            "fields": "id,name,instagram_business_account{id,username,profile_picture_url}",
            "access_token": access_token,
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)

        if response.status_code != 200:
            raise OAuthAccountIdentityError(
                f"Failed to get account identity: {response.text}",
                platform=self.PLATFORM,
            )

        data = response.json()

        # Extract Instagram Business Account info
        ig_account = data.get("instagram_business_account")
        if not ig_account:
            raise OAuthAccountIdentityError(
                "No Instagram Business Account found. Ensure Instagram account is linked to a Facebook Page.",
                platform=self.PLATFORM,
            )

        return {
            "platform_account_id": ig_account.get("id"),
            "username": ig_account.get("username"),
            "display_name": ig_account.get("name") or ig_account.get("username"),
            "profile_picture_url": ig_account.get("profile_picture_url"),
        }

    async def refresh_tokens(self, refresh_token: str) -> OAuthTokenResult:
        """Refresh access token using refresh token."""

        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "grant_type": "fb_exchange_token",
            "fb_exchange_token": refresh_token,
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(self.TOKEN_URL, params=data)

        if response.status_code != 200:
            raise OAuthTokenExchangeError(
                f"Token refresh failed: {response.text}",
                platform=self.PLATFORM,
                status_code=response.status_code,
                response_body=response.text,
            )

        data = response.json()
        return OAuthTokenResult(
            access_token=data.get("access_token"),
            refresh_token=refresh_token,  # Facebook doesn't return new refresh token
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
            raw_response=data,
        )

    async def revoke_tokens(self, access_token: str) -> bool:
        """Revoke tokens on platform."""

        url = "https://graph.facebook.com/v22.0/me/permissions"
        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient() as client:
            response = await client.delete(url, headers=headers)

        return response.status_code == 200

    def validate_configuration(self) -> None:
        """Validate OAuth configuration."""
        if not self.config.client_id:
            raise OAuthConfigurationError("Missing client_id for Instagram", platform=self.PLATFORM)
        if not self.config.client_secret:
            raise OAuthConfigurationError("Missing client_secret for Instagram", platform=self.PLATFORM)
        if not self.config.redirect_uri:
            raise OAuthConfigurationError("Missing redirect_uri for Instagram", platform=self.PLATFORM)
        if not self.config.scopes:
            raise OAuthConfigurationError("Missing scopes for Instagram", platform=self.PLATFORM)

    @staticmethod
    def create_config(
        app_id: str,
        app_secret: str,
        redirect_uri: str,
        scopes: list | None = None,
    ) -> OAuthConfig:
        """Create OAuth configuration for Instagram."""
        return OAuthConfig(
            platform="instagram",
            client_id=app_id,
            client_secret=app_secret,
            redirect_uri=redirect_uri,
            scopes=scopes or InstagramAuth.DEFAULT_SCOPES,
            authorization_url="https://www.facebook.com/v22.0/dialog/oauth",
            token_url="https://graph.facebook.com/v22.0/oauth/access_token",
            pkce_required=True,
        )


from urllib.parse import urlencode