"""TikTok OAuth authentication."""

import base64
from typing import Any
from urllib.parse import urlencode

import httpx

from src.auth.base import OAuthConfig, OAuthTokenResult
from src.auth.errors import (
    OAuthAccountIdentityError,
    OAuthConfigurationError,
    OAuthTokenExchangeError,
)


class TikTokAuth:
    """TikTok OAuth authentication via TikTok Login Kit."""

    PLATFORM = "tiktok"

    # OAuth endpoints
    AUTHORIZATION_URL = "https://www.tiktok.com/v2/auth/authorize/"
    TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
    USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"

    # Default scopes for TikTok publishing
    DEFAULT_SCOPES = [
        "video.upload",
        "video.publish",
        "user.info.basic",
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
        """Generate the authorization URL for TikTok."""
        params = {
            "client_key": self.config.client_id,
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
            "client_key": self.config.client_id,
            "client_secret": self.config.client_secret,
            "redirect_uri": self.config.redirect_uri,
            "code": code,
            "code_verifier": pkce_verifier,
            "grant_type": "authorization_code",
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.TOKEN_URL, data=data, headers=headers)

        if response.status_code != 200:
            raise OAuthTokenExchangeError(
                f"Token exchange failed: {response.text}",
                platform=self.PLATFORM,
                status_code=response.status_code,
                response_body=response.text,
            )

        data = response.json()

        # Check for error in response
        if data.get("error"):
            raise OAuthTokenExchangeError(
                f"Token exchange failed: {data.get('error_description', data.get('error'))}",
                platform=self.PLATFORM,
                response_body=str(data),
            )

        return OAuthTokenResult(
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
            platform_account_id=None,  # Will be populated by get_account_identity
            raw_response=data,
        )

    async def get_account_identity(self, access_token: str) -> dict[str, Any]:
        """Get TikTok account identity from access token."""

        url = self.USER_INFO_URL
        params = {
            "fields": "open_id,union_id,display_name,avatar_url",
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)

        if response.status_code != 200:
            raise OAuthAccountIdentityError(
                f"Failed to get account identity: {response.text}",
                platform=self.PLATFORM,
            )

        data = response.json()

        if data.get("error"):
            raise OAuthAccountIdentityError(
                f"Failed to get account identity: {data.get('message', 'Unknown error')}",
                platform=self.PLATFORM,
            )

        user_data = data.get("data", {}).get("user", {})

        return {
            "platform_account_id": user_data.get("open_id") or user_data.get("union_id"),
            "username": user_data.get("display_name"),
            "display_name": user_data.get("display_name"),
            "avatar_url": user_data.get("avatar_url"),
        }

    async def refresh_tokens(self, refresh_token: str) -> OAuthTokenResult:
        """Refresh access token using refresh token."""

        data = {
            "client_key": self.config.client_id,
            "client_secret": self.config.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.TOKEN_URL, data=data, headers=headers)

        if response.status_code != 200:
            raise OAuthTokenExchangeError(
                f"Token refresh failed: {response.text}",
                platform=self.PLATFORM,
                status_code=response.status_code,
                response_body=response.text,
            )

        data = response.json()

        if data.get("error"):
            raise OAuthTokenExchangeError(
                f"Token refresh failed: {data.get('error_description', data.get('error'))}",
                platform=self.PLATFORM,
                response_body=str(data),
            )

        return OAuthTokenResult(
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
            raw_response=data,
        )

    async def revoke_tokens(self, access_token: str) -> bool:
        """Revoke tokens on platform."""

        url = "https://open.tiktokapis.com/v2/oauth/revoke/"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "client_key": self.config.client_id,
            "client_secret": self.config.client_secret,
            "token": access_token,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data, headers=headers)

        return response.status_code == 200

    def validate_configuration(self) -> None:
        """Validate OAuth configuration."""
        if not self.config.client_id:
            raise OAuthConfigurationError("Missing client_key for TikTok", platform=self.PLATFORM)
        if not self.config.client_secret:
            raise OAuthConfigurationError("Missing client_secret for TikTok", platform=self.PLATFORM)
        if not self.config.redirect_uri:
            raise OAuthConfigurationError("Missing redirect_uri for TikTok", platform=self.PLATFORM)
        if not self.config.scopes:
            raise OAuthConfigurationError("Missing scopes for TikTok", platform=self.PLATFORM)

    @staticmethod
    def create_config(
        client_key: str,
        client_secret: str,
        redirect_uri: str,
        scopes: list | None = None,
    ) -> OAuthConfig:
        """Create OAuth configuration for TikTok."""
        return OAuthConfig(
            platform="tiktok",
            client_id=client_key,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=scopes or TikTokAuth.DEFAULT_SCOPES,
            authorization_url="https://www.tiktok.com/v2/auth/authorize/",
            token_url="https://open.tiktokapis.com/v2/oauth/token/",
            pkce_required=True,
        )