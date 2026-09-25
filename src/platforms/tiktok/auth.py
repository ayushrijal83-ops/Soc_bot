"""TikTok OAuth authentication."""

from typing import Any
from urllib.parse import urlencode

import httpx

from src.auth.base import OAuthConfig, OAuthTokenResult, PlatformAuth
from src.auth.errors import (
    OAuthAccountIdentityError,
    OAuthTokenExchangeError,
)


class TikTokAuth(PlatformAuth):
    """TikTok OAuth via Login Kit for Desktop (PKCE with a HEX-encoded S256 challenge)."""

    PLATFORM = "tiktok"
    # Login Kit for Desktop: code_challenge = hex(SHA256(code_verifier)), not RFC 7636 base64url.
    PKCE_CHALLENGE_ENCODING = "hex"
    # Login Kit: "A comma (,) separated string of authorization scope(s)".
    SCOPE_SEPARATOR = ","

    # OAuth endpoints
    AUTHORIZATION_URL = "https://www.tiktok.com/v2/auth/authorize/"
    TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
    USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"

    # Default scopes for TikTok publishing
    # Only what Direct Post needs: identity + video.publish. (video.upload is the separate
    # "upload to inbox/drafts" flow, which Soc_bot doesn't use; requesting a scope the app
    # doesn't have enabled makes authorization fail.)
    DEFAULT_SCOPES = (
        "user.info.basic",
        "video.publish",
    )

    def get_authorization_url(self, state: str, pkce_challenge: str | None = None) -> str:
        """Generate the authorization URL for TikTok."""
        params = {
            "client_key": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": self.SCOPE_SEPARATOR.join(self.config.scopes),
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
        if _tiktok_error(data):
            raise OAuthTokenExchangeError(
                f"Token exchange failed: {_tiktok_error(data)}",
                platform=self.PLATFORM,
                response_body=str(data),
            )

        return OAuthTokenResult(
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
            refresh_expires_in=data.get("refresh_expires_in"),
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

        # v2 always returns an "error" object; {"code": "ok"} means success.
        if _tiktok_error(data):
            raise OAuthAccountIdentityError(
                f"Failed to get account identity: {_tiktok_error(data)}",
                platform=self.PLATFORM,
            )

        user_data = (data.get("data") or {}).get("user") or {}
        if not (user_data.get("open_id") or user_data.get("union_id")):
            raise OAuthAccountIdentityError("TikTok user info returned no open_id", platform=self.PLATFORM)

        return {
            "platform_account_id": user_data.get("open_id") or user_data.get("union_id"),
            "username": user_data.get("display_name"),
            "display_name": user_data.get("display_name"),
            "avatar_url": user_data.get("avatar_url"),
        }

    async def refresh_tokens(self, refresh_token: str) -> OAuthTokenResult:
        """Refresh the access token.

        TikTok may rotate the refresh token: "You must use the newly-returned token
        if the value is different than the previous one." The returned result carries
        whichever refresh token TikTok sent back; callers must persist it.
        """

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

        if _tiktok_error(data):
            raise OAuthTokenExchangeError(
                f"Token refresh failed: {_tiktok_error(data)}",
                platform=self.PLATFORM,
                response_body=str(data),
            )

        return OAuthTokenResult(
            access_token=data.get("access_token"),
            # Rotation: use the new refresh token whenever TikTok returns one.
            refresh_token=data.get("refresh_token") or refresh_token,
            expires_in=data.get("expires_in"),
            refresh_expires_in=data.get("refresh_expires_in"),
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

    @staticmethod
    def create_config(
        client_key: str,
        client_secret: str,
        redirect_uri: str | None,
        scopes: list | None = None,
    ) -> OAuthConfig:
        """Create OAuth configuration for TikTok."""
        return OAuthConfig(
            platform="tiktok",
            client_id=client_key,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=list(scopes or TikTokAuth.DEFAULT_SCOPES),
            authorization_url="https://www.tiktok.com/v2/auth/authorize/",
            token_url="https://open.tiktokapis.com/v2/oauth/token/",
            pkce_required=True,
        )


def _tiktok_error(data: dict[str, Any]) -> str | None:
    """Error text from a TikTok response, or None on success.

    OAuth endpoints report failures as ``"error": "invalid_grant"`` (+ error_description); the v2 API
    endpoints always include ``"error": {"code": "ok", ...}``, where anything but "ok" is a failure.
    """
    error = data.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if code in (None, "", "ok"):
            return None
        return f"{code}: {error.get('message') or ''}".strip()
    if error:
        return str(data.get("error_description") or error)
    return None

