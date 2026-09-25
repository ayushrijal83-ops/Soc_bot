"""OAuth authentication package."""

from src.auth.base import OAuthConfig, OAuthTokenResult, PlatformAuth
from src.auth.callback_server import OAuthCallbackServer
from src.auth.errors import (
    OAuthAccountIdentityError,
    OAuthCallbackError,
    OAuthCallbackTimeoutError,
    OAuthConfigurationError,
    OAuthError,
    OAuthScopeError,
    OAuthStateError,
    OAuthTokenExchangeError,
)
from src.auth.manager import AuthManager, create_auth_manager
from src.auth.state import OAuthState, OAuthStateStore

__all__ = [
    "AuthManager",
    "OAuthAccountIdentityError",
    "OAuthCallbackError",
    "OAuthCallbackServer",
    "OAuthCallbackTimeoutError",
    "OAuthConfig",
    "OAuthConfigurationError",
    "OAuthError",
    "OAuthScopeError",
    "OAuthState",
    "OAuthStateError",
    "OAuthStateStore",
    "OAuthTokenExchangeError",
    "OAuthTokenResult",
    "PlatformAuth",
    "create_auth_manager",
]