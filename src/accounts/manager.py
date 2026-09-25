"""Account Manager for managing connected social media accounts."""

from contextlib import contextmanager
from datetime import datetime

from src.storage.database import Account, Database
from src.storage.tokens import TokenEncryption, TokenEncryptionError


class AccountError(Exception):
    """Base exception for account management errors."""


class AccountNotFoundError(AccountError):
    """Raised when an account is not found."""


class DuplicateAccountError(AccountError):
    """Raised when attempting to create a duplicate account."""


class InvalidPlatformError(AccountError):
    """Raised when an invalid platform is specified."""


class InvalidStatusError(AccountError):
    """Raised when an invalid status is specified."""


class AccountManager:
    """Manages connected social media accounts."""

    VALID_PLATFORMS = ("instagram", "tiktok", "youtube")
    VALID_STATUSES = ("active", "expired", "revoked", "disconnected")

    def __init__(self, database: Database, encryption: TokenEncryption):
        """
        Initialize Account Manager.

        Args:
            database: Database instance for persistence.
            encryption: TokenEncryption instance for token encryption/decryption.
        """
        self._database = database
        self._encryption = encryption

    @contextmanager
    def _session(self):
        """Get a database session."""
        with self._database.session() as session:
            yield session

    def _validate_platform(self, platform: str) -> None:
        """Validate platform is supported."""
        if platform not in self.VALID_PLATFORMS:
            raise InvalidPlatformError(
                f"Invalid platform: {platform}. Must be one of: {', '.join(self.VALID_PLATFORMS)}"
            )

    def _validate_status(self, status: str) -> None:
        """Validate status is supported."""
        if status not in self.VALID_STATUSES:
            raise InvalidStatusError(
                f"Invalid status: {status}. Must be one of: {', '.join(self.VALID_STATUSES)}"
            )

    def _check_duplicate(self, session, platform: str, platform_account_id: str, exclude_id: int | None = None) -> None:
        """Check for duplicate platform + platform_account_id combination."""
        query = session.query(Account).filter(
            Account.platform == platform,
            Account.platform_account_id == platform_account_id
        )
        if exclude_id is not None:
            query = query.filter(Account.id != exclude_id)
        if query.first():
            raise DuplicateAccountError(
                f"Account with platform '{platform}' and platform_account_id '{platform_account_id}' already exists"
            )

    def create_account(
        self,
        platform: str,
        platform_account_id: str,
        username: str,
        access_token: str,
        refresh_token: str | None = None,
        expires_at: datetime | None = None,
        display_name: str | None = None,
        status: str = "active",
        meta_json: str | None = None,
    ) -> Account:
        """
        Create a new connected account with encrypted tokens.

        Args:
            platform: Platform identifier (instagram, tiktok, youtube).
            platform_account_id: Platform's user/channel ID.
            username: Display handle (@username or channel name).
            access_token: OAuth access token (will be encrypted).
            refresh_token: OAuth refresh token (will be encrypted, optional).
            expires_at: Access token expiry timestamp (optional).
            display_name: Full name / channel title (optional).
            status: Account status (default: active).
            meta_json: JSON for platform-specific extra data (optional).

        Returns:
            Created Account instance.

        Raises:
            InvalidPlatformError: If platform is not supported.
            InvalidStatusError: If status is not supported.
            DuplicateAccountError: If platform + platform_account_id already exists.
            AccountError: If encryption fails.
        """
        self._validate_platform(platform)
        self._validate_status(status)

        with self._session() as session:
            self._check_duplicate(session, platform, platform_account_id)

            try:
                account = Account(
                    platform=platform,
                    platform_account_id=platform_account_id,
                    username=username,
                    display_name=display_name,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=expires_at,
                    status=status,
                    meta_json=meta_json,
                    _encryption=self._encryption,
                )
                session.add(account)
                session.flush()
                session.commit()
                return account
            except TokenEncryptionError as e:
                raise AccountError(f"Token encryption failed: {e}") from e

    def get_account(self, account_id: int) -> Account:
        """
        Get an account by internal ID.

        Args:
            account_id: Internal account ID.

        Returns:
            Account instance.

        Raises:
            AccountNotFoundError: If account does not exist.
        """
        with self._session() as session:
            account = session.query(Account).filter(Account.id == account_id).first()
            if not account:
                raise AccountNotFoundError(f"Account with ID {account_id} not found")
            return account

    def find_account(self, platform: str, platform_account_id: str) -> Account:
        """
        Find an account by platform and platform account ID.

        Args:
            platform: Platform identifier.
            platform_account_id: Platform's user/channel ID.

        Returns:
            Account instance.

        Raises:
            AccountNotFoundError: If account does not exist.
        """
        self._validate_platform(platform)
        with self._session() as session:
            account = session.query(Account).filter(
                Account.platform == platform,
                Account.platform_account_id == platform_account_id
            ).first()
            if not account:
                raise AccountNotFoundError(
                    f"Account with platform '{platform}' and platform_account_id '{platform_account_id}' not found"
                )
            return account

    def list_accounts(
        self,
        platform: str | None = None,
        status: str | None = None,
    ) -> list[Account]:
        """
        List accounts with optional filtering.

        Args:
            platform: Filter by platform (instagram, tiktok, youtube).
            status: Filter by status (active, expired, revoked, disconnected).

        Returns:
            List of Account instances.
        """
        if platform:
            self._validate_platform(platform)
        if status:
            self._validate_status(status)

        with self._session() as session:
            query = session.query(Account)
            if platform:
                query = query.filter(Account.platform == platform)
            if status:
                query = query.filter(Account.status == status)
            return query.order_by(Account.created_at.desc()).all()

    def update_account(
        self,
        account_id: int,
        username: str | None = None,
        display_name: str | None = None,
        status: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expires_at: datetime | None = None,
        meta_json: str | None = None,
    ) -> Account:
        """
        Update an existing account.

        Args:
            account_id: Internal account ID.
            username: New display handle (optional).
            display_name: New full name / channel title (optional).
            status: New status (optional).
            access_token: New access token (will be encrypted, optional).
            refresh_token: New refresh token (will be encrypted, optional).
            expires_at: New token expiry (optional).
            meta_json: New platform-specific metadata (optional).

        Returns:
            Updated Account instance.

        Raises:
            AccountNotFoundError: If account does not exist.
            InvalidStatusError: If status is not supported.
            AccountError: If encryption fails.
        """
        if status:
            self._validate_status(status)

        with self._session() as session:
            account = session.query(Account).filter(Account.id == account_id).first()
            if not account:
                raise AccountNotFoundError(f"Account with ID {account_id} not found")

            if username is not None:
                account.username = username
            if display_name is not None:
                account.display_name = display_name
            if status is not None:
                account.status = status
            if expires_at is not None:
                account.expires_at = expires_at
            if meta_json is not None:
                account.meta_json = meta_json

            if access_token is not None:
                try:
                    account.set_access_token(access_token, self._encryption)
                except TokenEncryptionError as e:
                    raise AccountError(f"Access token encryption failed: {e}") from e

            if refresh_token is not None:
                try:
                    account.set_refresh_token(refresh_token, self._encryption)
                except TokenEncryptionError as e:
                    raise AccountError(f"Refresh token encryption failed: {e}") from e

            session.commit()
            return account

    def disconnect_account(self, account_id: int) -> Account:
        """
        Disconnect an account by setting status to 'disconnected'.
        Preserves historical data (publish jobs, attempts).

        Args:
            account_id: Internal account ID.

        Returns:
            Updated Account instance.

        Raises:
            AccountNotFoundError: If account does not exist.
        """
        return self.update_account(account_id, status="disconnected")

    def enable_account(self, account_id: int) -> Account:
        """
        Enable an account by setting status to 'active'.

        Args:
            account_id: Internal account ID.

        Returns:
            Updated Account instance.

        Raises:
            AccountNotFoundError: If account does not exist.
        """
        return self.update_account(account_id, status="active")

    def get_active_accounts(self, platform: str | None = None) -> list[Account]:
        """
        Get all active accounts, optionally filtered by platform.

        Args:
            platform: Platform to filter by (optional).

        Returns:
            List of active Account instances.
        """
        return self.list_accounts(platform=platform, status="active")

    def get_account_display_info(self, account: Account) -> dict:
        """
        Get safe display information for an account (no tokens).

        Args:
            account: Account instance.

        Returns:
            Dictionary with safe display fields.
        """
        return {
            "id": account.id,
            "platform": account.platform,
            "platform_account_id": account.platform_account_id,
            "username": account.username,
            "display_name": account.display_name,
            "status": account.status,
            "expires_at": account.expires_at.isoformat() if account.expires_at else None,
            "created_at": account.created_at.isoformat() if account.created_at else None,
            "updated_at": account.updated_at.isoformat() if account.updated_at else None,
        }

    def get_account_with_tokens(self, account_id: int) -> tuple[str, str | None]:
        """
        Get decrypted tokens for an account (internal use only).

        WARNING: Returns raw decrypted tokens. Use only for publishing/OAuth operations.
        Never log or display these tokens.

        Args:
            account_id: Internal account ID.

        Returns:
            Tuple of (access_token, refresh_token or None).

        Raises:
            AccountNotFoundError: If account does not exist.
            AccountError: If decryption fails.
        """
        account = self.get_account(account_id)
        try:
            access_token = account.get_access_token(self._encryption)
            refresh_token = account.get_refresh_token(self._encryption)
            return access_token, refresh_token
        except TokenEncryptionError as e:
            raise AccountError(f"Token decryption failed: {e}") from e

    def create_development_account(
        self,
        platform: str,
        platform_account_id: str,
        username: str,
        access_token: str = "dev_access_token",
        refresh_token: str | None = "dev_refresh_token",
        display_name: str | None = None,
        status: str = "active",
    ) -> Account:
        """
        Create a development/test account.

        WARNING: This is for DEVELOPMENT/TESTING only.
        Does not perform real OAuth. Tokens are placeholder values.

        Args:
            platform: Platform identifier (instagram, tiktok, youtube).
            platform_account_id: Platform's user/channel ID.
            username: Display handle (@username or channel name).
            access_token: Placeholder access token (default: "dev_access_token").
            refresh_token: Placeholder refresh token (default: "dev_refresh_token").
            display_name: Full name / channel title (optional).
            status: Account status (default: active).

        Returns:
            Created Account instance.
        """
        return self.create_account(
            platform=platform,
            platform_account_id=platform_account_id,
            username=username,
            access_token=access_token,
            refresh_token=refresh_token,
            display_name=display_name or f"DEV {platform.title()} Account",
            status=status,
            meta_json='{"source": "development"}',
        )