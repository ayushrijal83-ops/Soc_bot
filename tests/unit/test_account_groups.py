"""Tests for Account Group Manager (Phase 1: storage and management only)."""

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from src.accounts.groups import (
    AccountAlreadyAssignedError,
    AccountNotFoundError,
    DuplicateGroupError,
    GroupError,
    GroupManager,
    GroupNotFoundError,
)
from src.accounts.manager import AccountManager
from src.storage.database import Account, AccountGroup, Database, Post, PublishJob, Video
from src.storage.tokens import TokenEncryption, generate_key

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "storage" / "migrations"


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
    """Create a test database instance (schema + migrations applied)."""
    db = Database(f"sqlite:///{temp_db_path}", encryption=encryption)
    db.init()
    yield db
    db.engine.dispose()


@pytest.fixture
def account_manager(database, encryption):
    return AccountManager(database, encryption)


@pytest.fixture
def group_manager(database):
    return GroupManager(database)


def make_account(account_manager, platform="instagram", platform_account_id="ig_1", username="user"):
    return account_manager.create_account(
        platform=platform, platform_account_id=platform_account_id, username=username,
        access_token="tok",
    )


class TestGroupCRUD:
    def test_create_group(self, group_manager):
        group = group_manager.create_group("AI Anime")
        assert group.id is not None
        assert group.name == "AI Anime"
        assert group.created_at is not None

    def test_create_group_strips_whitespace(self, group_manager):
        group = group_manager.create_group("  Motivation  ")
        assert group.name == "Motivation"

    def test_create_group_empty_name_rejected(self, group_manager):
        with pytest.raises(GroupError):
            group_manager.create_group("   ")

    def test_create_group_duplicate_name_rejected(self, group_manager):
        group_manager.create_group("AI Anime")
        with pytest.raises(DuplicateGroupError):
            group_manager.create_group("AI Anime")

    def test_list_groups_alphabetical(self, group_manager):
        group_manager.create_group("Motivation")
        group_manager.create_group("AI Anime")
        group_manager.create_group("Interesting Facts")
        names = [g.name for g in group_manager.list_groups()]
        assert names == ["AI Anime", "Interesting Facts", "Motivation"]

    def test_get_group_not_found(self, group_manager):
        with pytest.raises(GroupNotFoundError):
            group_manager.get_group(999)

    def test_rename_group(self, group_manager):
        group = group_manager.create_group("AI Anime")
        renamed = group_manager.rename_group(group.id, "AI Anime Clips")
        assert renamed.name == "AI Anime Clips"
        assert group_manager.get_group(group.id).name == "AI Anime Clips"

    def test_rename_group_not_found(self, group_manager):
        with pytest.raises(GroupNotFoundError):
            group_manager.rename_group(999, "New Name")

    def test_rename_group_duplicate_name_rejected(self, group_manager):
        group_manager.create_group("AI Anime")
        other = group_manager.create_group("Motivation")
        with pytest.raises(DuplicateGroupError):
            group_manager.rename_group(other.id, "AI Anime")

    def test_rename_group_to_its_own_current_name_is_allowed(self, group_manager):
        group = group_manager.create_group("AI Anime")
        renamed = group_manager.rename_group(group.id, "AI Anime")
        assert renamed.name == "AI Anime"

    def test_delete_group_not_found(self, group_manager):
        with pytest.raises(GroupNotFoundError):
            group_manager.delete_group(999)

    def test_delete_group_with_no_accounts(self, group_manager):
        group = group_manager.create_group("Empty Group")
        unassigned = group_manager.delete_group(group.id)
        assert unassigned == 0
        with pytest.raises(GroupNotFoundError):
            group_manager.get_group(group.id)


class TestMembership:
    def test_assign_account_to_group(self, group_manager, account_manager):
        account = make_account(account_manager)
        group = group_manager.create_group("AI Anime")
        updated = group_manager.assign_account(account.id, group.id)
        assert updated.group_id == group.id
        assert account_manager.get_account(account.id).group_id == group.id

    def test_assign_account_not_found(self, group_manager):
        group = group_manager.create_group("AI Anime")
        with pytest.raises(AccountNotFoundError):
            group_manager.assign_account(999, group.id)

    def test_assign_to_missing_group(self, group_manager, account_manager):
        account = make_account(account_manager)
        with pytest.raises(GroupNotFoundError):
            group_manager.assign_account(account.id, 999)

    def test_assign_occupied_account_to_a_different_group_is_rejected(self, group_manager, account_manager):
        """An account already in group A: assigning it straight to group B is rejected with a
        clear explanation, and it stays in group A until explicitly unassigned first."""
        account = make_account(account_manager)
        group_a = group_manager.create_group("AI Anime")
        group_b = group_manager.create_group("Motivation")
        group_manager.assign_account(account.id, group_a.id)

        with pytest.raises(AccountAlreadyAssignedError, match="AI Anime"):
            group_manager.assign_account(account.id, group_b.id)

        refreshed = account_manager.get_account(account.id)
        assert refreshed.group_id == group_a.id  # unchanged: still in its original group
        assert any(a.id == account.id for a in group_manager.accounts_in(group_a.id))
        assert all(a.id != account.id for a in group_manager.accounts_in(group_b.id))

    def test_unassign_then_assign_is_the_correct_way_to_relocate(self, group_manager, account_manager):
        """The documented workaround: explicit unassign, then assign, achieves what a direct
        reassignment is no longer allowed to do -- and still never duplicates membership."""
        account = make_account(account_manager)
        group_a = group_manager.create_group("AI Anime")
        group_b = group_manager.create_group("Motivation")
        group_manager.assign_account(account.id, group_a.id)

        group_manager.unassign_account(account.id)
        group_manager.assign_account(account.id, group_b.id)

        refreshed = account_manager.get_account(account.id)
        assert refreshed.group_id == group_b.id
        assert all(a.id != account.id for a in group_manager.accounts_in(group_a.id))
        assert any(a.id == account.id for a in group_manager.accounts_in(group_b.id))

    def test_assigning_to_the_same_group_already_held_is_a_no_op_success(self, group_manager, account_manager):
        account = make_account(account_manager)
        group = group_manager.create_group("AI Anime")
        group_manager.assign_account(account.id, group.id)

        result = group_manager.assign_account(account.id, group.id)  # not a conflict

        assert result.group_id == group.id

    def test_check_available_matches_assign_account_without_changing_anything(self, group_manager, account_manager):
        account = make_account(account_manager)
        group_a = group_manager.create_group("AI Anime")
        group_b = group_manager.create_group("Motivation")
        group_manager.assign_account(account.id, group_a.id)

        with pytest.raises(AccountAlreadyAssignedError):
            group_manager.check_available(account.id, group_b.id)

        assert account_manager.get_account(account.id).group_id == group_a.id  # read-only

    def test_move_account_between_groups(self, group_manager, account_manager):
        account = make_account(account_manager)
        group_a = group_manager.create_group("AI Anime")
        group_b = group_manager.create_group("Motivation")
        group_manager.assign_account(account.id, group_a.id)
        moved = group_manager.move_account(account.id, group_b.id)
        assert moved.group_id == group_b.id
        accounts_a = [a.id for a in group_manager.accounts_in(group_a.id)]
        accounts_b = [a.id for a in group_manager.accounts_in(group_b.id)]
        assert account.id not in accounts_a
        assert accounts_b.count(account.id) == 1  # never duplicated

    def test_move_account_to_missing_group_raises_and_keeps_original_membership(self, group_manager, account_manager):
        account = make_account(account_manager)
        group_a = group_manager.create_group("AI Anime")
        group_manager.assign_account(account.id, group_a.id)
        with pytest.raises(GroupNotFoundError):
            group_manager.move_account(account.id, 999)
        assert account_manager.get_account(account.id).group_id == group_a.id

    def test_unassign_account(self, group_manager, account_manager):
        account = make_account(account_manager)
        group = group_manager.create_group("AI Anime")
        group_manager.assign_account(account.id, group.id)
        group_manager.unassign_account(account.id)
        assert account_manager.get_account(account.id).group_id is None

    def test_unassign_already_unassigned_account_is_a_noop(self, group_manager, account_manager):
        account = make_account(account_manager)
        result = group_manager.unassign_account(account.id)
        assert result.group_id is None

    def test_unassign_account_not_found(self, group_manager):
        with pytest.raises(AccountNotFoundError):
            group_manager.unassign_account(999)

    def test_accounts_in_group_not_found(self, group_manager):
        with pytest.raises(GroupNotFoundError):
            group_manager.accounts_in(999)

    def test_accounts_in_empty_group(self, group_manager):
        group = group_manager.create_group("AI Anime")
        assert group_manager.accounts_in(group.id) == []

    def test_accounts_in_mixed_platform_group(self, group_manager, account_manager):
        """A group may contain accounts from Instagram, TikTok and YouTube at once."""
        ig = make_account(account_manager, "instagram", "ig_1", "ig_user")
        tt = make_account(account_manager, "tiktok", "tt_1", "tt_user")
        yt = make_account(account_manager, "youtube", "yt_1", "yt_user")
        group = group_manager.create_group("Mixed Niche")
        for account in (ig, tt, yt):
            group_manager.assign_account(account.id, group.id)
        members = {a.id for a in group_manager.accounts_in(group.id)}
        assert members == {ig.id, tt.id, yt.id}

    def test_unassigned_accounts_still_listed_by_account_manager(self, group_manager, account_manager):
        """Unassigned accounts keep working normally: ordinary account listing is unaffected."""
        account = make_account(account_manager)
        accounts = account_manager.list_accounts()
        assert len(accounts) == 1
        assert accounts[0].group_id is None


class TestGroupDeletionPreservesAccountsAndHistory:
    def test_delete_group_unassigns_without_disconnecting(self, group_manager, account_manager):
        account = make_account(account_manager)
        group = group_manager.create_group("AI Anime")
        group_manager.assign_account(account.id, group.id)

        unassigned = group_manager.delete_group(group.id)

        assert unassigned == 1
        refreshed = account_manager.get_account(account.id)
        assert refreshed.group_id is None
        assert refreshed.status == "active"  # never disconnected
        with pytest.raises(GroupNotFoundError):
            group_manager.get_group(group.id)

    def test_delete_group_preserves_publishing_history(self, database, group_manager, account_manager):
        """Deleting a group must never touch publish_jobs / publish_attempts history."""
        account = make_account(account_manager)
        group = group_manager.create_group("AI Anime")
        group_manager.assign_account(account.id, group.id)

        with database.session() as session:
            video = Video(filename="clip.mp4", path="/videos/clip.mp4", size_bytes=1000)
            session.add(video)
            session.commit()
            post = Post(video_id=video.id, caption="hello")
            session.add(post)
            session.commit()
            job = PublishJob(post_id=post.id, account_id=account.id, status="published",
                             platform_media_id="abc123")
            session.add(job)
            session.commit()
            job_id = job.id

        group_manager.delete_group(group.id)

        with database.session() as session:
            job = session.get(PublishJob, job_id)
            assert job is not None
            assert job.status == "published"
            assert job.platform_media_id == "abc123"
            assert job.account_id == account.id

    def test_delete_group_does_not_affect_other_groups(self, group_manager, account_manager):
        account_a = make_account(account_manager, "instagram", "ig_a", "a")
        account_b = make_account(account_manager, "tiktok", "tt_b", "b")
        group_a = group_manager.create_group("AI Anime")
        group_b = group_manager.create_group("Motivation")
        group_manager.assign_account(account_a.id, group_a.id)
        group_manager.assign_account(account_b.id, group_b.id)

        group_manager.delete_group(group_a.id)

        assert account_manager.get_account(account_b.id).group_id == group_b.id
        assert [g.name for g in group_manager.list_groups()] == ["Motivation"]

    def test_db_level_on_delete_set_null_without_manager(self, database, group_manager, account_manager):
        """The FK constraint itself (ON DELETE SET NULL) protects accounts even if a group row
        were ever removed by something other than GroupManager.delete_group."""
        account = make_account(account_manager)
        group = group_manager.create_group("AI Anime")
        group_manager.assign_account(account.id, group.id)

        with database.session() as session:
            raw_group = session.get(AccountGroup, group.id)
            session.delete(raw_group)
            session.commit()

        assert account_manager.get_account(account.id).group_id is None


class TestMigrationBehavior:
    def test_account_groups_table_and_indexes_exist(self, database):
        inspector = inspect(database.engine)
        tables = set(inspector.get_table_names())
        assert "account_groups" in tables

        indexes = {idx["name"] for idx in inspector.get_indexes("account_groups")}
        unique = {uc["name"] for uc in inspector.get_unique_constraints("account_groups")}
        assert "uq_account_groups_name" in indexes or "uq_account_groups_name" in unique

    def test_accounts_table_has_group_id_column_and_index(self, database):
        inspector = inspect(database.engine)
        columns = {c["name"] for c in inspector.get_columns("accounts")}
        assert "group_id" in columns

        indexes = {idx["name"] for idx in inspector.get_indexes("accounts")}
        assert "idx_accounts_group" in indexes

    def test_migration_007_is_applied(self, database):
        assert 7 in database.get_applied_migrations()

    def test_v6_database_upgrades_to_v7_additively(self, temp_db_path, encryption):
        """A database built from migrations 1-6 only (no account_groups, no group_id) upgrades
        cleanly: existing accounts/jobs are untouched and the new column defaults to NULL."""
        db = Database(f"sqlite:///{temp_db_path}", encryption=encryption)
        for version in range(1, 7):
            name = next(p for p in MIGRATIONS_DIR.glob(f"{version:03d}_*.sql"))
            db.apply_migration(version, name.stem, name.read_text())

        with db.session() as session:
            cols_before = {r[1] for r in session.execute(text("PRAGMA table_info(accounts)"))}
        assert "group_id" not in cols_before

        # Insert via raw SQL (not the ORM AccountManager, whose mapped Account class always
        # expects group_id): this is a pre-migration-007 row, like a real pre-upgrade database.
        token_enc = encryption.encrypt("tok").decode()
        with db.session() as session:
            session.execute(
                text("INSERT INTO accounts (platform, platform_account_id, username, "
                     "access_token_enc, status) VALUES (:p, :pid, :u, :tok, 'active')"),
                {"p": "instagram", "pid": "ig_pre", "u": "pre_migration_user", "tok": token_enc},
            )
            session.commit()

        db.migrate()

        with db.session() as session:
            cols_after = {r[1] for r in session.execute(text("PRAGMA table_info(accounts)"))}
            tables = {r[0] for r in session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "group_id" in cols_after
        assert "account_groups" in tables
        assert db.get_applied_migrations() == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

        # The pre-existing account survived the upgrade untouched, unassigned by default.
        refreshed = AccountManager(db, encryption).find_account("instagram", "ig_pre")
        assert refreshed.username == "pre_migration_user"
        assert refreshed.group_id is None
        db.engine.dispose()
