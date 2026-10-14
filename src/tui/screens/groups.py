"""Account Groups: list/create/rename/delete groups and manage account membership.

Presentation only: every action goes through src.services.groups (GroupService), the exact same
service the classic CLI (src/cli/group_menu.py) already uses. No business logic lives here.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from src.accounts.groups import AccountAlreadyAssignedError, GroupError, GroupNotFoundError
from src.tui.widgets import ConfirmModal, MessageModal, Page


class CreateGroupModal(ModalScreen[str | None]):
    """Prompt for a new group's name."""

    BINDINGS: ClassVar[list] = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("Create Group", classes="modal-title")
            yield Input(placeholder="Group name (e.g. AI Anime)", id="name")
            with Horizontal(classes="buttons"):
                yield Button("Create", variant="success", id="create")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            self.dismiss(self.query_one("#name", Input).value.strip() or None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RenameGroupModal(ModalScreen[str | None]):
    """Prompt for a group's new name, prefilled with its current name."""

    BINDINGS: ClassVar[list] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current_name: str):
        super().__init__()
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("Rename Group", classes="modal-title")
            yield Input(value=self.current_name, id="name")
            with Horizontal(classes="buttons"):
                yield Button("Rename", variant="success", id="rename")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        field = self.query_one("#name", Input)
        field.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename":
            self.dismiss(self.query_one("#name", Input).value.strip() or None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AssignAccountModal(ModalScreen[int | None]):
    """Pick an account to assign to this group. Shows AVAILABLE / OCCUPIED -- <group name>, the
    same wording as the classic CLI, for every connected account."""

    BINDINGS: ClassVar[list] = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = "#assign-picker { height: 80%; } #assign-accounts { height: 1fr; }"

    def __init__(self, group_id: int):
        super().__init__()
        self.group_id = group_id

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="assign-picker"):
            yield Label("Assign Account to Group", classes="modal-title")
            yield DataTable(id="assign-accounts", cursor_type="row", zebra_stripes=True)
            with Horizontal(classes="buttons"):
                yield Button("Assign", variant="success", id="assign")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        table = self.query_one("#assign-accounts", DataTable)
        table.add_columns("Account", "Platform", "Group Status")
        services = self.app.services
        group_names = {g.id: g.name for g in services.groups.list()}
        for account in services.account_manager.list_accounts():
            occupancy = (f"OCCUPIED -- {group_names.get(account.group_id, '#' + str(account.group_id))}"
                        if account.group_id else "AVAILABLE")
            table.add_row(account.display_name or account.username, account.platform.title(),
                         occupancy, key=str(account.id))
        if table.row_count == 0:
            table.add_row("No connected accounts", "", "", key="none")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "assign":
            table = self.query_one("#assign-accounts", DataTable)
            if table.row_count == 0:
                self.dismiss(None)
                return
            key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
            self.dismiss(None if key == "none" else int(key))
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class GroupDetailScreen(Screen):
    """One group's members: assign / unassign accounts. Unassigning never disconnects the
    account (GroupService.unassign -> GroupManager.unassign_account)."""

    BINDINGS: ClassVar[list] = [Binding("escape", "leave", "Back")]

    def __init__(self, group_id: int, group_name: str):
        super().__init__()
        self.group_id = group_id
        self.group_name = group_name

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="content"):
            yield Static(f"Group: {self.group_name}", classes="title", id="group-title")
            yield DataTable(id="members", cursor_type="row", zebra_stripes=True)
            with Horizontal(classes="buttons"):
                yield Button("Assign Account", variant="primary", id="assign")
                yield Button("Unassign Selected", variant="error", id="unassign")
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#members", DataTable).add_columns("Account", "Platform", "Status")
        self.refresh_members()

    def refresh_members(self) -> None:
        table = self.query_one("#members", DataTable)
        table.clear()
        try:
            accounts = self.app.services.groups.accounts_in(self.group_id)
        except GroupNotFoundError:
            accounts = []
        for a in accounts:
            table.add_row(a.display_name or a.username, a.platform.title(), a.status.title(), key=str(a.id))
        if table.row_count == 0:
            table.add_row("No accounts in this group yet", "", "", key="none")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "assign":
            self.app.push_screen(AssignAccountModal(self.group_id), self._assign)
        elif event.button.id == "unassign":
            table = self.query_one("#members", DataTable)
            if table.row_count == 0:
                return
            key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
            if key == "none":
                return
            self.app.services.groups.unassign(int(key))
            self.refresh_members()
            self.app.notify("Account unassigned (still connected).")

    def _assign(self, account_id: int | None) -> None:
        if account_id is None:
            return
        try:
            self.app.services.groups.assign(account_id, self.group_id)
            self.refresh_members()
            self.app.notify("Account assigned.")
        except AccountAlreadyAssignedError as e:
            self.app.push_screen(MessageModal("Already in a group", str(e)))
        except GroupError as e:
            self.app.push_screen(MessageModal("Could not assign", str(e)))

    def action_leave(self) -> None:
        self.app.pop_screen()


class GroupsScreen(Page):
    """List / search / create / rename / delete groups. Deleting a group never disconnects or
    deletes its accounts -- they just become unassigned (GroupService.delete_with_reconciliation,
    the same safe path the classic CLI uses, including the active-batch impact warning)."""

    MODE = "groups"

    def body(self) -> ComposeResult:
        yield Static("Account Groups", classes="title")
        with Horizontal(classes="row"):
            yield Input(placeholder="/ Search groups", id="search")
        yield DataTable(id="groups", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="buttons"):
            yield Button("Create Group", variant="primary", id="create")
            yield Button("Rename", id="rename")
            yield Button("Delete", variant="error", id="delete")
            yield Button("Open", id="open")

    def on_mount(self) -> None:
        self.query_one("#groups", DataTable).add_columns("Name", "Accounts")
        self.refresh_data()

    def refresh_data(self) -> None:
        table = self.query_one("#groups", DataTable)
        table.clear()
        needle = self.query_one("#search", Input).value.strip().lower()
        for g in self.app.services.groups.list():
            if needle and needle not in g.name.lower():
                continue
            table.add_row(g.name, str(g.account_count), key=str(g.id))
        if table.row_count == 0:
            table.add_row("No groups yet. Press Create Group.", "", key="none")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.refresh_data()

    def current(self) -> tuple[int | None, str | None]:
        table = self.query_one("#groups", DataTable)
        if table.row_count == 0:
            return None, None
        key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        if key == "none":
            return None, None
        group_id = int(key)
        name = next((g.name for g in self.app.services.groups.list() if g.id == group_id), "")
        return group_id, name

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            self.app.push_screen(CreateGroupModal(), self._create)
            return
        group_id, name = self.current()
        if group_id is None:
            self.app.notify("Select a group first.", severity="warning")
            return
        if event.button.id == "open":
            self.app.push_screen(GroupDetailScreen(group_id, name))
        elif event.button.id == "rename":
            self.app.push_screen(RenameGroupModal(name), lambda new: self._rename(group_id, new))
        elif event.button.id == "delete":
            impact = self.app.services.groups.preview_delete_impact(group_id)
            message = f"Delete '{name}'? Its accounts stay connected and become unassigned."
            if impact["active_batches"]:
                message += (f"\n\n{impact['active_batches']} active batch(es) use this group: "
                           f"{impact['stoppable_jobs']} not-yet-started job(s) will be stopped, "
                           f"{impact['in_flight_jobs']} in-progress job(s) left to finish.")
            self.app.push_screen(ConfirmModal("Delete group?", message, "Delete", danger=True),
                                 lambda ok: self._delete(ok, group_id, name))

    def _create(self, name: str | None) -> None:
        if not name:
            return
        try:
            self.app.services.groups.create(name)
            self.refresh_data()
            self.app.notify(f"Group '{name}' created.")
        except GroupError as e:
            self.app.push_screen(MessageModal("Could not create group", str(e)))

    def _rename(self, group_id: int, new_name: str | None) -> None:
        if not new_name:
            return
        try:
            self.app.services.groups.rename(group_id, new_name)
            self.refresh_data()
            self.app.notify("Group renamed.")
        except GroupError as e:
            self.app.push_screen(MessageModal("Could not rename group", str(e)))

    def _delete(self, ok: bool | None, group_id: int, name: str) -> None:
        if not ok:
            return
        try:
            result = self.app.services.groups.delete_with_reconciliation(group_id)
            self.refresh_data()
            self.app.notify(f"Group '{name}' deleted. {result['accounts_unassigned']} account(s) unassigned.")
        except GroupNotFoundError as e:
            self.app.push_screen(MessageModal("Could not delete group", str(e)))
