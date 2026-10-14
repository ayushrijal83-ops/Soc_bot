"""Group publishing service: the UI's way to manage groups and to plan/create/run a publishing
batch from a group's accounts.

Thin layer over GroupManager (membership) + PublishingService (the frozen engine's own service
layer): it never talks to a platform, a tunnel or a worker pool itself, and it never creates a
second job-creation or publishing path. Planning, batch creation and running a group's batch
reuse PublishingService.plan / create_batch / publish_batch exactly as individual-account
publishing does; the only new behavior is recording which group a batch came from.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.accounts.groups import GroupError, GroupManager, GroupNotFoundError
from src.core.publisher import batch_status
from src.services.publishing import BatchPlan, BatchView, PublishingService
from src.storage.database import Account, Post

__all__ = ["AddImpact", "GroupError", "GroupNotFoundError", "GroupService", "GroupView", "RemoveOutcome"]

ACTIVE_BATCH_STATUSES = ("pending", "running")  # batch_status() values meaning "not finished yet"
NOT_YET_STARTED = ("pending", "retrying")       # job statuses a worker has not claimed yet
IN_FLIGHT = ("uploading", "processing")          # job statuses already claimed: outcome unknown


@dataclass
class GroupView:
    id: int
    name: str
    account_count: int


@dataclass
class AddImpact:
    """One active batch of a group that adding an account would affect (read-only preview)."""
    post_id: int
    batch_status: str
    action: str   # "new_job" (a fresh job would be created) | "restore" (undo an earlier removal)


@dataclass
class RemoveOutcome:
    """What happened to one active batch's job when an account was removed from its group."""
    post_id: int
    job_id: int
    job_status: str
    stopped: bool   # True: the not-yet-started job is now blocked from ever being claimed
    reason: str     # human text, always truthful about what is actually known


class GroupService:
    def __init__(self, group_manager: GroupManager, publishing: PublishingService):
        self.groups = group_manager
        self.publishing = publishing

    # --- group CRUD (token-free; passes straight through to GroupManager) ----------------

    def list(self) -> list[GroupView]:
        return [self._view(g) for g in self.groups.list_groups()]

    def _view(self, group) -> GroupView:
        return GroupView(group.id, group.name, len(self.groups.accounts_in(group.id)))

    def create(self, name: str) -> GroupView:
        return self._view(self.groups.create_group(name))

    def rename(self, group_id: int, new_name: str) -> GroupView:
        return self._view(self.groups.rename_group(group_id, new_name))

    def delete(self, group_id: int) -> int:
        """Delete a group. Returns the number of accounts unassigned by the deletion."""
        return self.groups.delete_group(group_id)

    def assign(self, account_id: int, group_id: int):
        return self.groups.assign_account(account_id, group_id)

    def unassign(self, account_id: int):
        return self.groups.unassign_account(account_id)

    def accounts_in(self, group_id: int):
        return self.groups.accounts_in(group_id)

    # --- live membership: active batches of a group ---------------------------------------

    @staticmethod
    def _status_of(jobs) -> str:
        """batch_status(), correctly aware of membership-removed jobs: a pending/retrying job
        whose account was removed from its group will never be claimed, so it must never keep a
        fully-reconciled batch looking "pending"/"running" forever."""
        removed = sum(1 for j in jobs if j.status in ("pending", "retrying") and j.membership_removed_at)
        return batch_status([j.status for j in jobs], removed)

    def _active_batches(self, group_id: int) -> list[int]:
        """post_ids of this group's batches that still have unfinished work (``_status_of`` is
        "pending" or "running"). A batch whose only remaining jobs were all removed from the
        group is "stopped" (see ``_status_of``/``batch_status``), not active: it is excluded
        here, so group-membership impacts are never computed against work that will never
        happen. Read-only."""
        with self.publishing.store.database.session() as session:
            post_ids = [p[0] for p in session.query(Post.id).filter(Post.origin_group_id == group_id)]
        store = self.publishing.store
        return [pid for pid in post_ids
                if self._status_of(store.jobs_for_post(pid)) in ACTIVE_BATCH_STATUSES]

    def preview_add(self, account_id: int, group_id: int) -> list[AddImpact]:
        """Batches of this group that assigning this account would affect, without changing
        anything. Empty list = safe to assign without any confirmation.

        "restore" is offered for ANY of the group's batches where this account already has a
        removed job -- including one that is otherwise fully "stopped" (every job removed):
        restoring your own just-removed job is not the same as joining a batch as a brand-new
        destination, so a stopped batch is not excluded from this case. "new_job" is only offered
        for batches that are still genuinely active (pending/running): creating a brand-new
        destination in an already-finished or fully-stopped batch would never do anything.
        """
        self.groups.get_group(group_id)  # existence check (raises GroupNotFoundError)
        self.groups.check_available(account_id, group_id)  # AccountAlreadyAssignedError if occupied
        store = self.publishing.store
        with self.publishing.store.database.session() as session:
            all_post_ids = [p[0] for p in session.query(Post.id).filter(Post.origin_group_id == group_id)]
        active_post_ids = set(self._active_batches(group_id))
        impacts = []
        for post_id in all_post_ids:
            jobs = store.jobs_for_post(post_id)
            existing = next((j for j in jobs if j.account_id == account_id), None)
            status = self._status_of(jobs)
            if existing is not None and existing.membership_removed_at is not None:
                impacts.append(AddImpact(post_id, status, "restore"))
            elif existing is None and post_id in active_post_ids:
                impacts.append(AddImpact(post_id, status, "new_job"))
            # else: this account already has an ordinary (never removed) job here, or the batch
            # has no removed job for this account and is not active either; nothing to do.
        return impacts

    def apply_add(self, account_id: int, group_id: int, impacts: list[AddImpact]) -> dict[int, str]:
        """Create or restore this account's job for each given impact (caller must already have
        confirmed), then assign the account to the group. A brand-new job copies the options of
        an existing same-platform destination in that batch (the same options every account of
        that platform in the group was given); if the batch has none, it is skipped rather than
        guessing options. Returns {post_id: outcome}.

        Assignment happens FIRST: if the account already belongs to a different group,
        assign_account rejects it before any job is created or restored, so a rejection never
        leaves a partially-applied job behind.
        """
        self.groups.get_group(group_id)
        self.groups.assign_account(account_id, group_id)
        store = self.publishing.store
        results: dict[int, str] = {}
        for impact in impacts:
            jobs = store.jobs_for_post(impact.post_id)
            existing = next((j for j in jobs if j.account_id == account_id), None)
            if existing is not None and existing.membership_removed_at is not None:
                store.restore_membership(existing.id)
                results[impact.post_id] = "restored"
            elif existing is not None:
                results[impact.post_id] = "already present"
            else:
                target_platform = self._platform_of(account_id)
                same_platform = next((j for j in jobs if j.platform == target_platform), None)
                if same_platform is None:
                    results[impact.post_id] = (f"skipped: no existing {target_platform} destination "
                                               "in this batch to copy options from")
                    continue
                created = store.add_missing_jobs(impact.post_id, [(account_id, same_platform.options)])
                results[impact.post_id] = "created" if created else "already present"
        return results

    def _platform_of(self, account_id: int) -> str:
        with self.publishing.store.database.session() as session:
            account = session.get(Account, account_id)
            if account is None:
                raise GroupError(f"Account {account_id} not found")
            return account.platform

    def apply_remove(self, account_id: int, group_id: int) -> list[RemoveOutcome]:
        """Stop this account's future work in every active batch of this group where safely
        possible, then unassign it from the group. A job already claimed by a worker
        (uploading/processing) is left alone -- its outcome is unknown, so cancellation is never
        claimed. Completed jobs, attempts and history are never touched."""
        self.groups.get_group(group_id)
        store = self.publishing.store
        outcomes = []
        for post_id in self._active_batches(group_id):
            jobs = store.jobs_for_post(post_id)
            job = next((j for j in jobs if j.account_id == account_id), None)
            if job is None:
                continue
            outcomes.append(self._stop_job(post_id, job, store))
        self.groups.unassign_account(account_id)
        return outcomes

    @staticmethod
    def _stop_job(post_id: int, job, store) -> RemoveOutcome:
        if job.status in NOT_YET_STARTED and job.membership_removed_at is None:
            stopped = store.remove_membership(job.id)
            reason = ("stopped before it started" if stopped else
                      "claimed by a worker just before it could be stopped; left to finish (outcome unknown)")
        elif job.status in NOT_YET_STARTED:  # membership_removed_at already set
            stopped, reason = True, "already stopped"
        elif job.status in IN_FLIGHT:
            stopped, reason = False, "already in progress; outcome unknown, left to finish"
        elif job.status == "published":
            stopped, reason = False, "already published"
        else:  # failed
            stopped, reason = False, "already failed"
        return RemoveOutcome(post_id, job.id, job.status, stopped, reason)

    def preview_delete_impact(self, group_id: int) -> dict:
        """Read-only: how many active batches, stoppable (not-yet-started) jobs and in-flight
        (outcome-unknown) jobs deleting this group would affect."""
        group = self.groups.get_group(group_id)
        store = self.publishing.store
        post_ids = self._active_batches(group_id)
        stoppable = in_flight = 0
        for post_id in post_ids:
            for job in store.jobs_for_post(post_id):
                if job.status in NOT_YET_STARTED and job.membership_removed_at is None:
                    stoppable += 1
                elif job.status in IN_FLIGHT:
                    in_flight += 1
        return {"group": group, "active_batches": len(post_ids), "stoppable_jobs": stoppable,
                "in_flight_jobs": in_flight}

    def delete_with_reconciliation(self, group_id: int) -> dict:
        """Stop every cancellable (not-yet-started) job of this group's active batches, then
        delete the group exactly as GroupManager.delete_group does: accounts are unassigned, never
        disconnected; each post's origin_group_name snapshot is a plain column, untouched by the
        group's deletion (origin_group_id alone follows ON DELETE SET NULL)."""
        store = self.publishing.store
        stopped = left_in_flight = 0
        for post_id in self._active_batches(group_id):
            for job in store.jobs_for_post(post_id):
                if job.status in NOT_YET_STARTED and job.membership_removed_at is None:
                    if store.remove_membership(job.id):
                        stopped += 1
                    else:
                        left_in_flight += 1  # lost the race to a worker claim
                elif job.status in IN_FLIGHT:
                    left_in_flight += 1
        unassigned = self.groups.delete_group(group_id)
        return {"stopped": stopped, "left_in_flight": left_in_flight, "accounts_unassigned": unassigned}

    # --- group publishing: reuses PublishingService end to end ---------------------------

    def plan(self, group_id: int, video_path: str, caption: str, cover_path: str | None = None,
             platform_options: dict[str, dict] | None = None) -> BatchPlan:
        """Preflight every account currently in the group: same video, same caption, for all of
        them. No network, no jobs created (PublishingService.plan is read-only)."""
        accounts = self.groups.accounts_in(group_id)  # raises GroupNotFoundError if the group is gone
        if not accounts:
            raise GroupError("This group has no accounts; nothing to preflight.")
        account_ids = [a.id for a in accounts]
        return self.publishing.plan(video_path, caption, cover_path, account_ids, platform_options)

    def create_batch(self, group_id: int, plan: BatchPlan, include_already_published: bool = False) -> int:
        """Create the batch. PublishingService.create_batch already only uses ``plan.valid``
        (eligible) destinations, so blocked accounts never get a job. The group's current name is
        snapshotted onto the post, so the origin survives a later rename or delete of the group.
        """
        group = self.groups.get_group(group_id)  # fresh existence/name check at submission time
        post_id = self.publishing.create_batch(plan, include_already_published)
        self._attach_origin(post_id, group.id, group.name)
        return post_id

    def _attach_origin(self, post_id: int, group_id: int, group_name: str) -> None:
        """Record the originating group on the post, once (mirrors AudienceStore.attach's
        snapshot-on-creation pattern)."""
        with self.publishing.store.database.session() as session:
            post = session.get(Post, post_id)
            post.origin_group_id = group_id
            post.origin_group_name = group_name
            session.commit()

    def publish(self, post_id: int, on_event=None) -> BatchView:
        """Run the batch (PublishingService.publish_batch: publishing lock, retries, attempt
        records and link capture are all inherited, unchanged)."""
        return self.publishing.publish_batch(post_id, on_event)

    def origin(self, post_id: int) -> tuple[int | None, str | None]:
        """(group_id, group_name) this batch was created from, or (None, None) for an ordinary
        (non-group) post. ``group_name`` is the snapshot taken at creation, so it is correct even
        if the group was since renamed or deleted."""
        with self.publishing.store.database.session() as session:
            post = session.get(Post, post_id)
            if post is None:
                return None, None
            return post.origin_group_id, post.origin_group_name
