"""SOC_BOT terminal UI (Textual). Presentation only: every action goes through ``src.services``."""

from __future__ import annotations

import logging
from typing import ClassVar

from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.events import Resize

from src.platforms.base import redact
from src.services.due_scheduler import DueEvent, DueRunResult, scheduler_poll_seconds
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
SCHEDULER_BUSY_NOTICE = "Another Soc_bot window is publishing. Scheduled posts will be checked again on the next run."
log = logging.getLogger("soc_bot.tui")


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
        self._manual_publishing = False      # a PublishingScreen batch (set through publishing_active)
        self._scheduler_publishing = False   # the scheduler is publishing a due post right now
        self._scheduler_running = False      # a scheduler pass (check or publish) is in progress
        self._scheduler_timer = None
        self._scheduler_stopped = False
        self._busy_notified = False
        self._errors_notified: set[str] = set()
        self.scheduler_interval = scheduler_poll_seconds()

    @property
    def publishing_active(self) -> bool:
        """True while something is actually publishing (manual batch or a due scheduled post). A scheduler
        that is only checking does not count, so it never blocks quitting."""
        return self._manual_publishing or self._scheduler_publishing

    @publishing_active.setter
    def publishing_active(self, value: bool) -> None:  # used by PublishingScreen for manual batches
        self._manual_publishing = value

    def on_mount(self) -> None:
        self.register_theme(SOCBOT_THEME)
        self.theme = "socbot"
        self.set_class(self.size.width < NARROW_WIDTH, "narrow")
        self.run_scheduler()  # first check right away, then every scheduler_interval seconds
        self._scheduler_timer = self.set_interval(self.scheduler_interval, self.run_scheduler)

    # --- scheduler (DueScheduler.run_due in its own "scheduler" worker group) ------------------

    def run_scheduler(self) -> None:
        """One scheduler pass in a thread worker; skipped while the previous pass is still running."""
        if self._scheduler_running or self._scheduler_stopped:
            return
        self._scheduler_running = True
        self.run_worker(self._scheduler_pass, thread=True, group="scheduler", name="scheduler",
                        exit_on_error=False)

    def _scheduler_pass(self) -> None:
        scheduler = self.services.due_scheduler
        try:
            if not scheduler.has_work():
                return  # nothing due or overdue: the publishing lock is not taken at all
            result = scheduler.run_due(on_event=self._scheduler_event)
            self.call_from_thread(self._scheduler_done, result)
        except Exception as e:  # one failed pass must not stop the timer or the UI
            log.exception("Scheduler pass failed")
            self.call_from_thread(self._scheduler_problem, f"Scheduler check failed: {type(e).__name__}")
        finally:
            self.call_from_thread(self._scheduler_idle)

    def _scheduler_event(self, event: DueEvent) -> None:  # worker thread
        if event.kind == "publishing_started":
            self.call_from_thread(setattr, self, "_scheduler_publishing", True)
        elif event.kind in ("published", "failed", "error") and event.post_id is not None:
            self.call_from_thread(setattr, self, "_scheduler_publishing", False)

    def _scheduler_idle(self) -> None:
        self._scheduler_publishing = False
        self._scheduler_running = False

    def _scheduler_done(self, result: DueRunResult) -> None:
        """Concise notices. A lasting busy lock or a repeating error is shown once, not every tick."""
        if result.busy:
            if not self._busy_notified:
                self._busy_notified = True
                self.notify(SCHEDULER_BUSY_NOTICE, severity="warning", timeout=6)
            return
        self._busy_notified = False
        for event in result.events:
            when = f"{event.scheduled_at:%Y-%m-%d %H:%M} UTC" if event.scheduled_at else "time unknown"
            if event.kind == "missed":
                self.notify(f"Scheduled post #{event.post_id} ({when}) missed its window and was not published.",
                            severity="warning", timeout=8)
            elif event.kind == "published":
                self.notify(f"✓ Scheduled post #{event.post_id} published.", timeout=6)
            elif event.kind == "failed":
                self.notify(f"Scheduled post #{event.post_id} finished with problems ({event.status}). "
                            "See the Queue.", severity="error", timeout=8)
            elif event.kind == "error":
                self._scheduler_problem(f"Scheduled post #{event.post_id}: {event.message}" if event.post_id
                                        else f"Scheduler: {event.message}")

    def _scheduler_problem(self, message: str) -> None:
        safe = redact(message) or "Scheduler problem"
        if safe in self._errors_notified:
            return
        self._errors_notified.add(safe)
        self.notify(safe[:200], severity="error", timeout=8)

    def stop_scheduler(self) -> None:
        """No new passes. A pass that is only checking ends by itself; publishing is guarded by
        publishing_active (quitting is refused while it is True)."""
        self._scheduler_stopped = True
        if self._scheduler_timer is not None:
            self._scheduler_timer.stop()
        self.workers.cancel_group(self, "scheduler")

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
        self.push_screen(ConfirmModal("Exit SOC_BOT?", "Close the application?", "Exit"), self._confirm_exit)

    def _confirm_exit(self, ok: bool | None) -> None:
        if not ok:
            return
        if self.publishing_active:  # the scheduler started publishing while the dialog was open
            self.notify("A scheduled post started publishing. Wait until it finishes.", severity="warning")
            return
        self.stop_scheduler()
        self.exit()

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
