"""Tests for platform auth adapters."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

from src.platforms.instagram.auth import InstagramAuth
from src.platforms.tiktok.auth import TikTokAuth
from src.platforms.youtube.auth import YouTubeAuth
from src.auth.base import OAuthConfig, OAuthTokenResult
from src.auth.errors import (
    OAuthConfigurationError,
    OAuthTokenExchangeError,
    OAuthAccountIdentityError,
)


class TestInstagramAuth:
    """Tests for InstagramAuth class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = OAuthConfig(
            platform="instagram",
            client_id="test_app_id",
            client_secret="test_app_secret",
            redirect_uri="http://localhost:8080/callback/instagram",
            scopes=[
                "instagram_graph_user_profile",
                "instagram_graph_user_media",
                "pages_show_list",
                "pages_read_engagement",
            ],
            authorization_url="https://www.facebook.com/v22.0/dialog/oauth",
            token_url="https://graph.facebook.com/v22.0/oauth/access_token",
            pkce_required=True,
        )

        self.mock_state_store = MagicMock()
        self.mock_encryption = MagicMock()
        self.mock_account_manager = MagicMock()

    def test_create_config(self):
        """Test creating Instagram OAuth config."""
        config = InstagramAuth.create_config(
            app_id="test_app_id",
            app_secret="test_app_secret",
            redirect_uri="http://localhost:8080/callback/instagram",
        )

        assert config.platform == "instagram"
        assert config.client_id == "test_app_id"
        assert config.client_secret == "test_app_secret"
        assert config.redirect_uri == "http://localhost:8080/callback/instagram"
        assert config.authorization_url == "https://www.facebook.com/v22.0/dialog/oauth"
        assert config.token_url == "https://graph.facebook.com/v22.0/oauth/access_token"
        assert config.pkce_required is True

    def test_validate_configuration_valid(self):
        """Test validating valid configuration."""
        auth = InstagramAuth(
            config=self.config,
            state_store=MagicMock(),
            token_encryption=MagicMock(),
            account_manager=MagicMock(),
        )
        auth.validate_configuration()  # Should not raise

    def test_validate_configuration_missing_client_id(self):
        """Test validation fails with missing client_id."""
        config = OAuthConfig(
            platform="instagram",
            client_id="",
            client_secret="secret",
            redirect_uri="http://localhost",
            scopes=["test"],
            authorization_url="https://example.com/auth",
            token_url="https://example.com/token",
        )
        auth = InstagramAuth(config, MagicMock(), MagicMock(), MagicMock())
        with pytest.raises(Exception):  # OAuthConfigurationError
            auth.validate_configuration()

    def test_generate_pkce_pair(self):
        """Test PKCE pair generation."""
        auth = InstagramAuth(
            config=self.config,
            state_store=MagicMock(),
            token_encryption=MagicMock(),
            account_manager=MagicMock(),
        )
        verifier, challenge = auth.generate_pkce_pair()

        assert isinstance(verifier, str)
        assert len(verifier) > 40
        assert isinstance(challenge, str)
        assert len(challenge) > 0

    def test_get_authorization_url(self):
        """Test generating authorization URL."""
        auth = InstagramAuth(
            config=self.config,
            state_store=MagicMock(),
            token_encryption=MagicMock(),
            account_manager=MagicMock(),
        )

        pkce_verifier, pkce_challenge = auth.generate_pkce_pair()
        url = auth.get_authorization_url("test_state", pkce_challenge)

        assert "https://www.facebook.com/v22.0/dialog/oauth" in url
        assert "client_id=test_app_id" in url
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Fcallback%2Finstagram" in url
        assert "response_type=code" in url
        assert "scope=instagram_graph_user_profile+instagram_graph_user_media+pages_show_list+pages_read_engagement" in url
        assert "state=test_state" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url


class TestTikTokAuth:
    """Tests for TikTokAuth class."""

    def setup_method(self):
        self.config = OAuthConfig(
            platform="tiktok",
            client_id="test_client_key",
            client_secret="test_client_secret",
            redirect_uri="http://localhost:8080/callback/tiktok",
            scopes=["video.upload", "video.publish", "user.info.basic"],
            authorization_url="https://www.tiktok.com/v2/auth/authorize/",
            token_url="https://open.tiktokapis.com/v2/oauth/token/",
            pkce_required=True,
        )

    def test_create_config(self):
        """Test creating TikTok OAuth config."""
        config = TikTokAuth.create_config(
            client_key="test_client_key",
            client_secret="test_client_secret",
            redirect_uri="http://localhost:8080/callback/tiktok",
        )

        assert config.platform == "tiktok"
        assert config.client_id == "test_client_key"
        assert config.client_secret == "test_client_secret"
        assert config.redirect_uri == "http://localhost:8080/callback/tiktok"
        assert config.authorization_url == "https://www.tiktok.com/v2/auth/authorize/"
        assert config.token_url == "https://open.tiktokapis.com/v2/oauth/token/"
        assert config.pkce_required is True

    def test_get_authorization_url(self):
        """Test generating authorization URL."""
        auth = TikTokAuth(
            config=self.config,
            state_store=MagicMock(),
            token_encryption=MagicMock(),
            account_manager=MagicMock(),
        )

        pkce_verifier, pkce_challenge = auth.generate_pkce_pair()
        url = auth.get_authorization_url("test_state", pkce_challenge)

        assert "https://www.tiktok.com/v2/auth/authorize/" in url
        assert "client_key=test_client_key" in url
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Fcallback%2Ftiktok" in url
        assert "response_type=code" in url
        assert "scope=video.upload+video.publish+user.info.basic" in url
        assert "state=test_state" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url


class TestYouTubeAuth:
    """Tests for YouTubeAuth class."""

    def setup_method(self):
        self.config = OAuthConfig(
            platform="youtube",
            client_id="test_client_id",
            client_secret="test_client_secret",
            redirect_uri="http://localhost:8080/callback/youtube",
            scopes=[
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.readonly",
            ],
            authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            pkce_required=True,
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
            },
        )

    def test_create_config(self):
        """Test creating YouTube OAuth config."""
        config = YouTubeAuth.create_config(
            client_id="test_client_id",
            client_secret="test_client_secret",
            redirect_uri="http://localhost:8080/callback/youtube",
        )

        assert config.platform == "youtube"
        assert config.client_id == "test_client_id"
        assert config.client_secret == "test_client_secret"
        assert config.redirect_uri == "http://localhost:8080/callback/youtube"
        assert config.authorization_url == "https://accounts.google.com/o/oauth2/v2/auth"
        assert config.token_url == "https://oauth2.googleapis.com/token"
        assert config.pkce_required is True
        assert config.additional_params["access_type"] == "offline"
        assert config.additional_params["prompt"] == "consent"

    def test_get_authorization_url(self):
        """Test generating authorization URL."""
        auth = YouTubeAuth(
            config=self.config,
            state_store=MagicMock(),
            token_encryption=MagicMock(),
            account_manager=MagicMock(),
        )

        pkce_verifier, pkce_challenge = auth.generate_pkce_pair()
        url = auth.get_authorization_url("test_state", pkce_challenge)

        assert "https://accounts.google.com/o/oauth2/v2/auth" in url
        assert "client_id=test_client_id" in url
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Fcallback%2Fyoutube" in url
        assert "response_type=code" in url
        assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube.upload+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube.readonly" in url
        assert "state=test_state" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert "access_type=offline" in url
        assert "prompt=consent" in url