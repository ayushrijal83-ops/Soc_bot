"""Instagram OAuth authentication via Business Login for Instagram (Instagram API with Instagram Login).

Flow (verified against Meta docs, 2026-09):
    instagram.com/oauth/authorize            -> authorization code
    POST api.instagram.com/oauth/access_token -> short-lived token (1 hour) + user_id
    GET graph.instagram.com/access_token      -> long-lived token (grant_type=ig_exchange_token, ~60 days)
    GET graph.instagram.com/refresh_access_token (grant_type=ig_refresh_token)
        -> NEW long-lived token; allowed once the current token is >= 24h old and unexpired.

Instagram Login has no refresh token and no documented token-revocation endpoint.
"""

from typing import Any

import httpx

from src.auth.base import OAuthConfig, OAuthTokenResult, PlatformAuth
from src.auth.errors import OAuthAccountIdentityError, OAuthTokenExchangeError


class InstagramAuth(PlatformAuth):
    """Instagram OAuth via Business Login for Instagram (no Facebook Page required)."""

    PLATFORM = "instagram"
    SCOPE_SEPARATOR = ","
    # Instagram has no refresh token: the long-lived access token itself is re-exchanged.
    REFRESH_USES_ACCESS_TOKEN = True
    # Meta only redirects to URIs registered exactly in Business login settings, so a dynamic
    # loopback port can never work. INSTAGRAM_REDIRECT_URI must be set (see AuthManager).
    REQUIRES_REGISTERED_REDIRECT = True

    AUTHORIZATION_URL = "https://www.instagram.com/oauth/authorize"
    TOKEN_URL = "https://api.instagram.com/oauth/access_token"
    LONG_LIVED_TOKEN_URL = "https://graph.instagram.com/access_token"
    REFRESH_TOKEN_URL = "https://graph.instagram.com/refresh_access_token"
    USER_INFO_URL = "https://graph.instagram.com/v25.0/me"

    # instagram_business_basic: required for every Instagram Login flow (and for token refresh).
    # instagram_business_content_publish: required to publish (Phase 4).
    DEFAULT_SCOPES = (
        "instagram_business_basic",
        "instagram_business_content_publish",
    )

    async def exchange_code(self, code: str, pkce_verifier: str | None = None) -> OAuthTokenResult:
        """Exchange the code for a short-lived token, then for a long-lived token.

        ``pkce_verifier`` is accepted for interface compatibility; Instagram Login does not document PKCE.
        """
        # Meta appends "#_" to the redirect; it is not part of the code.
        code = code.removesuffix("#_")
        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "grant_type": "authorization_code",
            "redirect_uri": self.config.redirect_uri,
            "code": code,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.TOKEN_URL, data=data)

        if response.status_code != 200:
            raise OAuthTokenExchangeError(
                f"Token exchange failed (HTTP {response.status_code}): {_meta_error(response)}",
                platform=self.PLATFORM,
                status_code=response.status_code,
                response_body=_error_body(response),
            )

        body = response.json()
        # Docs show {"data": [{access_token, user_id, permissions}]}; accept the flat form too.
        short = body["data"][0] if isinstance(body.get("data"), list) and body["data"] else body
        short_token = short.get("access_token")
        if not short_token:
            raise OAuthTokenExchangeError("Token exchange returned no access_token", platform=self.PLATFORM)

        long_lived = await self._exchange_for_long_lived_token(short_token)

        return OAuthTokenResult(
            access_token=long_lived["access_token"],
            refresh_token=None,
            expires_in=long_lived.get("expires_in"),
            token_type=long_lived.get("token_type", "bearer"),
            scope=_permissions(short.get("permissions")),
            platform_account_id=str(short["user_id"]) if short.get("user_id") is not None else None,
            raw_response=None,  # never keep token-bearing bodies around
        )

    async def _exchange_for_long_lived_token(self, short_lived_token: str) -> dict[str, Any]:
        """Exchange a short-lived token for a long-lived one (grant_type=ig_exchange_token)."""
        params = {
            "grant_type": "ig_exchange_token",
            "client_secret": self.config.client_secret,
            "access_token": short_lived_token,
        }
        return await self._get_token(self.LONG_LIVED_TOKEN_URL, params, "Long-lived token exchange")

    async def refresh_tokens(self, refresh_token: str) -> OAuthTokenResult:
        """Re-exchange a long-lived ACCESS token for a new long-lived token (grant_type=ig_refresh_token).

        Instagram has no refresh token; pass the current long-lived access token. The returned
        token replaces the stored one. Meta only allows this once the token is >= 24h old and unexpired.
        """
        params = {"grant_type": "ig_refresh_token", "access_token": refresh_token}
        data = await self._get_token(self.REFRESH_TOKEN_URL, params, "Long-lived token refresh")
        return OAuthTokenResult(
            access_token=data["access_token"],
            refresh_token=None,
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "bearer"),
        )

    async def _get_token(self, url: str, params: dict[str, str], action: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
        if response.status_code != 200:
            raise OAuthTokenExchangeError(
                f"{action} failed (HTTP {response.status_code})",
                platform=self.PLATFORM,
                status_code=response.status_code,
                response_body=_error_body(response),
            )
        data = response.json()
        if not data.get("access_token"):
            raise OAuthTokenExchangeError(f"{action} returned no access_token", platform=self.PLATFORM)
        return data

    async def get_account_identity(self, access_token: str) -> dict[str, Any]:
        """Get the Instagram professional account ID (``user_id``) and username via /me."""
        params = {"fields": "user_id,username,name,profile_picture_url"}
        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient() as client:
            response = await client.get(self.USER_INFO_URL, params=params, headers=headers)

        if response.status_code != 200:
            raise OAuthAccountIdentityError(
                f"Failed to get account identity (HTTP {response.status_code})",
                platform=self.PLATFORM,
            )

        data = response.json()
        # user_id is the Instagram professional account ID; id is only app-scoped.
        user_id = data.get("user_id")
        if not user_id:
            raise OAuthAccountIdentityError("Instagram /me returned no user_id", platform=self.PLATFORM)

        return {
            "platform_account_id": str(user_id),
            "username": data.get("username"),
            "display_name": data.get("name") or data.get("username"),
            "profile_picture_url": data.get("profile_picture_url"),
        }

    async def revoke_tokens(self, access_token: str) -> bool:
        """Instagram Login documents no token-revocation endpoint.

        Returns False without making a request. The user removes access in Instagram
        settings (Apps and websites); Meta then calls the app's Deauthorize Callback URL.
        """
        return False

    @staticmethod
    def create_config(
        app_id: str,
        app_secret: str,
        redirect_uri: str | None,
        scopes: list | None = None,
    ) -> OAuthConfig:
        """Create OAuth configuration for Instagram (app_id/app_secret = the *Instagram* app ID/secret)."""
        return OAuthConfig(
            platform="instagram",
            client_id=app_id,
            client_secret=app_secret,
            redirect_uri=redirect_uri,
            scopes=list(scopes or InstagramAuth.DEFAULT_SCOPES),
            authorization_url=InstagramAuth.AUTHORIZATION_URL,
            token_url=InstagramAuth.TOKEN_URL,
            pkce_required=False,
            # Meta: force_reauth=true "forces an app user to use their Instagram professional account credentials
            # to log into your app even if the user is logged into Instagram". Without it the browser's current
            # Instagram session is reused, so Connect can only ever re-authorize that one account.
            additional_params={"force_reauth": "true"},
        )


def _meta_error(response: httpx.Response) -> str:
    """Meta's error message (no secrets in it), with a hint for the most common setup mistake."""
    try:
        body = response.json()
    except ValueError:
        return "no error message"
    message = body.get("error_message") or (body.get("error") or {}).get("message") if isinstance(body, dict) else None
    message = str(message or "no error message")[:200]
    if "invalid platform app" in message.lower():
        message += (" -- INSTAGRAM_APP_ID/INSTAGRAM_APP_SECRET must be the *Instagram* App ID and secret from "
                    "App Dashboard > Instagram > API setup with Instagram login, not the Meta App ID/secret")
    return message


def _error_body(response: httpx.Response) -> str:
    """Error body for diagnostics; error responses carry no tokens but keep it bounded."""
    return response.text[:500]


def _permissions(value: Any) -> str | None:
    """Granted permissions as a comma-separated string.

    Meta's docs show a comma-separated string, but responses can carry a JSON list.
    """
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return value if isinstance(value, str) else None

