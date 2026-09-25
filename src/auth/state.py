"""OAuth state management for CSRF protection."""

import secrets
import time
from dataclasses import dataclass, field

from src.auth.errors import OAuthStateError


@dataclass
class OAuthState:
    """OAuth authorization state for CSRF protection."""

    state: str
    platform: str
    pkce_verifier: str | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 600)  # 10 minutes

    def is_expired(self) -> bool:
        """Check if state has expired."""
        return time.time() > self.expires_at

    def is_valid(self) -> bool:
        """Check if state is valid (not expired)."""
        return not self.is_expired()


class OAuthStateStore:
    """In-memory store for OAuth authorization states."""

    def __init__(self, default_ttl: int = 600):
        self._states: dict[str, OAuthState] = {}
        self.default_ttl = default_ttl

    def create_state(self, platform: str, pkce_verifier: str | None = None) -> OAuthState:
        """Create a new OAuth state."""
        state = OAuthState(
            state=self._generate_state(),
            platform=platform,
            pkce_verifier=pkce_verifier,
        )
        self._states[state.state] = state
        return state

    def get_state(self, state: str) -> OAuthState | None:
        """Get state by value."""
        return self._states.get(state)

    def consume_state(self, state: str) -> OAuthState:
        """Get and remove state (single-use)."""
        oauth_state = self._states.pop(state, None)
        if oauth_state is None:
            raise OAuthStateError("Invalid or expired state", state=state)
        if oauth_state.is_expired():
            raise OAuthStateError("State has expired", state=state)
        return oauth_state

    def cleanup_expired(self) -> int:
        """Remove expired states. Returns count of removed states."""
        expired = [s for s, state in self._states.items() if state.is_expired()]
        for s in expired:
            del self._states[s]
        return len(expired)

    def _generate_state(self) -> str:
        """Generate a cryptographically secure random state."""
        return secrets.token_urlsafe(32)


def generate_pkce_verifier() -> str:
    """Generate a PKCE code verifier (43-128 characters)."""
    return secrets.token_urlsafe(32)


def generate_pkce_challenge(verifier: str) -> str:
    """Generate PKCE code challenge from verifier using S256."""
    import hashlib

    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return challenge


import base64