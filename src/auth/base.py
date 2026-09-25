"""Base classes for OAuth authentication."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from src.accounts.manager import AccountManager
from src.auth.errors import (
    OAuthConfigurationError,
)
from src.auth.state import (
    OAuthStateStore,
    generate_pkce_challenge,
    generate_pkce_verifier,
)
from src.storage.tokens import TokenEncryption


@dataclass
class OAuthConfig:
    """OAuth configuration for a platform."""

    platform: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: list[str]
    authorization_url: str
    token_url: str
    pkce_required: bool = True
    additional_params: dict[str, str] = None

    def __post_init__(self):
        if self.additional_params is None:
            self.additional_params = {}


@dataclass
class OAuthTokenResult:
    """Result of successful OAuth token exchange."""

    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    token_type: str = "Bearer"
    scope: str | None = None
    platform_account_id: str | None = None
    raw_response: dict[str, Any] = None


class PlatformAuth(ABC):
    """Abstract base class for platform-specific OAuth implementations."""

    def __init__(
        self,
        config: OAuthConfig,
        state_store: "OAuthStateStore",
        token_encryption: TokenEncryption,
        account_manager: AccountManager,
    ):
        self.config = config
        self.state_store = config.state_store if hasattr(config, 'state_store') else None
        self._state_store = state_store
        self.token_encryption = token_encryption
        self.account_manager = account_manager

    @property
    def platform(self) -> str:
        return self.config.platform

    def _get_state_store(self) -> "OAuthStateStore":
        return self._state_store

    def generate_pkce_pair(self) -> tuple[str, str]:
        """Generate PKCE verifier and challenge pair."""
        verifier = generate_pkce_verifier()
        challenge = generate_pkce_challenge(verifier)
        return verifier, challenge

    def get_authorization_url(self, state: str, pkce_challenge: str | None = None) -> str:
        """Generate the authorization URL for the platform."""
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

        # Add platform-specific additional parameters
        if self.config.additional_params:
            params.update(self.config.additional_params)

        return f"{self.config.authorization_url}?{urlencode(params)}"

    @abstractmethod
    async def exchange_code(self, code: str, pkce_verifier: str) -> OAuthTokenResult:
        """Exchange authorization code for tokens."""

    @abstractmethod
    async def get_account_identity(self, access_token: str) -> dict[str, Any]:
        """Get platform account identity from access token."""

    @abstractmethod
    async def refresh_tokens(self, refresh_token: str) -> OAuthTokenResult:
        """Refresh access token using refresh token."""

    @abstractmethod
    async def revoke_tokens(self, access_token: str) -> bool:
        """Revoke tokens on platform (optional)."""

    def create_account_from_oauth(
        self,
        platform_account_id: str,
        username: str,
        display_name: str | None,
        token_result: OAuthTokenResult,
    ) -> "Account":
        """Create or update account from OAuth result."""
        return self.account_manager.create_account(
            platform=self.config.platform,
            platform_account_id=platform_account_id,
            username=username,
            access_token=token_result.access_token,
            refresh_token=token_result.refresh_token,
            expires_in=token_result.expires_in,
            display_name=display_name,
            status="active",
        )

    def validate_configuration(self) -> None:
        """Validate OAuth configuration."""
        if not self.config.client_id:
            raise OAuthConfigurationError(f"Missing client_id for {self.config.platform}", platform=self.config.platform)
        if not self.config.client_secret:
            raise OAuthConfigurationError(f"Missing client_secret for {self.config.platform}", platform=self.config.platform)
        if not self.config.redirect_uri:
            raise OAuthConfigurationError(f"Missing redirect_uri for {self.config.platform}", platform=self.config.platform)
        if not self.config.scopes:
            raise OAuthConfigurationError(f"Missing scopes for {self.config.platform}", platform=self.config.platform)