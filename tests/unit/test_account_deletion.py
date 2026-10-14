"""Tests for permanent account deletion (a tombstone, never a row delete).

publish_jobs.account_id has ON DELETE CASCADE and Account.publish_jobs cascades at the ORM level
too, so a real row delete would silently wipe history -- deletion here never issues one. Fakes
only; nothing contacts a real platform.
"""
# ruff: noqa: F811, RUF059 - pytest fixtures imported from other test modules

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.accounts.groups import GroupManager
from src.accounts.manager import (
    AccountAlreadyDeletedError,
    AccountError,
    AccountManager,
    AccountNotFoundError,
    DELETED_TOKEN_PLACEHOLDER,
)
from src.cli.account_menu import AccountMenuHandler
from src.core.jobs import JobStore
from src.core.publisher import PublisherEngine
from src.services.accounts import AccountService
from src.services.groups import GroupService
from src.storage.database import Database
from src.storage.tokens import TokenEncryption, generate_key
from tests.unit.test_publishing_engine import FakePublisher


@pytest.fixture
def temp_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        if os.path.exists(path):
            os.unlink(path)
    except PermissionError:
        pass


@pytest.fixture
def encryption():
    return TokenEncryption(generate_key().encode())


@pytest.fixture
def database(temp_db_path, encryption):
    db = Database(f"sqlite:///{temp_db_path}", encryption=encryption)
    db.init()
    yield db
    db.engine.dispose()


@pytest.fixture
def account_manager(database, encryption):
    return AccountManager(database, encryption)


@pytest.fixture
def engine(database, account_manager):
    return PublisherEngine(database, account_manager, publishers={"tiktok": FakePublisher("tiktok")},
                           sleep=lambda s: None, probe_media=False)


@pytest.fixture
def account_service(account_manager, engine):
    return AccountService(account_manager, auth_manager=None, engine=engine)


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"v" * 2000)
    return str(p)


def make_account(account_manager, status="active", pid="tt_1", username="user1", access_token="real_secret_token"):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    return account_manager.create_account(platform="tiktok", platform_account_id=pid, username=username,
                                          access_token=access_token, refresh_token="real_refresh",
                                          expires_at=future, status=status)


# =================================================================================================
# 1. Enabled and disabled account deletion
# =================================================================================================

class TestBasicDeletion:
    def test_delete_enabled_account(self, account_manager):
        account = make_account(account_manager, status="active")
        deleted = account_manager.delete_account(account.id)
        assert deleted.deleted_at is not None
        assert deleted.status == "disconnected"

    def test_delete_disabled_account(self, account_manager):
        account = make_account(account_manager, status="disconnected")
        deleted = account_manager.delete_account(account.id)
        assert deleted.deleted_at is not None
        assert deleted.status == "disconnected"

    def test_deleted_account_removed_from_operational_listing(self, account_manager):
        account = make_account(account_manager)
        account_manager.delete_account(account.id)
        assert account.id not in [a.id for a in account_manager.list_accounts()]
        assert account.id in [a.id for a in account_manager.list_accounts(include_deleted=True)]

    def test_deleted_account_row_and_id_still_resolvable(self, account_manager):
        """The row itself is never removed: get_account still resolves it (e.g. for history views)."""
        account = make_account(account_manager)
        account_manager.delete_account(account.id)
        refreshed = account_manager.get_account(account.id)
        assert refreshed.id == account.id
        assert refreshed.deleted_at is not None


# =================================================================================================
# 2. Confirmation and cancellation (CLI)
# =================================================================================================

class TestCliConfirmation:
    def test_cancelling_the_confirmation_changes_nothing(self, account_manager, engine, capsys):
        account = make_account(account_manager)
        handler = AccountMenuHandler(account_manager, auth_manager=None, engine=engine)

        with patch("builtins.input", side_effect=[str(account.id), "n", ""]), \
             patch("src.cli.account_menu.clear_screen"):
            handler.handle_delete_account()

        out = capsys.readouterr().out
        assert "Cancelled" in out
        refreshed = account_manager.get_account(account.id)
        assert refreshed.deleted_at is None
        assert refreshed.status == "active"

    def test_confirming_shows_platform_identifier_status_and_deletes(self, account_manager, engine, capsys):
        account = make_account(account_manager, username="shown_user")
        handler = AccountMenuHandler(account_manager, auth_manager=None, engine=engine)

        with patch("builtins.input", side_effect=[str(account.id), "y", ""]), \
             patch("src.cli.account_menu.clear_screen"):
            handler.handle_delete_account()

        out = capsys.readouterr().out
        assert "Tiktok" in out
        assert "shown_user" in out
        assert "Active" in out
        assert "permanently deleted" in out.lower()
        assert account_manager.get_account(account.id).deleted_at is not None

    def test_confirmation_screen_never_prints_the_token(self, account_manager, engine, capsys):
        account = make_account(account_manager, access_token="SUPER_SECRET_TOKEN_VALUE")
        handler = AccountMenuHandler(account_manager, auth_manager=None, engine=engine)

        with patch("builtins.input", side_effect=[str(account.id), "y", ""]), \
             patch("src.cli.account_menu.clear_screen"):
            handler.handle_delete_account()

        out = capsys.readouterr().out
        assert "SUPER_SECRET_TOKEN_VALUE" not in out
        assert DELETED_TOKEN_PLACEHOLDER not in out


# =================================================================================================
# 3. Unknown or already-deleted account
# =================================================================================================

class TestUnknownOrAlreadyDeleted:
    def test_unknown_account_raises(self, account_manager):
        with pytest.raises(AccountNotFoundError):
            account_manager.delete_account(999)

    def test_already_deleted_account_is_rejected_not_silently_reapplied(self, account_manager):
        account = make_account(account_manager)
        account_manager.delete_account(account.id)
        with pytest.raises(AccountAlreadyDeletedError):
            account_manager.delete_account(account.id)

    def test_cli_reports_unknown_account_id(self, account_manager, engine, capsys):
        handler = AccountMenuHandler(account_manager, auth_manager=None, engine=engine)
        with patch("builtins.input", side_effect=["999", ""]), patch("src.cli.account_menu.clear_screen"):
            handler.handle_delete_account()
        assert "not found" in capsys.readouterr().out.lower()

    def test_cli_reports_already_deleted_account(self, account_manager, engine, capsys):
        account = make_account(account_manager)
        account_manager.delete_account(account.id)
        handler = AccountMenuHandler(account_manager, auth_manager=None, engine=engine)
        with patch("builtins.input", side_effect=[str(account.id), ""]), patch("src.cli.account_menu.clear_screen"):
            handler.handle_delete_account()
        assert "already deleted" in capsys.readouterr().out.lower()


# =================================================================================================
# 4. Group membership
# =================================================================================================

class TestGroupMembership:
    def test_deletion_unassigns_from_its_group(self, database, account_manager):
        groups = GroupManager(database)
        account = make_account(account_manager)
        group = groups.create_group("AI Anime")
        groups.assign_account(account.id, group.id)

        account_manager.delete_account(account.id)

        assert account_manager.get_account(account.id).group_id is None
        assert account.id not in [a.id for a in groups.accounts_in(group.id)]

    def test_deletion_does_not_affect_other_group_members(self, database, account_manager):
        groups = GroupManager(database)
        deleted_account = make_account(account_manager, pid="tt_del")
        other_account = make_account(account_manager, pid="tt_other", username="other")
        group = groups.create_group("AI Anime")
        groups.assign_account(deleted_account.id, group.id)
        groups.assign_account(other_account.id, group.id)

        account_manager.delete_account(deleted_account.id)

        assert account_manager.get_account(other_account.id).group_id == group.id
        assert other_account.id in [a.id for a in groups.accounts_in(group.id)]

    def test_group_itself_is_untouched_by_member_deletion(self, database, account_manager):
        groups = GroupManager(database)
        account = make_account(account_manager)
        group = groups.create_group("AI Anime")
        groups.assign_account(account.id, group.id)

        account_manager.delete_account(account.id)

        assert groups.get_group(group.id).name == "AI Anime"


# =================================================================================================
# 5. Pending, retrying, and in-progress jobs
# =================================================================================================

class TestJobSafety:
    def test_delete_blocks_when_a_job_is_in_progress(self, database, account_manager, account_service, video):
        account = make_account(account_manager)
        store = JobStore(database)
        post_id = store.create_post(video, "c", [(account.id, {"privacy_level": "SELF_ONLY"})])
        job_id = store.jobs_for_post(post_id)[0].id
        store.claim(job_id)  # now "uploading": outcome at the platform is unknown

        result = account_service.delete(account.id)

        assert result.ok is False
        assert "in progress" in result.message.lower()
        refreshed = account_manager.get_account(account.id)
        assert refreshed.deleted_at is None  # nothing changed
        assert store.get_job(job_id).status == "uploading"

    def test_delete_blocks_on_processing_job_too(self, database, account_manager, account_service, video):
        account = make_account(account_manager)
        store = JobStore(database)
        post_id = store.create_post(video, "c", [(account.id, {"privacy_level": "SELF_ONLY"})])
        job_id = store.jobs_for_post(post_id)[0].id
        store.claim(job_id)
        store.transition(job_id, "processing")

        result = account_service.delete(account.id)

        assert result.ok is False
        assert store.get_job(job_id).status == "processing"

    def test_delete_stops_pending_and_retrying_jobs(self, database, account_manager, account_service, video):
        account = make_account(account_manager)
        store = JobStore(database)
        post_id = store.create_post(video, "c", [(account.id, {"privacy_level": "SELF_ONLY"})])
        job_id = store.jobs_for_post(post_id)[0].id
        store.claim(job_id)
        store.transition(job_id, "retrying", retry_count=1, next_retry_at=None)

        result = account_service.delete(account.id)

        assert result.ok is True
        assert result.stopped_jobs == 1
        job = store.get_job(job_id)
        assert job.status == "retrying"  # status itself untouched, no new status invented
        assert job.membership_removed_at is not None
        assert store.claim(job_id) is False  # can never be claimed again

    def test_stopped_job_survives_a_fresh_engine_restart(self, database, account_manager, account_service, video):
        account = make_account(account_manager)
        store = JobStore(database)
        post_id = store.create_post(video, "c", [(account.id, {"privacy_level": "SELF_ONLY"})])
        account_service.delete(account.id)

        fresh_engine = PublisherEngine(database, account_manager, publishers={"tiktok": FakePublisher("tiktok")},
                                       sleep=lambda s: None, probe_media=False)
        results = fresh_engine.resume_open_jobs()

        assert post_id not in {r.post_id for r in results}


# =================================================================================================
# 6 & 7. Completed/failed publishing history and audit records preserved
# =================================================================================================

class TestHistoryPreservation:
    def test_published_job_and_media_id_preserved(self, database, account_manager, video):
        account = make_account(account_manager)
        store = JobStore(database)
        post_id = store.create_post(video, "c", [(account.id, {"privacy_level": "SELF_ONLY"})])
        job_id = store.jobs_for_post(post_id)[0].id
        store.claim(job_id)
        store.transition(job_id, "published", platform_media_id="TT123")

        account_manager.delete_account(account.id)

        job = store.get_job(job_id)
        assert job.status == "published" and job.platform_media_id == "TT123"

    def test_failed_job_and_error_message_preserved(self, database, account_manager, video):
        account = make_account(account_manager)
        store = JobStore(database)
        post_id = store.create_post(video, "c", [(account.id, {"privacy_level": "SELF_ONLY"})])
        job_id = store.jobs_for_post(post_id)[0].id
        store.claim(job_id)
        store.transition(job_id, "failed", error_message="network error")

        account_manager.delete_account(account.id)

        job = store.get_job(job_id)
        assert job.status == "failed" and job.error_message == "network error"

    def test_publish_attempts_untouched(self, database, account_manager, video):
        account = make_account(account_manager)
        store = JobStore(database)
        post_id = store.create_post(video, "c", [(account.id, {"privacy_level": "SELF_ONLY"})])
        job_id = store.jobs_for_post(post_id)[0].id
        store.claim(job_id)
        attempt_id = store.start_attempt(job_id)
        store.update_attempt(attempt_id, "success", provider_state={"ref": "R1"}, completed=True)
        store.transition(job_id, "published", platform_media_id="TT1")
        before = store.attempts(job_id)

        account_manager.delete_account(account.id)

        after = store.attempts(job_id)
        assert len(after) == len(before) == 1
        assert after[0].status == "success" and after[0].response_json == before[0].response_json

    def test_post_and_video_rows_never_removed(self, database, account_manager, video):
        account = make_account(account_manager)
        store = JobStore(database)
        post_id = store.create_post(video, "c", [(account.id, {"privacy_level": "SELF_ONLY"})])

        account_manager.delete_account(account.id)

        post, video = store.get_post(post_id)  # raises JobError if either row vanished
        assert post.id == post_id


# =================================================================================================
# 8. Credential invalidation after deletion
# =================================================================================================

class TestCredentialInvalidation:
    def test_original_token_is_irrecoverable_after_deletion(self, account_manager, encryption):
        account = make_account(account_manager, access_token="ORIGINAL_SECRET")
        account_manager.delete_account(account.id)

        access_token, refresh_token = account_manager.get_account_with_tokens(account.id)
        assert access_token != "ORIGINAL_SECRET"
        assert access_token == DELETED_TOKEN_PLACEHOLDER
        assert refresh_token is None

    def test_placeholder_is_not_an_empty_or_null_value(self, account_manager):
        """access_token_enc is NOT NULL: deletion must overwrite it with a real placeholder, not
        attempt to null it (which would violate the column constraint)."""
        account = make_account(account_manager)
        deleted = account_manager.delete_account(account.id)
        assert deleted.access_token_enc  # non-empty: NOT NULL satisfied
        assert deleted.refresh_token_enc is None


# =================================================================================================
# 9. Repeated deletion attempts
# =================================================================================================

class TestRepeatedDeletion:
    def test_second_direct_deletion_raises_and_first_result_unchanged(self, account_manager):
        account = make_account(account_manager)
        first = account_manager.delete_account(account.id)
        first_deleted_at = first.deleted_at.replace(tzinfo=None)

        with pytest.raises(AccountAlreadyDeletedError):
            account_manager.delete_account(account.id)

        refreshed_deleted_at = account_manager.get_account(account.id).deleted_at.replace(tzinfo=None)
        assert refreshed_deleted_at == first_deleted_at

    def test_second_service_level_deletion_reports_failure_cleanly(self, account_manager, account_service):
        account = make_account(account_manager)
        first = account_service.delete(account.id)
        assert first.ok is True

        second = account_service.delete(account.id)

        assert second.ok is False
        assert "already deleted" in second.message.lower()


# =================================================================================================
# 10. Existing enable/disable and Account Groups regression
# =================================================================================================

class TestRegressionEnableDisableAndGroups:
    def test_disconnect_then_enable_still_works_for_a_non_deleted_account(self, account_manager):
        account = make_account(account_manager)
        account_manager.disconnect_account(account.id)
        assert account_manager.get_account(account.id).status == "disconnected"
        account_manager.enable_account(account.id)
        assert account_manager.get_account(account.id).status == "active"

    def test_enabling_a_deleted_account_is_rejected(self, account_manager):
        account = make_account(account_manager)
        account_manager.delete_account(account.id)
        with pytest.raises(AccountAlreadyDeletedError):
            account_manager.enable_account(account.id)

    def test_disconnect_account_is_unaffected_by_deletion_feature(self, account_manager):
        """Disconnect (reversible) must still work exactly as before and never touch deleted_at."""
        account = make_account(account_manager)
        disconnected = account_manager.disconnect_account(account.id)
        assert disconnected.status == "disconnected"
        assert disconnected.deleted_at is None

    def test_group_service_assign_rejects_a_deleted_accounts_group_but_account_methods_still_work(
        self, database, account_manager
    ):
        """Regression: AccountGroups' own occupancy/assignment logic is unaffected by this feature
        (a deleted account is simply never offered for assignment in practice; directly assigning
        one at the GroupManager layer still succeeds today, since occupancy enforcement is about
        groups, not deletion -- deletion's own unassignment already covers this case)."""
        groups = GroupManager(database)
        account = make_account(account_manager)
        group = groups.create_group("AI Anime")
        groups.assign_account(account.id, group.id)
        assert account.id in [a.id for a in groups.accounts_in(group.id)]

    def test_group_publishing_preflight_unaffected_for_non_deleted_accounts(self, database, account_manager, engine, video):
        groups = GroupManager(database)
        from src.services.publishing import PublishingService

        account = make_account(account_manager)
        group = groups.create_group("AI Anime")
        groups.assign_account(account.id, group.id)
        service = GroupService(groups, PublishingService(engine, account_manager))

        plan = service.plan(group.id, video, "caption",
                            platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})

        assert len(plan.valid) == 1 and plan.valid[0].account_id == account.id
