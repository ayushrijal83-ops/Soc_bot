"""Tests for live group-membership reconciliation during active batches (Account Groups, phase 3).

No new publish_jobs.status value: publish_jobs.membership_removed_at (migration 009) is a plain
additive nullable column, guarded only by atomic conditional UPDATEs (the project's existing CAS
convention, same as JobStore.claim/transition). Fakes only; nothing here contacts a real platform.
"""
# ruff: noqa: F811, RUF059 - pytest fixtures imported from other test modules

from unittest.mock import patch

import pytest

from src.accounts.groups import AccountAlreadyAssignedError, GroupNotFoundError
from src.core.jobs import JobStore
from src.core.publisher import PublisherEngine
from src.platforms.base import PublishOutcome
from src.services.groups import AddImpact
from tests.unit.test_group_publishing import build_service, env, handler_for, make_account
from tests.unit.test_publishing_engine import FakePublisher


# =================================================================================================
# JobStore: the CAS primitives themselves
# =================================================================================================

class TestJobStoreMembershipGuards:
    def test_remove_membership_stops_a_pending_job_without_changing_its_status(self, env):
        db, accounts, video, _ = env
        account_id = make_account(accounts, "tiktok", "tt1", "u1")
        store = JobStore(db)
        post_id = store.create_post(video, "cap", [(account_id, {"privacy_level": "SELF_ONLY"})])
        job = store.jobs_for_post(post_id)[0]

        assert store.remove_membership(job.id) is True
        refreshed = store.get_job(job.id)
        assert refreshed.status == "pending"  # the status column itself is never touched
        assert refreshed.membership_removed_at is not None

    def test_remove_membership_is_false_on_a_second_call(self, env):
        db, accounts, video, _ = env
        account_id = make_account(accounts, "tiktok", "tt1", "u1")
        store = JobStore(db)
        post_id = store.create_post(video, "cap", [(account_id, {})])
        job = store.jobs_for_post(post_id)[0]
        assert store.remove_membership(job.id) is True
        assert store.remove_membership(job.id) is False

    def test_remove_membership_false_on_a_published_job(self, env):
        db, accounts, video, _ = env
        account_id = make_account(accounts, "tiktok", "tt1", "u1")
        store = JobStore(db)
        post_id = store.create_post(video, "cap", [(account_id, {})])
        job = store.jobs_for_post(post_id)[0]
        store.claim(job.id)
        store.transition(job.id, "published", platform_media_id="X")
        assert store.remove_membership(job.id) is False

    def test_claim_is_blocked_after_removal(self, env):
        db, accounts, video, _ = env
        account_id = make_account(accounts, "tiktok", "tt1", "u1")
        store = JobStore(db)
        post_id = store.create_post(video, "cap", [(account_id, {})])
        job = store.jobs_for_post(post_id)[0]
        store.remove_membership(job.id)
        assert store.claim(job.id) is False
        assert store.get_job(job.id).status == "pending"

    def test_restore_membership_allows_claim_again(self, env):
        db, accounts, video, _ = env
        account_id = make_account(accounts, "tiktok", "tt1", "u1")
        store = JobStore(db)
        post_id = store.create_post(video, "cap", [(account_id, {})])
        job = store.jobs_for_post(post_id)[0]
        store.remove_membership(job.id)
        assert store.restore_membership(job.id) is True
        assert store.get_job(job.id).membership_removed_at is None
        assert store.claim(job.id) is True

    def test_restore_membership_false_when_never_removed(self, env):
        db, accounts, video, _ = env
        account_id = make_account(accounts, "tiktok", "tt1", "u1")
        store = JobStore(db)
        post_id = store.create_post(video, "cap", [(account_id, {})])
        job = store.jobs_for_post(post_id)[0]
        assert store.restore_membership(job.id) is False

    def test_open_post_ids_excludes_a_post_whose_only_job_is_removed(self, env):
        db, accounts, video, _ = env
        account_id = make_account(accounts, "tiktok", "tt1", "u1")
        store = JobStore(db)
        post_id = store.create_post(video, "cap", [(account_id, {})])
        job = store.jobs_for_post(post_id)[0]
        assert post_id in store.open_post_ids()
        store.remove_membership(job.id)
        assert post_id not in store.open_post_ids()

    def test_open_post_ids_keeps_a_post_with_another_still_active_job(self, env):
        db, accounts, video, _ = env
        a1 = make_account(accounts, "tiktok", "tt1", "u1")
        a2 = make_account(accounts, "youtube", "yt1", "u2")
        store = JobStore(db)
        post_id = store.create_post(video, "cap", [(a1, {}), (a2, {})])
        jobs = {j.account_id: j for j in store.jobs_for_post(post_id)}
        store.remove_membership(jobs[a1].id)
        assert post_id in store.open_post_ids()  # a2's job is still open


class TestConcurrentClaimVsRemoval:
    """Deterministic CAS race coverage: whichever atomic UPDATE runs first wins; the other sees 0
    rows affected and reports honestly, never claiming or guessing the opposite outcome."""

    def test_claim_wins_the_race_removal_then_leaves_it_alone(self, env):
        db, accounts, video, _ = env
        account_id = make_account(accounts, "tiktok", "tt1", "u1")
        store = JobStore(db)
        post_id = store.create_post(video, "cap", [(account_id, {})])
        job = store.jobs_for_post(post_id)[0]

        assert store.claim(job.id) is True                  # a worker claims it first
        assert store.remove_membership(job.id) is False     # too late: already in flight
        assert store.get_job(job.id).status == "uploading"
        assert store.get_job(job.id).membership_removed_at is None

    def test_removal_wins_the_race_claim_then_fails(self, env):
        db, accounts, video, _ = env
        account_id = make_account(accounts, "tiktok", "tt1", "u1")
        store = JobStore(db)
        post_id = store.create_post(video, "cap", [(account_id, {})])
        job = store.jobs_for_post(post_id)[0]

        assert store.remove_membership(job.id) is True  # removal wins
        assert store.claim(job.id) is False              # the worker's claim now fails
        assert store.get_job(job.id).status == "pending"


# =================================================================================================
# GroupService: add reconciliation (confirmation, duplicate-safe re-addition)
# =================================================================================================

class TestAddReconciliation:
    def test_preview_add_identifies_active_batch_and_proposes_a_new_job(self, env):
        db, accounts, video, _ = env
        existing = make_account(accounts, "tiktok", "tt1", "u1")
        new_account = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(existing, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)  # not published yet: batch is still active

        impacts = service.preview_add(new_account, group.id)

        assert impacts == [AddImpact(post_id, "pending", "new_job")]

    def test_preview_add_is_empty_when_there_are_no_active_batches(self, env):
        service = build_service(env, {})
        group = service.create("AI Anime")
        account = make_account(env[1], "tiktok", "tt1", "u1")
        assert service.preview_add(account, group.id) == []

    def test_apply_add_creates_job_copying_the_batchs_existing_platform_options(self, env):
        db, accounts, video, _ = env
        existing = make_account(accounts, "tiktok", "tt1", "u1")
        new_account = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(existing, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        impacts = service.preview_add(new_account, group.id)

        results = service.apply_add(new_account, group.id, impacts)

        assert results == {post_id: "created"}
        jobs = {j.account_id: j for j in JobStore(db).jobs_for_post(post_id)}
        assert jobs[new_account].options == {"privacy_level": "SELF_ONLY"}
        assert accounts.get_account(new_account).group_id == group.id

    def test_apply_add_never_duplicates_on_repeat(self, env):
        db, accounts, video, _ = env
        existing = make_account(accounts, "tiktok", "tt1", "u1")
        new_account = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(existing, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        service.apply_add(new_account, group.id, service.preview_add(new_account, group.id))

        # "Re-adding" (preview again): nothing left to do, never a second job.
        assert service.preview_add(new_account, group.id) == []
        jobs = JobStore(db).jobs_for_post(post_id)
        assert sum(1 for j in jobs if j.account_id == new_account) == 1

    def test_apply_add_skips_a_platform_with_no_existing_destination_in_the_batch(self, env):
        db, accounts, video, _ = env
        tt_account = make_account(accounts, "tiktok", "tt1", "u1")
        yt_account = make_account(accounts, "youtube", "yt1", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok"), "youtube": FakePublisher("youtube")})
        group = service.create("AI Anime")
        service.assign(tt_account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        impacts = service.preview_add(yt_account, group.id)

        results = service.apply_add(yt_account, group.id, impacts)

        assert "skipped" in results[post_id]
        assert accounts.get_account(yt_account).group_id == group.id  # still assigned
        assert all(j.account_id != yt_account for j in JobStore(db).jobs_for_post(post_id))

    def test_removed_then_readded_restores_the_same_job_not_a_duplicate(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        job_id = JobStore(db).jobs_for_post(post_id)[0].id

        service.apply_remove(account, group.id)
        assert JobStore(db).get_job(job_id).membership_removed_at is not None

        impacts = service.preview_add(account, group.id)
        # The batch is now fully "stopped" (its only job was removed) -- restoring your own
        # just-removed job is still offered even though the batch itself is no longer "active".
        assert impacts == [AddImpact(post_id, "stopped", "restore")]
        results = service.apply_add(account, group.id, impacts)

        assert results == {post_id: "restored"}
        assert JobStore(db).get_job(job_id).membership_removed_at is None
        assert len(JobStore(db).jobs_for_post(post_id)) == 1  # same job, never duplicated


# =================================================================================================
# Occupancy enforcement: service layer must reject, not just the CLI
# =================================================================================================

class TestOccupancyEnforcement:
    def test_preview_add_rejects_an_account_occupied_by_a_different_group(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group_a = service.create("AI Anime")
        group_b = service.create("Motivation")
        service.assign(account, group_a.id)

        with pytest.raises(AccountAlreadyAssignedError, match="AI Anime"):
            service.preview_add(account, group_b.id)

        # Nothing changed: still in its original group.
        assert accounts.get_account(account).group_id == group_a.id

    def test_apply_add_never_creates_a_job_when_the_account_is_occupied_elsewhere(self, env):
        """The occupancy check happens BEFORE any job is created or restored: a rejection must
        never leave a partially-applied job behind."""
        db, accounts, video, _ = env
        occupied_account = make_account(accounts, "tiktok", "tt1", "u1")
        other_account = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group_a = service.create("AI Anime")
        group_b = service.create("Motivation")
        service.assign(occupied_account, group_a.id)
        service.assign(other_account, group_b.id)
        plan = service.plan(group_b.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group_b.id, plan)

        fabricated_impact = AddImpact(post_id, "pending", "new_job")
        with pytest.raises(AccountAlreadyAssignedError):
            service.apply_add(occupied_account, group_b.id, [fabricated_impact])

        assert accounts.get_account(occupied_account).group_id == group_a.id  # unchanged
        assert all(j.account_id != occupied_account for j in JobStore(db).jobs_for_post(post_id))

    def test_assigning_to_the_account_s_current_group_is_still_a_no_op_success(self, env):
        """Occupancy enforcement must not block re-assigning to the SAME group (not a conflict)."""
        account = make_account(env[1], "tiktok", "tt1", "u1")
        service = build_service(env, {})
        group = service.create("AI Anime")
        service.assign(account, group.id)

        result = service.assign(account, group.id)

        assert result.group_id == group.id


# =================================================================================================
# GroupService: remove reconciliation (safe-stop, truthful states, history preserved)
# =================================================================================================

class TestRemoveReconciliation:
    def test_apply_remove_stops_a_pending_job_and_unassigns(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        job_id = JobStore(db).jobs_for_post(post_id)[0].id

        outcomes = service.apply_remove(account, group.id)

        assert len(outcomes) == 1
        assert outcomes[0].post_id == post_id and outcomes[0].stopped is True
        assert JobStore(db).get_job(job_id).membership_removed_at is not None
        assert accounts.get_account(account).group_id is None

    def test_apply_remove_leaves_an_in_flight_job_alone(self, env):
        """A job already claimed (uploading) is never reported as stopped: its outcome is
        unknown, so cancellation is never claimed."""
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        job_id = JobStore(db).jobs_for_post(post_id)[0].id
        JobStore(db).claim(job_id)  # a worker already claimed it

        outcomes = service.apply_remove(account, group.id)

        assert outcomes[0].stopped is False
        assert "in progress" in outcomes[0].reason
        job = JobStore(db).get_job(job_id)
        assert job.status == "uploading" and job.membership_removed_at is None

    def test_apply_remove_leaves_a_published_job_alone_and_preserves_history(self, env):
        """A batch stays 'active' only while at least one of its jobs is unfinished; here the
        OTHER account's job is still pending, so this batch is examined, and the already-
        published account's own job/attempts must be left completely untouched."""
        db, accounts, video, _ = env
        published_account = make_account(accounts, "tiktok", "tt1", "u1")
        pending_account = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(published_account, group.id)
        service.assign(pending_account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        jobs = {j.account_id: j for j in JobStore(db).jobs_for_post(post_id)}
        JobStore(db).claim(jobs[published_account].id)
        attempt_id = JobStore(db).start_attempt(jobs[published_account].id)
        JobStore(db).update_attempt(attempt_id, "success", completed=True)
        JobStore(db).transition(jobs[published_account].id, "published", platform_media_id="TT1")

        outcomes = service.apply_remove(published_account, group.id)

        assert len(outcomes) == 1
        assert outcomes[0].stopped is False and "published" in outcomes[0].reason
        job = JobStore(db).get_job(jobs[published_account].id)
        assert job.status == "published" and job.platform_media_id == "TT1"
        assert len(JobStore(db).attempts(job.id)) >= 1  # attempt history untouched

    def test_apply_remove_with_no_active_batches_is_a_plain_unassign(self, env):
        service = build_service(env, {})
        group = service.create("AI Anime")
        account = make_account(env[1], "tiktok", "tt1", "u1")
        service.assign(account, group.id)

        outcomes = service.apply_remove(account, group.id)

        assert outcomes == []
        assert env[1].get_account(account).group_id is None


# =================================================================================================
# GroupService: delete-group reconciliation
# =================================================================================================

class TestDeleteReconciliation:
    def test_preview_delete_impact_counts_stoppable_and_in_flight_jobs(self, env):
        db, accounts, video, _ = env
        a1 = make_account(accounts, "tiktok", "tt1", "u1")
        a2 = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(a1, group.id)
        service.assign(a2, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        jobs = {j.account_id: j for j in JobStore(db).jobs_for_post(post_id)}
        JobStore(db).claim(jobs[a1].id)

        impact = service.preview_delete_impact(group.id)

        assert impact["active_batches"] == 1
        assert impact["stoppable_jobs"] == 1
        assert impact["in_flight_jobs"] == 1

    def test_delete_with_reconciliation_stops_jobs_and_preserves_origin_snapshot(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        job_id = JobStore(db).jobs_for_post(post_id)[0].id

        result = service.delete_with_reconciliation(group.id)

        assert result == {"stopped": 1, "left_in_flight": 0, "accounts_unassigned": 1}
        assert JobStore(db).get_job(job_id).membership_removed_at is not None
        assert service.origin(post_id) == (None, "AI Anime")  # FK SET NULL, snapshot kept
        assert accounts.get_account(account).status == "active"  # never disconnected


# =================================================================================================
# Restart / recovery and unchanged individual publishing
# =================================================================================================

class TestRestartRecovery:
    def test_removed_job_stays_blocked_after_a_fresh_engine_instance(self, env):
        """Simulates a process restart: a brand-new PublisherEngine/JobStore over the same
        database must never claim a previously-removed job (durable, not in-memory state)."""
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        service.apply_remove(account, group.id)

        fresh_engine = PublisherEngine(db, accounts, publishers={"tiktok": FakePublisher("tiktok")},
                                       sleep=lambda s: None, probe_media=False)
        results = fresh_engine.resume_open_jobs()

        assert post_id not in {r.post_id for r in results}  # nothing left to resume
        job = JobStore(db).jobs_for_post(post_id)[0]
        assert job.status == "pending" and job.membership_removed_at is not None


class TestIndividualPublishingUnaffected:
    def test_ordinary_post_publishes_normally_with_the_new_column_present(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        fake = FakePublisher("tiktok", PublishOutcome("published", "TT1", {}))
        engine = PublisherEngine(db, accounts, publishers={"tiktok": fake}, sleep=lambda s: None, probe_media=False)
        post_id = JobStore(db).create_post(video, "cap", [(account, {"privacy_level": "SELF_ONLY"})])

        result = engine.publish_post(post_id)

        assert result.jobs[0].status == "published"
        job = JobStore(db).jobs_for_post(post_id)[0]
        assert job.membership_removed_at is None


# =================================================================================================
# CLI integration: confirmation accepted/declined, impact reporting
# =================================================================================================

class TestGroupMenuMembershipCLI:
    def test_assign_account_screen_shows_available_and_occupied(self, env, capsys):
        db, accounts, video, _ = env
        free = make_account(accounts, "tiktok", "tt1", "u1")
        taken = make_account(accounts, "tiktok", "tt2", "u2")
        handler = handler_for(env, {})
        group = handler.groups.create("AI Anime")
        handler.groups.assign(taken, group.id)

        with patch("builtins.input", side_effect=[str(free), str(group.id), ""]), \
             patch("src.cli.group_menu.clear_screen"):
            handler.handle_assign_account()

        out = capsys.readouterr().out
        assert "AVAILABLE" in out
        assert "OCCUPIED -- AI Anime" in out

    def test_assign_occupied_account_to_a_different_group_is_rejected_with_a_clear_message(self, env, capsys):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        handler = handler_for(env, {})
        group_a = handler.groups.create("AI Anime")
        group_b = handler.groups.create("Motivation")
        handler.groups.assign(account, group_a.id)

        with patch("builtins.input", side_effect=[str(account), str(group_b.id), ""]), \
             patch("src.cli.group_menu.clear_screen"):
            handler.handle_assign_account()

        out = capsys.readouterr().out
        assert "already in 'AI Anime'" in out
        assert "unassign it first" in out.lower()
        assert accounts.get_account(account).group_id == group_a.id  # unchanged

    def test_assign_declines_confirmation_leaves_active_batch_unchanged(self, env):
        db, accounts, video, _ = env
        existing = make_account(accounts, "tiktok", "tt1", "u1")
        new_account = make_account(accounts, "tiktok", "tt2", "u2")
        handler = handler_for(env, {"tiktok": FakePublisher("tiktok")})
        group = handler.groups.create("AI Anime")
        handler.groups.assign(existing, group.id)
        plan = handler.groups.plan(group.id, video, "cap",
                                   platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = handler.groups.create_batch(group.id, plan)

        answers = [str(new_account), str(group.id), "n", ""]
        with patch("builtins.input", side_effect=answers), patch("src.cli.group_menu.clear_screen"):
            handler.handle_assign_account()

        assert accounts.get_account(new_account).group_id == group.id  # still assigned
        assert len(JobStore(db).jobs_for_post(post_id)) == 1  # no new job created

    def test_assign_confirms_creates_the_job(self, env):
        db, accounts, video, _ = env
        existing = make_account(accounts, "tiktok", "tt1", "u1")
        new_account = make_account(accounts, "tiktok", "tt2", "u2")
        handler = handler_for(env, {"tiktok": FakePublisher("tiktok")})
        group = handler.groups.create("AI Anime")
        handler.groups.assign(existing, group.id)
        plan = handler.groups.plan(group.id, video, "cap",
                                   platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = handler.groups.create_batch(group.id, plan)

        answers = [str(new_account), str(group.id), "y", ""]
        with patch("builtins.input", side_effect=answers), patch("src.cli.group_menu.clear_screen"):
            handler.handle_assign_account()

        assert len(JobStore(db).jobs_for_post(post_id)) == 2

    def test_unassign_reports_the_stopped_job(self, env, capsys):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        handler = handler_for(env, {"tiktok": FakePublisher("tiktok")})
        group = handler.groups.create("AI Anime")
        handler.groups.assign(account, group.id)
        plan = handler.groups.plan(group.id, video, "cap",
                                   platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        handler.groups.create_batch(group.id, plan)

        with patch("builtins.input", side_effect=[str(account), ""]), patch("src.cli.group_menu.clear_screen"):
            handler.handle_unassign_account()

        out = capsys.readouterr().out
        assert "stopped" in out.lower()
        assert accounts.get_account(account).group_id is None

    def test_delete_group_shows_impact_and_stops_jobs(self, env, capsys):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        handler = handler_for(env, {"tiktok": FakePublisher("tiktok")})
        group = handler.groups.create("AI Anime")
        handler.groups.assign(account, group.id)
        plan = handler.groups.plan(group.id, video, "cap",
                                   platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        handler.groups.create_batch(group.id, plan)

        with patch("builtins.input", side_effect=[str(group.id), "y", ""]), patch("src.cli.group_menu.clear_screen"):
            handler.handle_delete_group()

        out = capsys.readouterr().out
        assert "active batch" in out.lower()
        assert "stopped" in out.lower()

    def test_delete_group_handles_a_concurrent_deletion_gracefully(self, env, capsys):
        """Race: another window deletes the group between the existence check and the
        active-batch impact preview. Must print a graceful error, never propagate an uncaught
        exception out of the menu loop."""
        handler = handler_for(env, {})
        group = handler.groups.create("AI Anime")

        def vanished(group_id):
            raise GroupNotFoundError(f"Group with ID {group_id} not found")

        with patch.object(handler.groups, "preview_delete_impact", side_effect=vanished), \
             patch("builtins.input", side_effect=[str(group.id), ""]), \
             patch("src.cli.group_menu.clear_screen"):
            result = handler.handle_delete_group()

        assert result is True
        assert "not found" in capsys.readouterr().out.lower()


# =================================================================================================
# Reporting fix: a fully-reconciled batch must not appear perpetually active
# =================================================================================================

class TestReportingExcludesRemovedJobs:
    def test_batch_with_only_removed_jobs_is_reported_as_stopped(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        service.apply_remove(account, group.id)  # the batch's only job is now removed

        view = service.publishing.batch(post_id)

        assert view.status == "stopped"
        assert view.counts.get("pending", 0) == 1  # the job's literal status/history is untouched

    def test_batch_with_removed_and_active_jobs_is_still_reported_as_running(self, env):
        """The surviving account's job is actually in flight (claimed): real progress is
        happening, so the batch must still read "running", not "stopped" or "pending"."""
        db, accounts, video, _ = env
        removed_account = make_account(accounts, "tiktok", "tt1", "u1")
        active_account = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(removed_account, group.id)
        service.assign(active_account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        jobs = {j.account_id: j for j in JobStore(db).jobs_for_post(post_id)}
        JobStore(db).claim(jobs[active_account].id)
        service.apply_remove(removed_account, group.id)

        view = service.publishing.batch(post_id)

        assert view.status == "running"

    def test_batch_with_removed_and_untouched_pending_job_is_still_pending(self, env):
        """Neither surviving job has actually started yet: nothing has run, so the batch still
        reads "pending" (there is still something to do), not "stopped"."""
        db, accounts, video, _ = env
        removed_account = make_account(accounts, "tiktok", "tt1", "u1")
        waiting_account = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(removed_account, group.id)
        service.assign(waiting_account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        service.apply_remove(removed_account, group.id)

        view = service.publishing.batch(post_id)

        assert view.status == "pending"

    def test_batch_with_removed_and_published_jobs_is_reported_as_completed(self, env):
        db, accounts, video, _ = env
        removed_account = make_account(accounts, "tiktok", "tt1", "u1")
        published_account = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(removed_account, group.id)
        service.assign(published_account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        jobs = {j.account_id: j for j in JobStore(db).jobs_for_post(post_id)}
        JobStore(db).claim(jobs[published_account].id)
        JobStore(db).transition(jobs[published_account].id, "published", platform_media_id="TT1")
        service.apply_remove(removed_account, group.id)

        view = service.publishing.batch(post_id)

        assert view.status == "completed"

    def test_queue_counts_excludes_removed_jobs_but_keeps_an_active_sibling_pending(self, env):
        db, accounts, video, _ = env
        removed_account = make_account(accounts, "tiktok", "tt1", "u1")
        active_account = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(removed_account, group.id)
        service.assign(active_account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        service.create_batch(group.id, plan)
        service.apply_remove(removed_account, group.id)

        counts = service.publishing.queue_counts()

        assert counts["removed"] == 1
        assert counts["pending"] == 1  # only the still-active account's job counts as pending

    def test_queue_counts_removed_only_batch_has_zero_pending(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        service.create_batch(group.id, plan)
        service.apply_remove(account, group.id)

        counts = service.publishing.queue_counts()

        assert counts["pending"] == 0
        assert counts["removed"] == 1


class TestFullyRemovedBatchesAreNoLongerActive:
    """The root-cause fix: _active_batches() must stop treating a fully-reconciled (all jobs
    removed) batch as active, so it is never re-examined by preview_add/apply_remove/
    preview_delete_impact."""

    def test_preview_add_ignores_a_fully_removed_batch(self, env):
        db, accounts, video, _ = env
        removed_account = make_account(accounts, "tiktok", "tt1", "u1")
        other_account = make_account(accounts, "tiktok", "tt2", "u2")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(removed_account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        service.create_batch(group.id, plan)
        service.apply_remove(removed_account, group.id)  # the batch's only job is now removed

        impacts = service.preview_add(other_account, group.id)

        assert impacts == []  # the fully-reconciled batch no longer counts as "active"

    def test_preview_delete_impact_excludes_a_fully_removed_batch(self, env):
        db, accounts, video, _ = env
        account = make_account(accounts, "tiktok", "tt1", "u1")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        service.create_batch(group.id, plan)
        service.apply_remove(account, group.id)

        impact = service.preview_delete_impact(group.id)

        assert impact["active_batches"] == 0
        assert impact["stoppable_jobs"] == 0
        assert impact["in_flight_jobs"] == 0

    def test_preview_add_still_sees_a_batch_with_one_removed_and_one_active_job(self, env):
        """Regression: the fix must not become overzealous and hide a batch that genuinely still
        has other active work."""
        db, accounts, video, _ = env
        removed_account = make_account(accounts, "tiktok", "tt1", "u1")
        active_account = make_account(accounts, "tiktok", "tt2", "u2")
        new_account = make_account(accounts, "tiktok", "tt3", "u3")
        service = build_service(env, {"tiktok": FakePublisher("tiktok")})
        group = service.create("AI Anime")
        service.assign(removed_account, group.id)
        service.assign(active_account, group.id)
        plan = service.plan(group.id, video, "cap", platform_options={"tiktok": {"privacy_level": "SELF_ONLY"}})
        post_id = service.create_batch(group.id, plan)
        service.apply_remove(removed_account, group.id)  # active_account's job is still pending

        impacts = service.preview_add(new_account, group.id)

        assert len(impacts) == 1 and impacts[0].post_id == post_id and impacts[0].action == "new_job"
