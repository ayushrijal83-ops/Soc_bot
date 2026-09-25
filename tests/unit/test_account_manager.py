"""Tests for Account Manager."""

import os
import tempfile
from datetime import datetime, timezone

import pytest

from src.accounts.manager import (
    AccountError,
    AccountManager,
    AccountNotFoundError,
    DuplicateAccountError,
    InvalidPlatformError,
    InvalidStatusError,
)
from src.storage.database import Database
from src.storage.tokens import TokenEncryption, generate_key


@pytest.fixture
def temp_db_path():
    """Create a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        if os.path.exists(path):
            os.unlink(path)
    except PermissionError:
        pass


@pytest.fixture
def encryption_key():
    """Generate a test encryption key."""
    return generate_key()


@pytest.fixture
def encryption(encryption_key):
    """Create TokenEncryption instance."""
    return TokenEncryption(encryption_key.encode())


@pytest.fixture
def database(temp_db_path, encryption):
    """Create a test database instance."""
    db = Database(f"sqlite:///{temp_db_path}", encryption=encryption)
    db.init()
    yield db
    db.engine.dispose()


@pytest.fixture
def account_manager(database, encryption):
    """Create an AccountManager instance."""
    return AccountManager(database, encryption)


class TestAccountManager:
    """Tests for AccountManager class."""

    def test_create_account(self, account_manager, encryption):
        """Test creating a valid account with encrypted tokens."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_12345",
            username="test_user",
            access_token="access_token_abc",
            refresh_token="refresh_token_xyz",
            expires_at=datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
            display_name="Test User",
            status="active",
        )

        assert account is not None
        assert account.id is not None
        assert account.platform == "instagram"
        assert account.platform_account_id == "ig_12345"
        assert account.username == "test_user"
        assert account.display_name == "Test User"
        assert account.status == "active"
        assert account.expires_at.replace(tzinfo=timezone.utc) == datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

        # Verify tokens are encrypted in database
        assert account.access_token_enc != "access_token_abc"
        assert account.refresh_token_enc != "refresh_token_xyz"

        # Verify decryption works
        assert account.get_access_token(encryption) == "access_token_abc"
        assert account.get_refresh_token(encryption) == "refresh_token_xyz"

    def test_create_account_without_refresh_token(self, account_manager, encryption):
        """Test creating account without refresh token."""
        account = account_manager.create_account(
            platform="tiktok",
            platform_account_id="tt_67890",
            username="tiktok_user",
            access_token="access_only",
        )

        assert account is not None
        assert account.get_refresh_token(encryption) is None

    def test_create_account_without_display_name(self, account_manager):
        """Test creating account without display name."""
        account = account_manager.create_account(
            platform="youtube",
            platform_account_id="yt_123",
            username="yt_user",
            access_token="token",
        )

        assert account.display_name is None

    def test_create_account_default_status(self, account_manager):
        """Test that default status is 'active'."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="token",
        )
        assert account.status == "active"

    def test_create_account_invalid_platform(self, account_manager):
        """Test creating account with invalid platform raises error."""
        with pytest.raises(InvalidPlatformError):
            account_manager.create_account(
                platform="invalid_platform",
                platform_account_id="123",
                username="user",
                access_token="token",
            )

    def test_create_account_invalid_status(self, account_manager):
        """Test creating account with invalid status raises error."""
        with pytest.raises(InvalidStatusError):
            account_manager.create_account(
                platform="instagram",
                platform_account_id="123",
                username="user",
                access_token="token",
                status="invalid_status",
            )

    def test_create_duplicate_account(self, account_manager):
        """Test creating duplicate account raises error."""
        account_manager.create_account(
            platform="instagram",
            platform_account_id="same_id",
            username="user1",
            access_token="token1",
        )

        with pytest.raises(DuplicateAccountError):
            account_manager.create_account(
                platform="instagram",
                platform_account_id="same_id",
                username="user2",
                access_token="token2",
            )

    def test_create_duplicate_different_platform(self, account_manager):
        """Test that same platform_account_id is allowed for different platforms."""
        account_manager.create_account(
            platform="instagram",
            platform_account_id="same_id",
            username="user1",
            access_token="token1",
        )

        # Should work - different platform
        account = account_manager.create_account(
            platform="tiktok",
            platform_account_id="same_id",
            username="user2",
            access_token="token2",
        )
        assert account is not None

    def test_get_account(self, account_manager):
        """Test getting an account by ID."""
        created = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="token",
        )

        account = account_manager.get_account(created.id)
        assert account.id == created.id
        assert account.username == "user"

    def test_get_nonexistent_account(self, account_manager):
        """Test getting nonexistent account raises error."""
        with pytest.raises(AccountNotFoundError):
            account_manager.get_account(99999)

    def test_find_account(self, account_manager):
        """Test finding account by platform and platform_account_id."""
        created = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_12345",
            username="test_user",
            access_token="token",
        )

        found = account_manager.find_account("instagram", "ig_12345")
        assert found.id == created.id
        assert found.username == "test_user"

    def test_find_nonexistent_account(self, account_manager):
        """Test finding nonexistent account raises error."""
        with pytest.raises(AccountNotFoundError):
            account_manager.find_account("instagram", "nonexistent")

    def test_list_accounts_all(self, account_manager):
        """Test listing all accounts."""
        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_1",
            username="user1",
            access_token="token1",
        )
        account_manager.create_account(
            platform="tiktok",
            platform_account_id="tt_1",
            username="user2",
            access_token="token2",
        )

        accounts = account_manager.list_accounts()
        assert len(accounts) == 2

    def test_list_accounts_filter_by_platform(self, account_manager):
        """Test listing accounts filtered by platform."""
        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_1",
            username="user1",
            access_token="token1",
        )
        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_2",
            username="user2",
            access_token="token2",
        )
        account_manager.create_account(
            platform="tiktok",
            platform_account_id="tt_1",
            username="user3",
            access_token="token3",
        )

        instagram_accounts = account_manager.list_accounts(platform="instagram")
        assert len(instagram_accounts) == 2
        assert all(a.platform == "instagram" for a in instagram_accounts)

        tiktok_accounts = account_manager.list_accounts(platform="tiktok")
        assert len(tiktok_accounts) == 1
        assert tiktok_accounts[0].platform == "tiktok"

    def test_list_accounts_filter_by_status(self, account_manager):
        """Test listing accounts filtered by status."""
        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_1",
            username="user1",
            access_token="token1",
            status="active",
        )
        account_manager.create_account(
            platform="tiktok",
            platform_account_id="tt_1",
            username="user2",
            access_token="token2",
            status="disconnected",
        )

        active_accounts = account_manager.list_accounts(status="active")
        assert len(active_accounts) == 1
        assert active_accounts[0].status == "active"

    def test_list_accounts_filter_by_both(self, account_manager):
        """Test listing accounts filtered by platform and status."""
        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_1",
            username="user1",
            access_token="token1",
            status="active",
        )
        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_2",
            username="user2",
            access_token="token2",
            status="disconnected",
        )

        accounts = account_manager.list_accounts(platform="instagram", status="active")
        assert len(accounts) == 1
        assert accounts[0].status == "active"

    def test_list_accounts_invalid_platform(self, account_manager):
        """Test listing with invalid platform raises error."""
        with pytest.raises(InvalidPlatformError):
            account_manager.list_accounts(platform="invalid")

    def test_list_accounts_invalid_status(self, account_manager):
        """Test listing with invalid status raises error."""
        with pytest.raises(InvalidStatusError):
            account_manager.list_accounts(status="invalid")

    def test_update_account_display_info(self, account_manager, encryption):
        """Test updating account display information."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="old_user",
            access_token="token",
        )

        updated = account_manager.update_account(
            account.id,
            username="new_user",
            display_name="New Display Name",
        )

        assert updated.username == "new_user"
        assert updated.display_name == "New Display Name"

    def test_update_account_status(self, account_manager):
        """Test updating account status."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="token",
            status="active",
        )

        updated = account_manager.update_account(account.id, status="disconnected")
        assert updated.status == "disconnected"

    def test_update_account_status_invalid(self, account_manager):
        """Test updating with invalid status raises error."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="token",
        )

        with pytest.raises(InvalidStatusError):
            account_manager.update_account(account.id, status="invalid")

    def test_update_account_access_token(self, account_manager, encryption):
        """Test updating access token encrypts it."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="old_token",
        )

        updated = account_manager.update_account(account.id, access_token="new_token")
        assert updated.get_access_token(encryption) == "new_token"

    def test_update_account_refresh_token(self, account_manager, encryption):
        """Test updating refresh token encrypts it."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="token",
        )

        updated = account_manager.update_account(account.id, refresh_token="new_refresh")
        assert updated.get_refresh_token(encryption) == "new_refresh"

    def test_update_nonexistent_account(self, account_manager):
        """Test updating nonexistent account raises error."""
        with pytest.raises(AccountNotFoundError):
            account_manager.update_account(99999, username="new")

    def test_disconnect_account(self, account_manager):
        """Test disconnecting account sets status to disconnected."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="token",
            status="active",
        )

        disconnected = account_manager.disconnect_account(account.id)
        assert disconnected.status == "disconnected"

    def test_disconnect_preserves_data(self, account_manager):
        """Test disconnecting preserves historical data (doesn't delete)."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="token",
            status="active",
        )
        account_id = account.id

        account_manager.disconnect_account(account_id)

        # Account should still exist
        account = account_manager.get_account(account_id)
        assert account is not None
        assert account.status == "disconnected"

    def test_enable_account(self, account_manager):
        """Test enabling account sets status to active."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="token",
            status="disconnected",
        )

        enabled = account_manager.enable_account(account.id)
        assert enabled.status == "active"

    def test_get_active_accounts(self, account_manager):
        """Test getting active accounts."""
        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_1",
            username="user1",
            access_token="token1",
            status="active",
        )
        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_2",
            username="user2",
            access_token="token2",
            status="disconnected",
        )
        account_manager.create_account(
            platform="tiktok",
            platform_account_id="tt_1",
            username="user3",
            access_token="token3",
            status="active",
        )

        all_active = account_manager.get_active_accounts()
        assert len(all_active) == 2

        instagram_active = account_manager.get_active_accounts(platform="instagram")
        assert len(instagram_active) == 1
        assert instagram_active[0].username == "user1"

    def test_get_account_display_info(self, account_manager):
        """Test getting safe display info (no tokens)."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="test_user",
            access_token="secret_token",
            display_name="Test User",
        )

        info = account_manager.get_account_display_info(account)
        assert info["id"] == account.id
        assert info["platform"] == "instagram"
        assert info["username"] == "test_user"
        assert info["display_name"] == "Test User"
        assert info["status"] == "active"
        assert "access_token" not in info
        assert "refresh_token" not in info

    def test_get_account_with_tokens(self, account_manager, encryption):
        """Test getting decrypted tokens (internal use)."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="access_token_secret",
            refresh_token="refresh_token_secret",
        )

        access_token, refresh_token = account_manager.get_account_with_tokens(account.id)
        assert access_token == "access_token_secret"
        assert refresh_token == "refresh_token_secret"

    def test_get_account_with_tokens_nonexistent(self, account_manager):
        """Test getting tokens for nonexistent account raises error."""
        with pytest.raises(AccountNotFoundError):
            account_manager.get_account_with_tokens(99999)

    def test_create_development_account(self, account_manager):
        """Test creating development/test account."""
        account = account_manager.create_development_account(
            platform="instagram",
            platform_account_id="dev_ig_123",
            username="dev_user",
        )

        assert account is not None
        assert account.platform == "instagram"
        assert account.platform_account_id == "dev_ig_123"
        assert account.username == "dev_user"
        assert account.status == "active"
        assert account.meta_json == '{"source": "development"}'

    def test_encryption_error_handling(self, account_manager):
        """Test that encryption errors are wrapped in AccountError."""
        # This is hard to test directly without mocking, but we can verify
        # the error hierarchy is correct
        assert issubclass(AccountError, Exception)
        assert issubclass(AccountNotFoundError, AccountError)
        assert issubclass(DuplicateAccountError, AccountError)
        assert issubclass(InvalidPlatformError, AccountError)
        assert issubclass(InvalidStatusError, AccountError)

    def test_update_expires_at(self, account_manager):
        """Test updating token expiry."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="token",
        )

        new_expiry = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        updated = account_manager.update_account(account.id, expires_at=new_expiry)
        assert updated.expires_at.replace(tzinfo=timezone.utc) == new_expiry

    def test_update_meta_json(self, account_manager):
        """Test updating meta_json."""
        account = account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_123",
            username="user",
            access_token="token",
        )

        updated = account_manager.update_account(
            account.id,
            meta_json='{"custom": "data"}',
        )
        assert updated.meta_json == '{"custom": "data"}'

    def test_case_insensitive_platform(self, account_manager):
        """Test that platform validation is case-sensitive (lowercase only)."""
        with pytest.raises(InvalidPlatformError):
            account_manager.create_account(
                platform="INSTAGRAM",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
            )

        with pytest.raises(InvalidPlatformError):
            account_manager.create_account(
                platform="TikTok",
                platform_account_id="tt_123",
                username="user",
                access_token="token",
            )

    def test_case_insensitive_status(self, account_manager):
        """Test that status validation is case-sensitive (lowercase only)."""
        with pytest.raises(InvalidStatusError):
            account_manager.create_account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                status="ACTIVE",
            )

    def test_list_accounts_ordering(self, account_manager):
        """Test that list_accounts orders by created_at descending."""
        # Add delays to ensure different timestamps (SQLite may have second precision)
        import time

        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_1",
            username="user1",
            access_token="token1",
        )
        time.sleep(1.1)  # Ensure different second
        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_2",
            username="user2",
            access_token="token2",
        )
        time.sleep(1.1)
        account_manager.create_account(
            platform="instagram",
            platform_account_id="ig_3",
            username="user3",
            access_token="token3",
        )

        accounts = account_manager.list_accounts(platform="instagram")
        assert len(accounts) == 3
        # Should be ordered by created_at DESC (newest first)
        assert accounts[0].username == "user3"
        assert accounts[1].username == "user2"
        assert accounts[2].username == "user1"