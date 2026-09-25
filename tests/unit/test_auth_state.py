"""Tests for OAuth state management."""

import time

import pytest

from src.auth.errors import OAuthStateError
from src.auth.state import (
    OAuthState,
    OAuthStateStore,
    generate_pkce_challenge,
    generate_pkce_verifier,
)


class TestOAuthState:
    """Tests for OAuthState class."""

    def test_create_state(self):
        """Test creating a new OAuth state."""
        state = OAuthState(
            state="test_state_123",
            platform="instagram",
            pkce_verifier="test_verifier",
        )

        assert state.state == "test_state_123"
        assert state.platform == "instagram"
        assert state.pkce_verifier == "test_verifier"
        assert state.created_at > 0
        assert state.expires_at > state.created_at

    def test_is_expired_false(self):
        """Test is_expired returns False for valid state."""
        state = OAuthState(
            state="test",
            platform="instagram",
            expires_at=time.time() + 600,
        )
        assert state.is_expired() is False

    def test_is_expired_true(self):
        """Test is_expired returns True for expired state."""
        state = OAuthState(
            state="test",
            platform="instagram",
            expires_at=time.time() - 10,
        )
        assert state.is_expired() is True

    def test_is_valid(self):
        """Test is_valid returns True for valid state."""
        state = OAuthState(
            state="test",
            platform="instagram",
            expires_at=time.time() + 600,
        )
        assert state.is_valid() is True

    def test_is_valid_expired(self):
        """Test is_valid returns False for expired state."""
        state = OAuthState(
            state="test",
            platform="instagram",
            expires_at=time.time() - 10,
        )
        assert state.is_valid() is False


class TestOAuthStateStore:
    """Tests for OAuthStateStore class."""

    def test_create_state(self):
        """Test creating a new state."""
        store = OAuthStateStore()
        state = store.create_state("instagram", "test_verifier")

        assert state.platform == "instagram"
        assert state.pkce_verifier == "test_verifier"
        assert state.state in store._states

    def test_create_state_without_pkce(self):
        """Test creating state without PKCE verifier."""
        store = OAuthStateStore()
        state = store.create_state("tiktok")

        assert state.platform == "tiktok"
        assert state.pkce_verifier is None

    def test_get_state(self):
        """Test retrieving state by value."""
        store = OAuthStateStore()
        created = store.create_state("instagram", "verifier")

        retrieved = store.get_state(created.state)
        assert retrieved is not None
        assert retrieved.state == created.state

    def test_get_nonexistent_state(self):
        """Test retrieving nonexistent state returns None."""
        store = OAuthStateStore()
        result = store.get_state("nonexistent")
        assert result is None

    def test_consume_state(self):
        """Test consuming (getting and removing) a state."""
        store = OAuthStateStore()
        created = store.create_state("instagram", "verifier")

        consumed = store.consume_state(created.state)
        assert consumed.state == created.state
        assert consumed.pkce_verifier == "verifier"
        assert created.state not in store._states

    def test_consume_nonexistent_state(self):
        """Test consuming nonexistent state raises error."""
        store = OAuthStateStore()
        with pytest.raises(OAuthStateError):
            store.consume_state("nonexistent")

    def test_consume_expired_state(self):
        """Test consuming expired state raises error."""
        store = OAuthStateStore()
        created = store.create_state("instagram", "verifier")
        # Manually expire the state
        created.expires_at = time.time() - 10

        with pytest.raises(OAuthStateError, match="expired"):
            store.consume_state(created.state)

    def test_cleanup_expired(self):
        """Test cleaning up expired states."""
        store = OAuthStateStore()
        store.create_state("instagram", "verifier1")
        store.create_state("tiktok", "verifier2")

        # Expire one state
        states = list(store._states.values())
        states[0].expires_at = time.time() - 10

        removed = store.cleanup_expired()
        assert removed == 1
        assert len(store._states) == 1


class TestPKCE:
    """Tests for PKCE generation."""

    def test_generate_pkce_verifier(self):
        """Test PKCE verifier generation."""
        verifier = generate_pkce_verifier()
        assert isinstance(verifier, str)
        assert len(verifier) > 40  # URL-safe base64 of 32 bytes

    def test_generate_pkce_challenge(self):
        """Test PKCE challenge generation from verifier."""
        verifier = "test_verifier_123456789012345678901234"
        challenge = generate_pkce_challenge(verifier)

        assert isinstance(challenge, str)
        assert len(challenge) > 0
        # Challenge should be base64url encoded SHA256
        import base64
        import hashlib
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        assert challenge == expected

    def test_pkce_verifier_uniqueness(self):
        """Test that generated verifiers are unique."""
        verifiers = {generate_pkce_verifier() for _ in range(100)}
        assert len(verifiers) == 100

class TestPlatformPKCEAndBinding:
    def test_hex_challenge_for_tiktok(self):
        import hashlib

        verifier = generate_pkce_verifier()
        assert generate_pkce_challenge(verifier, "hex") == hashlib.sha256(verifier.encode()).hexdigest()

    def test_default_challenge_is_base64url(self):
        verifier = generate_pkce_verifier()
        assert generate_pkce_challenge(verifier) == generate_pkce_challenge(verifier, "base64url")
        assert "=" not in generate_pkce_challenge(verifier)

    def test_unknown_encoding_rejected(self):
        with pytest.raises(ValueError):
            generate_pkce_challenge("v" * 43, "plain")

    def test_verifier_length_within_rfc7636_bounds(self):
        assert 43 <= len(generate_pkce_verifier()) <= 128

    def test_consume_with_matching_platform(self):
        store = OAuthStateStore()
        st = store.create_state("youtube", "v")
        assert store.consume_state(st.state, platform="youtube").pkce_verifier == "v"

    def test_consume_with_mismatched_platform_rejected_and_consumed(self):
        store = OAuthStateStore()
        st = store.create_state("instagram", "v")
        with pytest.raises(OAuthStateError, match="different platform"):
            store.consume_state(st.state, platform="tiktok")
        with pytest.raises(OAuthStateError):
            store.consume_state(st.state, platform="instagram")

    def test_store_ttl_is_applied(self):
        store = OAuthStateStore(default_ttl=5)
        st = store.create_state("tiktok")
        assert st.expires_at - st.created_at < 6
