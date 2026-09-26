# ruff: noqa: F811 - pytest fixtures imported from other test modules
"""Service layer + Textual TUI (headless pilot). Fake Instagram publisher: no network, no tunnel."""

import asyncio
from pathlib import Path

import pytest

from src.content.intake import ContentIntake
from src.core.published_links import PublishedLinks
from src.core.publisher import PublisherEngine
from src.services import build_services, friendly_error
from tests.unit.test_batch import FlakyInstagram, add_accounts
from tests.unit.test_publishing_engine import env  # noqa: F401


@pytest.fixture
def services(env, tmp_path):
    db, accounts, _, video = env
    add_accounts(env, 3)
    fake = FlakyInstagram(fails={"ig002": 1})  # fails once, recovered by the automatic retry round
    links = PublishedLinks(tmp_path / "links")
    engine = PublisherEngine(db, accounts, publishers={"instagram": fake}, sleep=lambda s: None,
                             probe_media=False, links=links, max_concurrent=5)
    intake = ContentIntake(db, accounts, engine, root=tmp_path / "content")
    copied = []
    s = build_services(accounts, None, engine, intake, tmp_path / ".env")
    s.links.copier = copied.append
    s.links.opener = lambda url: None
    s.fake, s.copied, s.video = fake, copied, video
    return s


# ------------------------------------------------------------------------------------------------
# Service layer
# ------------------------------------------------------------------------------------------------

def test_plan_create_publish_with_events(services):
    ids = [a.id for a in services.accounts.list("instagram")]
    plan = services.publishing.plan(services.video, "hello", None, ids)
    assert len(plan.valid) == 4 and plan.instagram_jobs == 4 and services.publishing.batches() == []  # no jobs yet
    post_id = services.publishing.create_batch(plan)
    events = []
    view = services.publishing.publish_batch(post_id, events.append)
    kinds = [e.kind for e in events]
    assert kinds[0] == "batch_started" and kinds[-1] == "batch_finished" and "retry_started" in kinds
    assert kinds.index("retry_started") > max(i for i, k in enumerate(kinds) if k == "job_failed")
    final = events[-1].summary
    assert final["published"] == 4 and final["failed"] == 0 and final["recovered"] == 1 and final["links_saved"] == 4
    failed = next(e for e in events if e.kind == "job_failed")
    assert failed.message == "Instagram could not finish processing this video." and "ERROR" in failed.details
    assert view.status == "completed" and all(j.url for j in view.jobs)
    assert "trycloudflare" not in repr(events)


def test_duplicates_skipped_unless_asked(services):
    ids = [a.id for a in services.accounts.list("instagram")][:2]
    services.publishing.publish_batch(services.publishing.create_batch(services.publishing.plan(services.video, "", None, ids)))
    again = services.publishing.plan(services.video, "", None, ids)
    assert all(d.already_published for d in again.valid)
    with pytest.raises(Exception, match="at least one destination"):
        services.publishing.create_batch(again)
    assert services.publishing.create_batch(again, include_already_published=True)


def test_links_copy_only_permanent_urls(services):
    services.links.copy("instagram", ["https://www.instagram.com/reel/A/", "https://x.trycloudflare.com/v.mp4"])
    assert services.copied == ["https://www.instagram.com/reel/A/"]
    with pytest.raises(ValueError):
        services.links.copy("instagram", ["https://x.trycloudflare.com/v.mp4"])


def test_friendly_errors():
    assert friendly_error("instagram", "token_expired", "x") == "The account's login expired. Reconnect the account."
    assert "YouTube" in friendly_error("youtube", "quota_exceeded", "x")
    assert friendly_error("instagram", None, "Cloudflare Quick Tunnel did not start").startswith("The video could not")
    assert friendly_error("tiktok", None, None) == "Publishing failed."


def test_settings_validate_and_save(services, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=1\n", encoding="utf-8")
    services.settings.env_file = env_file
    monkeypatch.setenv("INSTAGRAM_MAX_CONCURRENT_PUBLISHES", "5")
    assert services.settings.save("INSTAGRAM_MAX_CONCURRENT_PUBLISHES", "3") == 3
    assert "INSTAGRAM_MAX_CONCURRENT_PUBLISHES=3" in env_file.read_text(encoding="utf-8")
    assert "OTHER=1" in env_file.read_text(encoding="utf-8")
    for bad in ("0", "21", "x"):
        with pytest.raises(ValueError):
            services.settings.save("INSTAGRAM_MAX_CONCURRENT_PUBLISHES", bad)


# ------------------------------------------------------------------------------------------------
# TUI (headless)
# ------------------------------------------------------------------------------------------------

def run_app(services, script, size=(120, 40)):
    from src.tui.app import SocBotApp

    async def main():
        app = SocBotApp(services)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await script(app, pilot)

    asyncio.run(main())


def test_startup_navigation_and_help(services):
    async def script(app, pilot):
        assert app.current_mode == "dashboard"
        for key, mode in (("c", "create"), ("q", "queue"), ("h", "history"), ("l", "links"), ("t", "content"),
                          ("s", "settings"), ("d", "dashboard")):
            await pilot.press(key)
            await pilot.pause()
            assert app.current_mode == mode
        await pilot.press("a")
        await pilot.pause()
        assert app.current_mode == "accounts"
        await pilot.press("question_mark")
        await pilot.pause()
        assert type(app.screen).__name__ == "HelpModal"
        await pilot.press("escape")
        await pilot.press("x")
        await pilot.pause()
        assert type(app.screen).__name__ == "ConfirmModal"  # exit asks first
        await pilot.press("escape")

    run_app(services, script)


@pytest.mark.parametrize("size", [(80, 24), (100, 30), (160, 50)])
def test_every_page_renders_at_supported_sizes(services, size):
    async def script(app, pilot):
        for mode in ("dashboard", "create", "queue", "history", "accounts", "links", "content", "settings"):
            app.navigate(mode)
            await pilot.pause()
        assert app.has_class("narrow") == (size[0] < 100)  # sidebar hidden on small terminals

    run_app(services, script, size)


def test_create_post_flow_publishes_with_retry_and_links(services, tmp_path):
    from textual.widgets import Input, SelectionList, Static, TextArea

    async def script(app, pilot):
        await pilot.press("c")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#video-path", Input).value = services.video
        screen.action_next()
        await pilot.pause()
        assert screen.step == 1
        screen.query_one("#caption", TextArea).text = "Batch from the TUI"
        screen.action_next()
        await pilot.pause()
        screen.toggle_platform("instagram")
        await pilot.pause()
        screen.query_one("#search", Input).value = "account0"
        await pilot.pause()
        assert len(screen.query_one("#accounts", SelectionList).options) == 3  # search filters
        screen.query_one("#search", Input).value = ""
        await pilot.pause()
        screen.query_one("#accounts", SelectionList).focus()
        await pilot.press("a")  # select all
        await pilot.pause()
        assert len(screen.selected) == 4
        await pilot.press("n")  # select none
        await pilot.pause()
        assert not screen.selected
        await pilot.press("a")
        screen.action_next()
        await pilot.pause()
        screen.action_next()
        await pilot.pause()
        review = str(screen.query_one("#review", Static).render())
        assert "READY TO PUBLISH" in review and "4 account(s)" in review and "retried once" in review
        assert services.publishing.batches() == []  # nothing created before confirmation
        screen.confirm_publish()
        await pilot.pause()
        await pilot.press("y")  # confirm modal
        for _ in range(60):
            await pilot.pause(0.05)
            if type(app.screen).__name__ == "PublishingScreen" and not app.screen.running:
                break
        pub = app.screen
        assert type(pub).__name__ == "PublishingScreen" and not pub.running
        text = str(pub.query_one("#round", Static).render())
        assert "PUBLISH COMPLETE" in text and "Recovered 1" in text and "Links saved: 4" in text
        assert "trycloudflare" not in text
        pub.query_one("#links").press()
        await pilot.pause()
        assert app.current_mode == "links"
        await pilot.press("c")  # copy selected link
        await pilot.pause()
        assert len(services.copied) == 1 and services.copied[0].startswith("https://www.instagram.com/reel/")

    run_app(services, script)


def test_queue_and_batch_detail(services):
    ids = [a.id for a in services.accounts.list("instagram")][:2]
    post_id = services.publishing.create_batch(services.publishing.plan(services.video, "", None, ids))
    services.publishing.publish_batch(post_id)

    async def script(app, pilot):
        from textual.widgets import DataTable

        await pilot.press("q")
        await pilot.pause()
        card = app.screen.query("BatchCard").first()
        card.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert type(app.screen).__name__ == "BatchDetailScreen"
        assert app.screen.query_one(DataTable).row_count == 2
        await pilot.press("c")
        await pilot.pause()
        assert len(services.copied) == 1

    run_app(services, script)


def test_main_plain_flag_and_fallback(monkeypatch):
    import main as main_module

    monkeypatch.setattr("sys.argv", ["main.py", "--plain"])
    assert main_module.parse_args().plain
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert main_module.tui_available() is False
    assert Path(main_module.__file__).exists()


# ------------------------------------------------------------------------------------------------
# Review actions + active navigation (regression)
# ------------------------------------------------------------------------------------------------

async def to_review(app, pilot, services, search=""):
    from textual.widgets import Input, SelectionList

    await pilot.press("c")
    await pilot.pause()
    s = app.screen
    s.query_one("#video-path", Input).value = services.video
    s.action_next()
    await pilot.pause()
    s.action_next()
    await pilot.pause()
    s.toggle_platform("instagram")
    s.query_one("#search", Input).value = search
    await pilot.pause()
    s.query_one("#accounts", SelectionList).select_all()
    await pilot.pause()
    s.action_next()
    await pilot.pause()
    s.action_next()
    await pilot.pause()
    assert s.step == 4
    return s


def visible(app, widget, size) -> bool:
    r = widget.region
    return widget.display and r.height > 0 and r.width > 0 and r.y >= 0 and r.bottom <= size[1] - 1


@pytest.mark.parametrize("size", [(80, 24), (100, 30), (120, 40), (160, 50)])
def test_review_shows_publish_back_cancel_at_every_size(services, size):
    from textual.widgets import Button

    async def script(app, pilot):
        s = await to_review(app, pilot, services)
        publish, back, cancel, nxt = (s.query_one(f"#{i}", Button) for i in ("publish", "back", "cancel", "next"))
        assert str(publish.label) == "▶ PUBLISH NOW" and not publish.disabled
        for button in (publish, back, cancel):
            assert visible(app, button, size), (button.id, button.region, size)
        assert not nxt.display  # no "Next" on the final step
        assert app.focused is publish and publish.can_focus
        assert s.check_action("next", ()) is False and s.check_action("publish", ()) is True

    run_app(services, script, size)


def test_enter_activates_publish_then_single_confirmation(services):
    async def script(app, pilot):
        await to_review(app, pilot, services)
        await pilot.press("enter")  # focused PUBLISH NOW
        await pilot.pause()
        assert type(app.screen).__name__ == "ConfirmModal"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("p")  # visible footer shortcut
        await pilot.pause()
        assert type(app.screen).__name__ == "ConfirmModal"
        await pilot.press("escape")
        await pilot.pause()
        assert services.publishing.batches() == []  # nothing published without confirming

    run_app(services, script)


def test_back_and_cancel_from_review(services):
    async def script(app, pilot):
        s = await to_review(app, pilot, services)
        await pilot.press("escape")
        await pilot.pause()
        assert s.step == 3 and s.check_action("next", ()) is True
        s.action_next()
        await pilot.pause()
        s.query_one("#cancel").press()
        await pilot.pause()
        assert type(app.screen).__name__ == "ConfirmModal"
        await pilot.press("y")
        await pilot.pause()
        assert app.current_mode == "dashboard"

    run_app(services, script)


@pytest.mark.parametrize("size", [(80, 24), (100, 30)])
def test_long_review_never_hides_the_actions(services, env, size):
    from textual.widgets import Button, Checkbox

    add_accounts(env, 11, prefix="dup")
    ids = [a.id for a in services.accounts.list("instagram") if a.label.startswith("dup")]
    services.publishing.publish_batch(services.publishing.create_batch(services.publishing.plan(services.video, "", None, ids)))
    add_accounts(env, 50, prefix="bulk")

    async def script(app, pilot):
        s = await to_review(app, pilot, services)  # 11 duplicates + 50 more accounts in one review
        assert s.query_one("#include-dups", Checkbox).display  # duplicate warning shown
        assert len(s.plan.valid) >= 61
        for button_id in ("publish", "back", "cancel"):
            assert visible(app, s.query_one(f"#{button_id}", Button), size), button_id

    run_app(services, script, size)


@pytest.mark.parametrize("mode", ["dashboard", "create", "queue", "history", "accounts", "links", "content", "settings"])
def test_sidebar_marks_the_active_page(services, mode):
    from textual.widgets import OptionList

    from src.tui.widgets import NAV, Sidebar

    async def script(app, pilot):
        app.navigate(mode)
        await pilot.pause()
        index = [m for m, _, _ in NAV].index(mode)
        nav = app.screen.query_one(Sidebar).query_one(OptionList)
        assert nav.highlighted == index
        prompts = [str(nav.get_option_at_index(i).prompt) for i in range(len(NAV))]
        assert [i for i, p in enumerate(prompts) if "▌" in p] == [index]  # exactly one active marker
        nav.highlighted = 0 if index else 1  # cursor drifts (arrows / mouse)
        app.navigate("dashboard" if mode != "dashboard" else "queue")
        await pilot.pause()
        app.navigate(mode)
        await pilot.pause()
        assert nav.highlighted == index  # restored when the page is shown again

    run_app(services, script)


def test_delete_history_keeps_open_jobs_and_links(services):
    from src.core.jobs import JobStore

    ids = [a.id for a in services.accounts.list("instagram")][:3]
    post_id = services.publishing.create_batch(services.publishing.plan(services.video, "", None, ids))
    services.publishing.publish_batch(post_id)
    links_before = services.links.records("instagram")
    open_post = services.publishing.create_batch(services.publishing.plan(services.video, "", None, ids[:1]),
                                                 include_already_published=True)  # stays pending
    jobs = JobStore(services.engine.store.database).jobs_for_post(post_id)
    pending = JobStore(services.engine.store.database).jobs_for_post(open_post)[0]
    assert services.publishing.delete_history([jobs[0].id, pending.id]) == 1  # open job refused
    assert [h["job_id"] for h in services.publishing.history()].count(pending.id) == 1
    assert services.publishing.delete_history([j.id for j in jobs]) == 2
    assert all(h["post_id"] != post_id for h in services.publishing.history())
    assert post_id not in [b.post_id for b in services.publishing.batches()]  # empty batch removed
    assert services.links.records("instagram") == links_before  # permanent links untouched


def test_history_screen_delete_selected_and_all(services):
    ids = [a.id for a in services.accounts.list("instagram")][:3]
    services.publishing.publish_batch(services.publishing.create_batch(services.publishing.plan(services.video, "", None, ids)))

    async def script(app, pilot):
        from textual.widgets import DataTable

        await pilot.press("h")
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        assert table.row_count == 3
        table.focus()
        await pilot.press("delete")
        await pilot.pause()
        assert type(app.screen).__name__ == "ConfirmModal"
        await pilot.press("y")
        await pilot.pause()
        assert app.screen.query_one(DataTable).row_count == 2
        app.screen.query_one("#hist-delete-all").press()
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert app.screen.query_one(DataTable).row_count == 0

    run_app(services, script)
