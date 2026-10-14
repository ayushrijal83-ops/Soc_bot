"""Account Groups submenu for the classic CLI: group management and group publishing.

Group CRUD and membership go through GroupService -> GroupManager directly: no tokens, no
network, no worker thread needed. Group publishing reuses PublishingService end to end (via
GroupService.plan / create_batch / publish), so a group's batch is the exact same kind of batch
and independent per-account jobs individual-account publishing already creates.
"""

from src.accounts.groups import DuplicateGroupError, GroupError, GroupNotFoundError
from src.accounts.manager import AccountNotFoundError
from src.cli.display import (
    clear_screen,
    confirm,
    print_error,
    print_header,
    print_info,
    print_menu,
    print_success,
    print_table,
    print_warning,
)
from src.cli.prompts import prompt_int, prompt_menu_selection, prompt_text
from src.cli.publish_menu import _ask_cover, _ask_options, print_plan
from src.core.publish_lock import BUSY_MESSAGE, PublishLockBusy
from src.core.validation import validate_video_file
from src.services.groups import GroupService


class GroupMenuHandler:
    """Handles the Account Groups submenu navigation and actions."""

    def __init__(self, group_service: GroupService, account_manager):
        self.groups = group_service
        self.account_manager = account_manager
        self.running = True
        self.menu_options = [
            "List Groups",
            "Create Group",
            "Rename Group",
            "Delete Group",
            "Assign Account to Group",
            "Unassign Account from Group",
            "Publish to Group",
            "Back to Main Menu",
        ]

    def show_menu(self) -> None:
        clear_screen()
        print_menu(self.menu_options, "ACCOUNT GROUPS")

    def handle_choice(self, choice: int) -> bool:
        handlers = {
            1: self.handle_list_groups,
            2: self.handle_create_group,
            3: self.handle_rename_group,
            4: self.handle_delete_group,
            5: self.handle_assign_account,
            6: self.handle_unassign_account,
            7: self.handle_publish_to_group,
            8: self.handle_back,
        }
        handler = handlers.get(choice)
        return handler() if handler else True

    # --- group CRUD ------------------------------------------------------------------

    def handle_list_groups(self) -> bool:
        clear_screen()
        print_header("GROUPS")
        self._print_groups()
        self._pause()
        return True

    def handle_create_group(self) -> bool:
        clear_screen()
        print_header("CREATE GROUP")
        name = prompt_text("Group name (e.g. AI Anime)")
        if not name:
            return True
        try:
            group = self.groups.create(name)
            print_success(f"Group '{group.name}' created (ID {group.id}).")
        except (GroupError, DuplicateGroupError) as e:
            print_error(str(e))
        self._pause()
        return True

    def handle_rename_group(self) -> bool:
        clear_screen()
        print_header("RENAME GROUP")
        self._print_groups()
        group_id = prompt_int("Group ID", min_val=1)
        if group_id is None:
            return True
        new_name = prompt_text("New name")
        if not new_name:
            return True
        try:
            group = self.groups.rename(group_id, new_name)
            print_success(f"Group renamed to '{group.name}'.")
        except (GroupNotFoundError, DuplicateGroupError, GroupError) as e:
            print_error(str(e))
        self._pause()
        return True

    def handle_delete_group(self) -> bool:
        clear_screen()
        print_header("DELETE GROUP")
        print_warning("Accounts in this group stay connected; they only become unassigned.")
        print_warning("Batches already published from this group keep their record of it.")
        self._print_groups()
        group_id = prompt_int("Group ID", min_val=1)
        if group_id is None:
            return True
        try:
            group = self.groups.groups.get_group(group_id)
            impact = self.groups.preview_delete_impact(group_id)
        except GroupNotFoundError as e:
            print_error(str(e))
            self._pause()
            return True
        if impact["active_batches"]:
            print_warning(f"{impact['active_batches']} active batch(es) use this group: "
                          f"{impact['stoppable_jobs']} not-yet-started job(s) will be stopped, "
                          f"{impact['in_flight_jobs']} in-progress job(s) will be left to finish "
                          "(their outcome is unknown, so they are never cancelled here).")
        if not confirm(f"Delete group '{group.name}'?", default=False):
            return True
        try:
            result = self.groups.delete_with_reconciliation(group_id)
            print_success(f"Group '{group.name}' deleted. {result['accounts_unassigned']} account(s) "
                          f"unassigned, {result['stopped']} not-yet-started job(s) stopped, "
                          f"{result['left_in_flight']} left in progress.")
        except GroupNotFoundError as e:
            print_error(str(e))
        self._pause()
        return True

    # --- membership --------------------------------------------------------------------

    def handle_assign_account(self) -> bool:
        clear_screen()
        print_header("ASSIGN ACCOUNT TO GROUP")
        self._print_accounts()
        account_id = prompt_int("Account ID", min_val=1)
        if account_id is None:
            return True
        self._print_groups()
        group_id = prompt_int("Group ID", min_val=1)
        if group_id is None:
            return True
        try:
            impacts = self.groups.preview_add(account_id, group_id)
        except GroupError as e:
            # Covers: group/account not found, and an account already OCCUPIED by a different
            # group (rejected here, before anything else happens; the clear explanation is
            # str(e), e.g. "Account #5 is already in 'AI Anime'. Unassign it first...").
            print_error(str(e))
            self._pause()
            return True
        if not impacts:
            try:
                account = self.groups.assign(account_id, group_id)
                print_success(f"Account #{account.id} assigned to group #{group_id}.")
            except (GroupError, GroupNotFoundError) as e:
                print_error(str(e))
            self._pause()
            return True

        print_warning(f"This group has {len(impacts)} active batch(es). Adding this account now would:")
        for impact in impacts:
            verb = "create a new job in" if impact.action == "new_job" else "restore its stopped job in"
            print_info(f"  {verb} batch #{impact.post_id} ({impact.batch_status})")
        if confirm(f"Create/restore this account's job in {len(impacts)} active batch(es)?", default=False):
            try:
                results = self.groups.apply_add(account_id, group_id, impacts)
            except GroupError as e:
                print_error(str(e))
                self._pause()
                return True
            for post_id, outcome in results.items():
                print_info(f"  Batch #{post_id}: {outcome}")
            print_success(f"Account #{account_id} assigned to group #{group_id}.")
        else:
            try:
                account = self.groups.assign(account_id, group_id)
                print_info(f"Account #{account.id} assigned to group #{group_id}. "
                           f"The {len(impacts)} active batch(es) were left unchanged.")
            except (GroupError, GroupNotFoundError) as e:
                print_error(str(e))
        self._pause()
        return True

    def handle_unassign_account(self) -> bool:
        clear_screen()
        print_header("UNASSIGN ACCOUNT FROM GROUP")
        self._print_accounts()
        account_id = prompt_int("Account ID", min_val=1)
        if account_id is None:
            return True
        try:
            account = self.account_manager.get_account(account_id)
        except AccountNotFoundError as e:
            print_error(str(e))
            self._pause()
            return True
        if account.group_id is None:
            print_info("This account is not in a group.")
            self._pause()
            return True
        try:
            outcomes = self.groups.apply_remove(account_id, account.group_id)
        except GroupError as e:
            print_error(str(e))
            self._pause()
            return True
        if outcomes:
            print_info(f"Stopping future work in {len(outcomes)} active batch(es):")
            for o in outcomes:
                mark = "stopped" if o.stopped else "left running"
                print_info(f"  Batch #{o.post_id} job #{o.job_id} ({o.job_status}): {mark} -- {o.reason}")
        print_success(f"Account #{account_id} unassigned (still connected).")
        self._pause()
        return True

    def _print_groups(self) -> None:
        groups = self.groups.list()
        if not groups:
            print_info("No groups yet.")
            return
        print_table(["ID", "Name", "Accounts"], [[str(g.id), g.name, str(g.account_count)] for g in groups])

    def _print_accounts(self) -> None:
        accounts = self.account_manager.list_accounts()
        if not accounts:
            print_info("No connected accounts.")
            return
        group_names = {g.id: g.name for g in self.groups.list()}
        rows = []
        for a in accounts:
            occupancy = f"OCCUPIED -- {group_names.get(a.group_id, f'#{a.group_id}')}" if a.group_id else "AVAILABLE"
            rows.append([str(a.id), a.platform.title(), a.display_name or a.username, occupancy])
        print_table(["ID", "Platform", "Account", "Group Status"], rows)

    # --- group publishing ----------------------------------------------------------------

    def handle_publish_to_group(self) -> bool:
        clear_screen()
        print_header("PUBLISH TO GROUP")
        self._print_groups()
        group_id = prompt_int("Group ID", min_val=1)
        if group_id is None:
            return True

        try:
            accounts = self.groups.accounts_in(group_id)
        except GroupNotFoundError as e:
            print_error(str(e))
            self._pause()
            return True
        if not accounts:
            print_error("This group has no accounts. Assign accounts to it first.")
            self._pause()
            return True
        print_info(f"Group has {len(accounts)} account(s): "
                   + ", ".join(f"{a.platform}/{a.display_name or a.username}" for a in accounts))

        video_path = prompt_text("Video file path")
        if not video_path:
            return True
        check = validate_video_file(video_path.strip().strip('"'))
        for warning in check.warnings:
            print_warning(warning)
        if not check.ok:
            for error in check.errors:
                print_error(error)
            self._pause()
            return True
        video_path = check.media.path

        cover = _ask_cover()
        if cover is False:
            return True

        caption = prompt_text("Caption", required=False, default="") or ""

        # Same per-platform options for every account of that platform in the group (the same
        # video and caption already go to all of them; options follow the same "one choice per
        # platform for the whole group" rule).
        platform_options: dict[str, dict] = {}
        for platform in sorted({a.platform for a in accounts}):
            options = _ask_options(platform, platform.title(), caption, check.media.size_bytes)
            if options is None:
                print_info("Cancelled. Nothing was published.")
                return True
            platform_options[platform] = options

        try:
            plan = self.groups.plan(group_id, video_path, caption, cover, platform_options)
        except GroupError as e:
            print_error(str(e))
            self._pause()
            return True

        print_plan(plan.destinations)
        ready, blocked = plan.valid, plan.invalid
        print_info(f"Eligible: {len(ready)}   Blocked: {len(blocked)}")
        if not ready:
            print_error("No eligible accounts in this group. Fix the problems above.")
            self._pause()
            return True
        if not confirm(f"Publish to {len(ready)} eligible account(s)? "
                       f"({len(blocked)} blocked account(s) will be skipped)", default=False):
            print_info("Cancelled. Nothing was published.")
            return True

        try:
            post_id = self.groups.create_batch(group_id, plan)
        except GroupNotFoundError as e:
            print_error(f"Could not create the batch: {e}")
            self._pause()
            return True

        print_info(f"Publishing batch #{post_id}...")
        try:
            view = self.groups.publish(post_id, on_event=_print_batch_event)
        except PublishLockBusy:
            print_error(BUSY_MESSAGE + " Nothing further was created.")
            self._pause()
            return True
        self._print_group_batch_result(view)
        self._pause()
        return True

    def _print_group_batch_result(self, view) -> None:
        group_id, group_name = self.groups.origin(view.post_id)
        print_header("GROUP PUBLISH RESULT")
        print_info(f"Group: {group_name or '(unknown)'}   Batch: #{view.post_id}   Status: {view.status.upper()}")
        for job in view.jobs:
            line = f"  {job.platform:<10} {job.account_label:<24} {job.status.upper()}"
            if job.status == "published":
                line += f"  {job.url}" if job.url else "  (published; no public link available yet)"
            elif job.error:
                line += f"  ({job.error})"
            print(line)

    def handle_back(self) -> bool:
        return False

    def _pause(self) -> None:
        try:
            input("\nPress Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            print()

    def run(self) -> None:
        while self.running:
            self.show_menu()
            choice = prompt_menu_selection(self.menu_options, "Select an option")
            if choice is None:
                break
            self.running = self.handle_choice(choice)


def _print_batch_event(event) -> None:
    """Per-job progress line as the batch runs (BatchEvent from PublishingService.publish_batch)."""
    if event.job_id is None:
        return
    line = f"  {(event.platform or ''):<10} {(event.account or ''):<24} {(event.status or '').upper()}"
    if event.message:
        line += f"  ({event.message})"
    print(line)


def run_group_menu(group_service: GroupService, account_manager) -> None:
    """Entry point for running the Account Groups menu."""
    handler = GroupMenuHandler(group_service, account_manager)
    handler.run()
