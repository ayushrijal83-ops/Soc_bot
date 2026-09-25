"""YouTube (Google) OAuth authentication."""

from typing import Any

import httpx

from src.auth.base import OAuthConfig, OAuthTokenResult, PlatformAuth
from src.auth.errors import (
    OAuthAccountIdentityError,
    OAuthTokenExchangeError,
)


class YouTubeAuth(PlatformAuth):
    """YouTube OAuth authentication via Google OAuth 2.0."""

    PLATFORM = "youtube"

    # OAuth endpoints
    AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USER_INFO_URL = "https://www.googleapis.com/youtube/v3/channels"

    # Default scopes for YouTube publishing
    DEFAULT_SCOPES = (
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.readonly",
    )

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
        """Get YouTube channel identity from access token."""

        url = self.USER_INFO_URL
        params = {
            "part": "snippet,contentDetails,statistics",
            "mine": "true",
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
                f"Failed to get account identity: {data.get('error', {}).get('message', 'Unknown error')}",
                platform=self.PLATFORM,
            )

        items = data.get("items", [])
        if not items:
            raise OAuthAccountIdentityError(
                "No YouTube channel found for this account",
                platform=self.PLATFORM,
            )

        channel = items[0]
        snippet = channel.get("snippet", {})

        return {
            "platform_account_id": channel.get("id"),
            "username": snippet.get("customUrl") or snippet.get("title"),
            "display_name": snippet.get("title"),
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url"),
        }

    async def refresh_tokens(self, refresh_token: str) -> OAuthTokenResult:
        """Refresh access token using refresh token."""

        data = {
            "client_id": self.config.client_id,
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
            refresh_token=data.get("refresh_token", refresh_token),
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
            raw_response=data,
        )

    async def revoke_tokens(self, access_token: str) -> bool:
        """Revoke tokens on platform."""

        url = "https://oauth2.googleapis.com/revoke"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "token": access_token,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data, headers=headers)

        return response.status_code == 200

    @staticmethod
    def create_config(
        client_id: str,
        client_secret: str,
        redirect_uri: str | None,
        scopes: list | None = None,
    ) -> OAuthConfig:
        """Create OAuth configuration for YouTube."""
        return OAuthConfig(
            platform="youtube",
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=list(scopes or YouTubeAuth.DEFAULT_SCOPES),
            authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            pkce_required=True,
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
            },
        )