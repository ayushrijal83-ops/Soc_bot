"""SOC_BOT terminal UI (Textual). Presentation only: every action goes through ``src.services``."""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.events import Resize

from src.tui.screens.create_post import CreatePostScreen
from src.tui.screens.dashboard import DashboardScreen
from src.tui.screens.lists import (
    AccountsScreen,
    ContentScreen,
    HistoryScreen,
    LinksScreen,
    QueueScreen,
    SettingsScreen,
)
from src.tui.theme import CSS, SOCBOT_THEME
from src.tui.widgets import NAV, ConfirmModal, HelpModal

NARROW_WIDTH = 100


class SocBotApp(App):
    TITLE = "SOC_BOT"
    SUB_TITLE = "Social media publishing control center"
    CSS = CSS
    MODES: ClassVar[dict] = {
        "dashboard": DashboardScreen,
        "create": CreatePostScreen,
        "queue": QueueScreen,
        "history": HistoryScreen,
        "accounts": AccountsScreen,
        "links": LinksScreen,
        "content": ContentScreen,
        "settings": SettingsScreen,
    }
    DEFAULT_MODE = "dashboard"
    BINDINGS: ClassVar[list] = [
        Binding("d", "navigate('dashboard')", "Dashboard"),
        Binding("c", "navigate('create')", "Create"),
        Binding("q", "navigate('queue')", "Queue"),
        Binding("h", "navigate('history')", "History", show=False),
        Binding("a", "navigate('accounts')", "Accounts", show=False),
        Binding("l", "navigate('links')", "Links", show=False),
        Binding("t", "navigate('content')", "Content", show=False),
        Binding("s", "navigate('settings')", "Settings", show=False),
        Binding("x", "navigate('exit')", "Exit"),
        Binding("question_mark", "help", "Help"),
        Binding("ctrl+q", "navigate('exit')", "Quit", show=False, priority=True),
    ]

    def __init__(self, services, **kwargs):
        super().__init__(**kwargs)
        self.services = services
        self.publishing_active = False

    def on_mount(self) -> None:
        self.register_theme(SOCBOT_THEME)
        self.theme = "socbot"
        self.set_class(self.size.width < NARROW_WIDTH, "narrow")

    def on_resize(self, event: Resize) -> None:
        # Small terminals: hide the sidebar (shortcuts, Ctrl+P and ? still reach every page).
        self.set_class(event.size.width < NARROW_WIDTH, "narrow")

    # --- navigation ----------------------------------------------------------------------------

    def navigate(self, target: str | None) -> None:
        if not target:
            return
        if target == "exit":
            self.request_exit()
            return
        if target in self.MODES and target != self.current_mode:
            self.switch_mode(target)

    def action_navigate(self, target: str) -> None:
        self.navigate(target)

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        for mode, key, label in NAV:
            yield SystemCommand(f"{label}", f"Go to {label} ({key})", lambda m=mode: self.navigate(m))
        for platform, name in (("instagram", "Instagram"), ("youtube", "YouTube"), ("tiktok", "TikTok")):
            yield SystemCommand(f"{name} accounts", f"Show connected {name} accounts",
                                lambda: self.navigate("accounts"))

    # --- quitting ------------------------------------------------------------------------------

    def request_exit(self) -> None:
        if self.publishing_active:
            # The engine's workers can't be cancelled mid-post; quitting now would leave them running unseen.
            self.notify("A batch is publishing. Wait until it finishes (queued jobs could be resumed later).",
                        severity="warning", timeout=6)
            return
        self.push_screen(ConfirmModal("Exit SOC_BOT?", "Close the application?", "Exit"),
                         lambda ok: self.exit() if ok else None)

    # --- shared actions ------------------------------------------------------------------------

    def copy_links(self, platform: str, urls: list[str]) -> None:
        """Copy permanent URLs only (the link service refuses anything else)."""
        try:
            count = self.services.links.copy(platform, urls)
        except (OSError, ValueError) as e:
            self.notify(f"Could not copy: {e}", severity="error")
            return
        self.notify(f"✓ {count} link{'s' if count != 1 else ''} copied" if count > 1 else "✓ Link copied")


def run_tui(services) -> None:
    SocBotApp(services).run()
