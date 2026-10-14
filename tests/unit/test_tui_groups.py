# ruff: noqa: F811 - fixtures imported from other test modules
"""Account Groups TUI screen (headless pilot). Every action goes through GroupService; no
business logic is tested here (already covered by test_account_groups.py /
test_group_membership_reconciliation.py / test_group_publishing.py) -- only that the screen
wires up correctly and shows the right thing."""

import asyncio

from tests.unit.test_publishing_engine import env  # noqa: F401
from tests.unit.test_tui import services  # noqa: F401


def run(services, script, size=(120, 40)):
    from src.tui.app import SocBotApp

    async def main():
        app = SocBotApp(services)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await script(app, pilot)

    asyncio.run(main())


def test_navigate_to_groups_and_create_one(services):
    from textual.widgets import Button, DataTable, Input

    async def script(app, pilot):
        await pilot.press("g")
        await pilot.pause()
        assert app.current_mode == "groups"
        screen = app.screen
        table = screen.query_one("#groups", DataTable)
        assert table.row_count == 1 and table.get_cell_at((0, 0)) == "No groups yet. Press Create Group."

        screen.query_one("#create", Button).press()
        await pilot.pause()
        assert type(app.screen).__name__ == "CreateGroupModal"
        app.screen.query_one("#name", Input).value = "AI Anime"
        app.screen.query_one("#create", Button).press()
        await pilot.pause()

        table = screen.query_one("#groups", DataTable)
        assert table.row_count == 1
        assert table.get_cell_at((0, 0)) == "AI Anime" and table.get_cell_at((0, 1)) == "0"
        assert len(services.groups.list()) == 1 and services.groups.list()[0].name == "AI Anime"

    run(services, script)


def test_rename_and_delete_group(services):
    from textual.widgets import Button, Input

    services.groups.create("Motivation")

    async def script(app, pilot):
        await pilot.press("g")
        await pilot.pause()
        screen = app.screen

        screen.query_one("#rename", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert type(app.screen).__name__ == "RenameGroupModal"
        assert app.screen.query_one("#name", Input).value == "Motivation"
        app.screen.query_one("#name", Input).value = "Motivation Clips"
        app.screen.query_one("#rename", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert services.groups.list()[0].name == "Motivation Clips"

        screen.query_one("#delete", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert type(app.screen).__name__ == "ConfirmModal"
        await pilot.press("y")
        await pilot.pause()
        assert services.groups.list() == []

    run(services, script)


def test_assign_shows_available_and_occupied_then_unassign_keeps_account_connected(services):
    from textual.widgets import Button, DataTable

    group = services.groups.create("AI Anime")

    async def script(app, pilot):
        await pilot.press("g")
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#groups", DataTable)
        table.move_cursor(row=0)
        screen.query_one("#open", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert type(app.screen).__name__ == "GroupDetailScreen"
        detail = app.screen

        detail.query_one("#assign", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert type(app.screen).__name__ == "AssignAccountModal"
        modal = app.screen
        picker = modal.query_one("#assign-accounts", DataTable)
        # env() seeds 1 account per platform (instagram/tiktok/youtube) + add_accounts(env, 3) adds
        # 3 more instagram accounts = 6 connected accounts total, all AVAILABLE.
        assert picker.row_count == 6
        for row in range(picker.row_count):
            assert picker.get_cell_at((row, 2)) == "AVAILABLE"
        picker.move_cursor(row=0)
        modal.query_one("#assign", Button).press()
        await pilot.pause()
        await pilot.pause()

        assert type(app.screen).__name__ == "GroupDetailScreen"
        members = detail.query_one("#members", DataTable)
        assert members.row_count == 1
        assigned_id = next(iter(services.groups.accounts_in(group.id))).id
        assert services.account_manager.get_account(assigned_id).group_id == group.id

        # Re-open the picker: the assigned account must now show OCCUPIED, not AVAILABLE.
        detail.query_one("#assign", Button).press()
        await pilot.pause()
        await pilot.pause()
        picker2 = app.screen.query_one("#assign-accounts", DataTable)
        occupied_rows = [picker2.get_cell_at((r, 2)) for r in range(picker2.row_count)]
        assert "OCCUPIED -- AI Anime" in occupied_rows
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()

        members.move_cursor(row=0)
        detail.query_one("#unassign", Button).press()
        await pilot.pause()
        await pilot.pause()
        refreshed = services.account_manager.get_account(assigned_id)
        assert refreshed.group_id is None
        assert refreshed.status == "active"  # still connected, never disconnected

    run(services, script)


def test_create_post_destinations_show_group_row_and_hide_its_members(services):
    """Destinations step (Create Post): a group's accounts never appear as individual rows --
    only the group row does -- and checking the group row selects every member at once."""
    from textual.widgets import Input, SelectionList, TextArea

    from src.tui.screens.create_post import GROUP_ROW_OFFSET

    ig_ids = [a.id for a in services.accounts.list("instagram")]
    group = services.groups.create("AI Anime")
    services.groups.assign(ig_ids[0], group.id)  # one of the 4 instagram accounts is now grouped

    async def script(app, pilot):
        await pilot.press("c")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#video-path", Input).value = services.video
        screen.action_next()
        await pilot.pause()
        screen.query_one("#caption", TextArea).text = "Group destinations test"
        screen.action_next()
        await pilot.pause()
        screen.toggle_platform("instagram")
        await pilot.pause()

        lst = screen.query_one("#accounts", SelectionList)
        labels = [str(opt.prompt) for opt in lst.options]
        assert any("AI Anime" in label for label in labels)
        grouped_account_label = next(a.label for a in services.accounts.list("instagram") if a.id == ig_ids[0])
        assert not any(grouped_account_label in label for label in labels)  # hidden: only via its group
        # The other 3 (ungrouped) instagram accounts still show individually.
        assert len(screen._shown_accounts) == 3

        pseudo_id = GROUP_ROW_OFFSET + group.id
        lst.select(pseudo_id)
        await pilot.pause()
        assert ig_ids[0] in screen.selected

        lst.deselect(pseudo_id)
        await pilot.pause()
        assert ig_ids[0] not in screen.selected

        screen.query_one("#accounts", SelectionList).select_all()
        await pilot.pause()
        assert screen.selected == set(ig_ids)  # group member + the 3 ungrouped accounts, all 4

    run(services, script)
