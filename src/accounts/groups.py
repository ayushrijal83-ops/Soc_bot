"""Account Group Manager: group accounts into content niches (e.g. AI Anime, Motivation).

An account belongs to zero or one group at a time: membership is the single nullable
``accounts.group_id`` column (migration 007), never a join table, so an account can never be a
member of two groups and "move" is just reassigning that column. Every operation here only ever
touches ``accounts.group_id`` and ``account_groups`` rows: tokens, publishing jobs, attempts and
history are never read, written or deleted.
"""

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import update

from src.storage.database import Account, AccountGroup, Database


class GroupError(Exception):
    """Base exception for account group management errors."""


class GroupNotFoundError(GroupError):
    """Raised when a group is not found."""


class DuplicateGroupError(GroupError):
    """Raised when creating or renaming a group to a name that already exists."""


class AccountNotFoundError(GroupError):
    """Raised when the referenced account does not exist."""


class AccountAlreadyAssignedError(GroupError):
    """Raised when assigning an account that already belongs to a different group. The account
    stays in its current group; the caller must unassign it first."""


class GroupManager:
    """Manages account groups (content niches) and account-to-group membership."""

    def __init__(self, database: Database):
        self._database = database

    @contextmanager
    def _session(self):
        """Get a database session."""
        with self._database.session() as session:
            yield session

    def _check_duplicate_name(self, session, name: str, exclude_id: int | None = None) -> None:
        query = session.query(AccountGroup).filter(AccountGroup.name == name)
        if exclude_id is not None:
            query = query.filter(AccountGroup.id != exclude_id)
        if query.first():
            raise DuplicateGroupError(f"A group named '{name}' already exists")

    @staticmethod
    def _clean_name(name: str) -> str:
        name = (name or "").strip()
        if not name:
            raise GroupError("Group name cannot be empty")
        return name

    # --- create / list --------------------------------------------------------

    def create_group(self, name: str) -> AccountGroup:
        """
        Create a new account group.

        Args:
            name: Group name (e.g. "AI Anime"). Must be unique.

        Returns:
            Created AccountGroup instance.

        Raises:
            GroupError: If name is empty.
            DuplicateGroupError: If a group with this name already exists.
        """
        name = self._clean_name(name)
        with self._session() as session:
            self._check_duplicate_name(session, name)
            group = AccountGroup(name=name)
            session.add(group)
            session.flush()
            session.commit()
            return group

    def get_group(self, group_id: int) -> AccountGroup:
        """
        Get a group by ID.

        Raises:
            GroupNotFoundError: If the group does not exist.
        """
        with self._session() as session:
            group = session.get(AccountGroup, group_id)
            if group is None:
                raise GroupNotFoundError(f"Group with ID {group_id} not found")
            return group

    def list_groups(self) -> list[AccountGroup]:
        """List every group, alphabetically by name."""
        with self._session() as session:
            return session.query(AccountGroup).order_by(AccountGroup.name).all()

    # --- rename / delete --------------------------------------------------------

    def rename_group(self, group_id: int, new_name: str) -> AccountGroup:
        """
        Rename a group. Membership (which accounts belong to it) is unaffected.

        Raises:
            GroupNotFoundError: If the group does not exist.
            DuplicateGroupError: If another group already has this name.
        """
        new_name = self._clean_name(new_name)
        with self._session() as session:
            group = session.get(AccountGroup, group_id)
            if group is None:
                raise GroupNotFoundError(f"Group with ID {group_id} not found")
            self._check_duplicate_name(session, new_name, exclude_id=group_id)
            group.name = new_name
            group.updated_at = datetime.now(timezone.utc)
            session.commit()
            return group

    def delete_group(self, group_id: int) -> int:
        """
        Delete a group. Its accounts are never disconnected or deleted: they become unassigned
        (``group_id`` set to NULL). Publishing jobs, attempts and history are untouched.

        Args:
            group_id: Internal group ID.

        Returns:
            Number of accounts that were unassigned by this deletion.

        Raises:
            GroupNotFoundError: If the group does not exist.
        """
        with self._session() as session:
            group = session.get(AccountGroup, group_id)
            if group is None:
                raise GroupNotFoundError(f"Group with ID {group_id} not found")
            result = session.execute(
                update(Account).where(Account.group_id == group_id).values(group_id=None)
            )
            session.delete(group)
            session.commit()
            return result.rowcount

    # --- membership --------------------------------------------------------

    def assign_account(self, account_id: int, group_id: int) -> Account:
        """
        Assign an unoccupied account to a group. An account already in a DIFFERENT group is
        rejected (AccountAlreadyAssignedError): it stays in its current group until the caller
        explicitly unassigns it first, then assigns it to the new one. Assigning an account to
        the group it is already in is a no-op success (not a conflict).

        Args:
            account_id: Internal account ID.
            group_id: Internal group ID to assign the account to.

        Returns:
            Updated Account instance.

        Raises:
            AccountNotFoundError: If the account does not exist.
            GroupNotFoundError: If the group does not exist.
            AccountAlreadyAssignedError: If the account already belongs to a different group.
        """
        with self._session() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise AccountNotFoundError(f"Account with ID {account_id} not found")
            group = session.get(AccountGroup, group_id)
            if group is None:
                raise GroupNotFoundError(f"Group with ID {group_id} not found")
            if account.group_id is not None and account.group_id != group_id:
                current = session.get(AccountGroup, account.group_id)
                current_name = current.name if current is not None else f"group #{account.group_id}"
                raise AccountAlreadyAssignedError(
                    f"Account #{account_id} is already in '{current_name}'. Unassign it first, "
                    "then assign it to the new group."
                )
            account.group_id = group_id
            session.commit()
            return account

    def check_available(self, account_id: int, group_id: int) -> None:
        """Read-only: raise AccountAlreadyAssignedError if assign_account(account_id, group_id)
        would be rejected (the account belongs to a DIFFERENT group already). No-op if the
        account is unassigned or already in ``group_id``. Lets a caller preview a rejection
        before doing anything else, instead of discovering it only after other side effects."""
        with self._session() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise AccountNotFoundError(f"Account with ID {account_id} not found")
            if account.group_id is not None and account.group_id != group_id:
                current = session.get(AccountGroup, account.group_id)
                current_name = current.name if current is not None else f"group #{account.group_id}"
                raise AccountAlreadyAssignedError(
                    f"Account #{account_id} is already in '{current_name}'. Unassign it first, "
                    "then assign it to the new group."
                )

    def unassign_account(self, account_id: int) -> Account:
        """
        Remove an account from its group, if any. A no-op (still succeeds) if the account was
        already unassigned. Never disconnects or deletes the account.

        Raises:
            AccountNotFoundError: If the account does not exist.
        """
        with self._session() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise AccountNotFoundError(f"Account with ID {account_id} not found")
            account.group_id = None
            session.commit()
            return account

    def move_account(self, account_id: int, new_group_id: int) -> Account:
        """
        Explicitly move an account from its current group (if any) to another: unassigns it,
        then assigns it to ``new_group_id``, in one call. Unlike assign_account, this never
        rejects an already-assigned account -- calling move_account IS the explicit action a
        caller takes to relocate it (assign_account's rejection exists precisely so a plain
        "assign" can never do this implicitly). Membership is a single nullable column, so this
        can never leave the account a member of two groups.

        Raises:
            AccountNotFoundError: If the account does not exist.
            GroupNotFoundError: If the destination group does not exist.
        """
        with self._session() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise AccountNotFoundError(f"Account with ID {account_id} not found")
            group = session.get(AccountGroup, new_group_id)
            if group is None:
                raise GroupNotFoundError(f"Group with ID {new_group_id} not found")
            account.group_id = new_group_id
            session.commit()
            return account

    def accounts_in(self, group_id: int) -> list[Account]:
        """
        List the accounts currently in a group.

        Raises:
            GroupNotFoundError: If the group does not exist.
        """
        with self._session() as session:
            group = session.get(AccountGroup, group_id)
            if group is None:
                raise GroupNotFoundError(f"Group with ID {group_id} not found")
            return (
                session.query(Account)
                .filter(Account.group_id == group_id)
                .order_by(Account.platform, Account.username)
                .all()
            )
