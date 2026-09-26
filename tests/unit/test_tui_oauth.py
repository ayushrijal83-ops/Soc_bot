# ruff: noqa: F811 - pytest fixtures imported from other test modules
"""OAuth from the TUI: the existing async flow runs on a worker thread (own loop), never on the UI loop."""

import asyncio
import gc
import threading
import warnings
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import ClassVar
from unittest.mock import patch

import pytest

from src.auth.errors import OAuthCallbackError
from tests.unit.test_publishing_engine import env  # noqa: F401
from tests.unit.test_tui import run_app, services  # noqa: F401


class FakeAuth:
    """Stands in for AuthManager: same public surface the service/CLI use (no network)."""

    def __init__(self, accounts, configured=("instagram", "youtube"), fail=None, paste=False):
        self.accounts, self.configured, self.fail, self.paste = accounts, configured, fail, paste
        self.redirect_prompt = None
        self.threads, self.answers = [], []

    def is_configured(self, platform):
        return platform in self.configured

    async def connect_account(self, platform):
        asyncio.get_running_loop()  # a loop of its own...
        self.threads.append(threading.current_thread().name)  # ...on a non-UI thread
        if self.paste:
            self.answers.append(await asyncio.to_thread(self.redirect_prompt, "Paste the address"))
        if self.fail:
            raise self.fail
        account = self.accounts.create_account(platform=platform, platform_account_id=f"new-{platform}",
                                               username=f"new_{platform}", access_token="T",
                                               expires_at=datetime.now(timezone.utc) + timedelta(days=60))
        return {"success": True, "account": {"id": account.id, "username": account.username, "display_name": None,
                                             "scopes": []}}

    def disconnect_account(self, account_id):
        asyncio.run(asyncio.sleep(0))  # like the real revoke: its own loop
        self.threads.append(threading.current_thread().name)
        self.accounts.disconnect_account(account_id)
        return True


def with_auth(services, env, **kw):
    auth = FakeAuth(env[1], **kw)
    services.accounts.auth = auth
    services.auth_manager = auth
    return auth


async def wait_until(pilot, condition, rounds=100):
    for _ in range(rounds):
        await pilot.pause(0.05)
        if condition():
            return True
    return False


async def open_accounts(app, pilot, platform):
    from textual.widgets import TabbedContent

    await pilot.press("a")
    await pilot.pause()
    app.screen.query_one(TabbedContent).active = f"tab-{platform}"
    await pilot.pause()


def test_connect_runs_on_a_worker_and_returns_to_accounts(services, env):
    auth = with_auth(services, env)

    async def script(app, pilot):
        await open_accounts(app, pilot, "instagram")
        app.screen.query_one("#connect").press()
        assert await wait_until(pilot, lambda: type(app.screen).__name__ == "AccountsScreen" and auth.threads)
        await wait_until(pilot, lambda: not [w for w in app.workers if w.is_running])
        assert auth.threads and auth.threads[0] != "MainThread"
        assert type(app.screen).__name__ == "AccountsScreen"
        assert any(a.username == "new_instagram" for a in services.accounts.list("instagram"))
        assert not [w for w in app.workers if w.is_running]  # no leaked worker
        assert auth.redirect_prompt is None  # restored after the attempt

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_app(services, script)
        gc.collect()
    assert not [w for w in caught if "never awaited" in str(w.message)]


def test_instagram_paste_mode_through_the_modal(services, env):
    from textual.widgets import Input

    auth = with_auth(services, env, paste=True)

    async def script(app, pilot):
        await open_accounts(app, pilot, "instagram")
        app.screen.query_one("#connect").press()
        assert await wait_until(pilot, lambda: type(app.screen).__name__ == "ConnectModal" and app.screen.pasting)
        app.screen.query_one("#paste-url", Input).value = "https://example.github.io/cb?code=X&state=Y"
        app.screen.query_one("#paste-ok").press()
        assert await wait_until(pilot, lambda: type(app.screen).__name__ == "AccountsScreen" and auth.threads)
        assert auth.answers == ["https://example.github.io/cb?code=X&state=Y"]

    run_app(services, script)


def test_failure_shows_a_clean_message(services, env):
    with_auth(services, env, fail=OAuthCallbackError("Authorization failed: access_denied", platform="instagram"))

    async def script(app, pilot):
        await open_accounts(app, pilot, "instagram")
        app.screen.query_one("#connect").press()
        assert await wait_until(pilot, lambda: type(app.screen).__name__ == "MessageModal")
        modal = app.screen
        assert modal.message == "✗ Instagram connection failed." and "access_denied" in modal.details
        await pilot.press("escape")
        await pilot.pause()
        assert type(app.screen).__name__ == "AccountsScreen"

    run_app(services, script)


def test_youtube_connects_and_tiktok_reports_not_set_up(services, env):
    auth = with_auth(services, env)

    async def script(app, pilot):
        await open_accounts(app, pilot, "youtube")
        app.screen.query_one("#connect").press()
        assert await wait_until(pilot, lambda: auth.threads and type(app.screen).__name__ == "AccountsScreen")
        await open_accounts(app, pilot, "tiktok")
        app.screen.query_one("#connect").press()
        assert await wait_until(pilot, lambda: type(app.screen).__name__ == "MessageModal")
        assert "TikTok is not set up" in app.screen.message

    run_app(services, script)
    assert any(a.username == "new_youtube" for a in services.accounts.list("youtube"))


def test_disconnect_runs_on_a_worker(services, env):
    auth = with_auth(services, env)
    target = services.accounts.list("instagram")[0]

    async def script(app, pilot):
        await open_accounts(app, pilot, "instagram")
        app.screen.query_one("#disconnect").press()
        await pilot.pause()
        await pilot.press("y")
        assert await wait_until(pilot, lambda: bool(auth.threads))
        assert auth.threads[0] != "MainThread"

    run_app(services, script)
    assert next(a for a in services.accounts.list("instagram") if a.id == target.id).status == "disconnected"


def test_service_refuses_to_run_on_a_running_loop(services, env):
    with_auth(services, env)

    async def inside_loop():
        with pytest.raises(RuntimeError, match="worker thread"):
            services.accounts.connect("instagram")
        with pytest.raises(RuntimeError, match="worker thread"):
            services.accounts.disconnect(1)

    asyncio.run(inside_loop())
    assert services.accounts.connect("instagram").ok  # fine outside a running loop (worker thread / CLI)


def test_classic_cli_connect_unchanged(services, env):
    from src.cli.account_menu import AccountMenuHandler

    auth = with_auth(services, env)
    handler = AccountMenuHandler(services.account_manager, auth)
    with patch("builtins.input", return_value=""), patch("src.cli.account_menu.clear_screen"):
        assert handler.handle_connect_account("youtube") is True
    assert auth.threads == ["MainThread"]  # the CLI still runs it directly (no event loop there)
    assert any(a.username == "new_youtube" for a in services.accounts.list("youtube"))


def test_run_classic_runs_the_flow_off_the_loop_thread():
    from src.tui.widgets import run_classic

    class App:
        notes: ClassVar[list] = []

        @contextmanager
        def suspend(self):
            yield

        def notify(self, message, **kw):
            self.notes.append(message)

    seen = []

    def classic_flow():
        seen.append(threading.current_thread().name)
        asyncio.run(asyncio.sleep(0))  # e.g. token refresh inside the classic inbox

    async def ui_loop():
        run_classic(App(), classic_flow)

    asyncio.run(ui_loop())
    assert seen == ["soc_bot-classic"] and App.notes == []


@pytest.mark.parametrize("size", [(80, 24), (100, 30), (120, 40)])
def test_paste_field_is_visible_on_screen(services, env, size):
    with_auth(services, env, paste=True)

    async def script(app, pilot):
        await open_accounts(app, pilot, "instagram")
        app.screen.query_one("#connect").press()
        assert await wait_until(pilot, lambda: type(app.screen).__name__ == "ConnectModal" and app.screen.pasting)
        modal = app.screen
        await pilot.pause()
        try:
            for widget_id in ("#paste-url", "#paste-ok", "#paste-cancel"):
                r = modal.query_one(widget_id).region
                assert r.height > 0 and r.y >= 0 and r.bottom <= size[1], (widget_id, r, size)
            assert app.focused is modal.query_one("#paste-url")
        finally:
            modal._reply("")  # never leave the OAuth thread waiting, even when an assertion fails

    run_app(services, script, size)
