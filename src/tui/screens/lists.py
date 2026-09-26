"""Queue, batch detail, history, accounts, published links, content and settings screens."""

from __future__ import annotations

import threading
from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    LoadingIndicator,
    ProgressBar,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from src.tui.theme import (
    AMBER,
    BLUE,
    GREEN,
    PLATFORM_NAMES,
    RED,
    mb,
    platform_markup,
    status_markup,
)
from src.tui.widgets import ConfirmModal, MessageModal, Page, run_classic

PLATFORMS = ("instagram", "youtube", "tiktok")


def _date(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "-"


# ------------------------------------------------------------------------------------------------
# Queue + batch detail
# ------------------------------------------------------------------------------------------------

class BatchCard(Vertical):
    can_focus = True
    BINDINGS: ClassVar[list] = [Binding("enter", "open", "Open")]

    def __init__(self, view):
        super().__init__(classes="batchcard")
        self.view = view

    def compose(self) -> ComposeResult:
        v = self.view
        platforms = "  ".join(platform_markup(p) for p in v.platforms)
        yield Static(f"[b]BATCH #{v.post_id}[/]  {status_markup(v.status)}   [b]{v.video[:40]}[/]   {platforms}   "
                     f"{v.total} account(s)   [dim]{v.created_at:%m-%d %H:%M}[/]" if v.created_at else f"{v.total} account(s)")
        bar = ProgressBar(total=max(v.total, 1), show_eta=False)
        bar.progress = v.done
        yield bar
        c = v.counts
        yield Static(f"[{GREEN}]✓ {c.get('published', 0)} Published[/]   "
                     f"[{BLUE}]→ {c.get('uploading', 0) + c.get('processing', 0)} Running[/]   "
                     f"○ {c.get('pending', 0)} Pending   [{AMBER}]↻ {c.get('retrying', 0)} Retrying[/]   "
                     f"[{RED}]✗ {c.get('failed', 0)} Failed[/]   [dim]Enter = open[/]")

    def action_open(self) -> None:
        self.app.push_screen(BatchDetailScreen(self.view.post_id))

    def on_click(self) -> None:
        self.action_open()


class QueueScreen(Page):
    MODE = "queue"

    def body(self) -> ComposeResult:
        yield Static("Publishing Queue", classes="title")
        yield Static("", id="queue-summary", classes="subtitle")
        with Horizontal(classes="buttons"):
            yield Button("Continue open jobs", id="resume", variant="warning")
            yield Button("Refresh", id="refresh")
        yield VerticalScroll(id="batches", classes="fill")

    def on_mount(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        service = self.app.services.publishing
        counts = service.queue_counts()
        open_jobs = counts["pending"] + counts["uploading"] + counts["processing"] + counts["retrying"]
        owed = service.store.retry_owed_post_ids(("instagram",))
        self.query_one("#queue-summary", Static).update(
            f"{open_jobs} open job(s)  ·  {counts['published']} published  ·  {counts['failed']} failed"
            + (f"  ·  {len(owed)} batch(es) owe their automatic retry" if owed else ""))
        self.query_one("#resume", Button).display = bool(open_jobs or owed)
        box = self.query_one("#batches", VerticalScroll)
        box.remove_children()
        views = service.batches(limit=25)
        box.mount_all([BatchCard(v) for v in views] or [Static("[dim]No batches yet.[/]")])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.refresh_data()
        elif event.button.id == "resume":
            self.app.push_screen(ConfirmModal(
                "Continue open jobs?", "Resume every open job and owed automatic retry round now? "
                "Already published jobs are never published again.", "Continue"), self._resume)

    def _resume(self, ok: bool | None) -> None:
        if ok:
            from src.tui.screens.publishing import PublishingScreen

            self.app.push_screen(PublishingScreen(resume=True))


class BatchDetailScreen(Screen):
    BINDINGS: ClassVar[list] = [Binding("escape", "app.pop_screen", "Back"), Binding("o", "open_link", "Open post"),
                Binding("c", "copy_link", "Copy link"), Binding("r", "retry", "Retry failed"),
                Binding("enter", "details", "Details", show=False)]

    def __init__(self, post_id: int):
        super().__init__()
        self.post_id = post_id

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="content"):
            yield Static(f"Batch #{self.post_id}", classes="title")
            yield Static("", id="batch-meta", classes="panel")
            yield DataTable(id="batch-jobs", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Account", "Platform", "Status", "Attempts", "Result")
        self.refresh_data()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        v = self.app.services.publishing.batch(self.post_id)
        self.view = v
        caption = v.caption.replace("\n", " ")
        retried = sum(j.auto_retried for j in v.jobs)
        self.query_one("#batch-meta", Static).update(
            f"Video [b]{v.video}[/]   Cover {v.cover or 'none'}   {status_markup(v.status)}\n"
            f"Caption [dim]{caption[:110]}{'…' if len(caption) > 110 else ''}[/]\n"
            f"Platforms {'  '.join(platform_markup(p) for p in v.platforms)}   Accounts {v.total}   "
            f"Automatic retries used {retried}")
        table = self.query_one(DataTable)
        table.clear()
        for job in v.jobs:
            result = job.url or (job.error or "")
            table.add_row(f"@{job.account_label}", platform_markup(job.platform), status_markup(job.status),
                          str(job.attempts), result[:70], key=str(job.job_id))

    def _job(self):
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        return next((j for j in self.view.jobs if str(j.job_id) == key), None)

    def action_open_link(self) -> None:
        job = self._job()
        if job and job.url:
            self.app.services.links.open(job.platform, job.url)
            self.app.notify("Opened in your browser")

    def action_copy_link(self) -> None:
        job = self._job()
        if job and job.url:
            self.app.copy_links(job.platform, [job.url])
        elif job:
            self.app.notify("No permanent link for this job.", severity="warning")

    def action_details(self) -> None:
        job = self._job()
        if job is None:
            return
        if job.status == "failed":
            self.app.push_screen(MessageModal(f"@{job.account_label}", f"✗ Publishing failed\n\n{job.error}",
                                              job.error_details))
        elif job.url:
            self.app.push_screen(MessageModal(f"@{job.account_label}", f"✓ Published\n\n{job.url}\n\n"
                                              "o = open  ·  c = copy"))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_details()

    def action_retry(self) -> None:
        job = self._job()
        if job is None or job.status != "failed":
            self.app.notify("Select a failed job to retry.", severity="warning")
            return
        self.app.push_screen(ConfirmModal(
            "Retry this job?", f"Retry @{job.account_label} now? This is a manual retry: the automatic retry "
            "will not run for this job afterwards.", "Retry"), lambda ok: self._retry(ok, job.job_id))

    def _retry(self, ok: bool | None, job_id: int) -> None:
        if ok:
            from src.tui.screens.publishing import PublishingScreen

            self.app.push_screen(PublishingScreen(post_id=self.post_id, retry_job_id=job_id))


# ------------------------------------------------------------------------------------------------
# History
# ------------------------------------------------------------------------------------------------

class HistoryScreen(Page):
    MODE = "history"
    BINDINGS: ClassVar[list] = [Binding("slash", "search", "Search", show=False),
                                Binding("delete", "delete_selected", "Delete entry")]

    def body(self) -> ComposeResult:
        yield Static("History", classes="title")
        with Horizontal(classes="row"):
            yield Select([("All platforms", ""), *[(PLATFORM_NAMES[p], p) for p in PLATFORMS]],
                         value="", allow_blank=False, id="hist-platform")
            yield Select([("All statuses", ""), ("Published", "published"), ("Failed", "failed"),
                          ("Pending", "pending"), ("Processing", "processing")], value="", allow_blank=False,
                         id="hist-status")
        yield Input(placeholder="/ Search video or account", id="hist-search")
        yield DataTable(id="hist-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="buttons"):
            yield Button("Delete selected", id="hist-delete", variant="error")
            yield Button("Delete all shown", id="hist-delete-all", variant="error")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("", "Video", "Platform", "Account", "Date", "Status")
        self.refresh_data()

    def refresh_data(self) -> None:
        platform = self.query_one("#hist-platform", Select).value or None
        status = self.query_one("#hist-status", Select).value or None
        rows = self.app.services.publishing.history(platform, status, self.query_one("#hist-search", Input).value)
        self.rows = rows
        table = self.query_one(DataTable)
        table.clear()
        for r in rows:
            video = r["video"][:16] + ("…" if len(r["video"]) > 16 else "")
            table.add_row("▣", video, platform_markup(r["platform"]), f"@{r['account']}", _date(r["date"]),
                          status_markup(r["status"]), key=f"{r['job_id']}")

    def on_select_changed(self, event: Select.Changed) -> None:
        self.refresh_data()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.refresh_data()

    def action_search(self) -> None:
        self.query_one("#hist-search", Input).focus()

    # --- delete history ------------------------------------------------------------------------

    def action_delete_selected(self) -> None:
        table = self.query_one(DataTable)
        if not self.rows or table.cursor_row < 0:
            return
        self.confirm_delete([self.rows[table.cursor_row]])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "hist-delete":
            self.action_delete_selected()
        elif event.button.id == "hist-delete-all":
            self.confirm_delete(list(self.rows))

    def confirm_delete(self, rows: list[dict]) -> None:
        finished = [r["job_id"] for r in rows if r["status"] in ("published", "failed")]
        if not finished:
            self.app.notify("Only finished entries (published or failed) can be deleted.", severity="warning")
            return
        kept = len(rows) - len(finished)
        message = (f"Delete [b]{len(finished)}[/] finished entr{'y' if len(finished) == 1 else 'ies'} from history?\n\n"
                   "Posts stay online and their links stay in Published Links. After deleting, Soc_bot no longer "
                   "warns if the same video is published to these accounts again."
                   + (f"\n{kept} open job(s) are kept." if kept else ""))
        self.app.push_screen(ConfirmModal("Delete history?", message, "Delete", danger=True),
                             lambda ok: self._delete(ok, finished))

    def _delete(self, ok: bool | None, job_ids: list[int]) -> None:
        if ok:
            count = self.app.services.publishing.delete_history(job_ids)
            self.app.notify(f"✓ Deleted {count} history entr{'y' if count == 1 else 'ies'}")
            self.refresh_data()


# ------------------------------------------------------------------------------------------------
# Accounts
# ------------------------------------------------------------------------------------------------

class ConnectModal(ModalScreen[None]):
    """Shown while the existing OAuth flow runs on a worker thread. Answers its paste-mode question."""

    BINDINGS: ClassVar[list] = [Binding("escape", "cancel", "Cancel")]
    PASTE_TIMEOUT = 600
    # LoadingIndicator is height:100% by default; in this auto-height modal that pushed the paste field
    # below the screen. Keep it one line.
    DEFAULT_CSS = "ConnectModal #spinner { height: 1; }"

    def __init__(self, platform: str):
        super().__init__()
        self.platform = platform
        self._answer: str | None = None
        self._answered = threading.Event()
        self.pasting = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(f"CONNECTING {PLATFORM_NAMES[self.platform].upper()}", classes="modal-title")
            yield Static("Browser authorization required: your browser opens the official login page.")
            yield LoadingIndicator(id="spinner")
            yield Static("Waiting for authorization… (stops by itself after 5 minutes)", id="connect-status",
                         classes="muted")
            with Vertical(id="paste-box"):
                yield Static("", id="paste-help")
                yield Input(placeholder="Paste the full address from the browser here", id="paste-url")
                with Horizontal(classes="buttons"):
                    yield Button("Continue", variant="primary", id="paste-ok")
                    yield Button("Cancel", id="paste-cancel")

    def on_mount(self) -> None:
        self.query_one("#paste-box").display = False

    # Called on the OAuth helper thread (never the UI thread): blocks that thread until answered.
    def ask(self, message: str) -> str | None:
        self.app.call_from_thread(self._show_paste, message)
        self._answered.wait(self.PASTE_TIMEOUT)
        return self._answer or ""

    def _show_paste(self, message: str) -> None:
        self.pasting = True
        self.query_one("#spinner").display = False
        self.query_one("#paste-help", Static).update(message)
        self.query_one("#paste-box").display = True
        self.query_one("#connect-status", Static).update("Waiting for the pasted address…")
        self.query_one("#paste-url", Input).focus()

    def _reply(self, value: str) -> None:
        self._answer = value
        self.pasting = False
        self.query_one("#paste-box").display = False
        self.query_one("#connect-status", Static).update("Completing connection…")
        self._answered.set()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "paste-ok":
            self._reply(self.query_one("#paste-url", Input).value.strip())
        elif event.button.id == "paste-cancel":
            self._reply("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._reply(event.value.strip())

    def action_cancel(self) -> None:
        if self.pasting:
            self._reply("")  # safe: the flow ends with "cancelled" and cleans up
        else:
            self.app.notify("Waiting for the browser. The attempt ends by itself (max 5 minutes).")


class AccountsScreen(Page):
    MODE = "accounts"

    def body(self) -> ComposeResult:
        yield Static("Accounts", classes="title")
        with TabbedContent(id="acct-tabs"):
            for platform in PLATFORMS:
                with TabPane(PLATFORM_NAMES[platform], id=f"tab-{platform}"):
                    yield DataTable(id=f"acct-{platform}", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="buttons"):
            yield Button("Connect", id="connect", variant="primary")
            yield Button("Reconnect", id="reconnect")
            yield Button("Disconnect", id="disconnect", variant="error")
            yield Button("Enable", id="enable")

    def on_mount(self) -> None:
        for platform in PLATFORMS:
            self.query_one(f"#acct-{platform}", DataTable).add_columns("Account", "Status", "Login valid until")
        self.refresh_data()

    def refresh_data(self) -> None:
        summary = {s.platform: s for s in self.app.services.accounts.summary()}
        for platform in PLATFORMS:
            table = self.query_one(f"#acct-{platform}", DataTable)
            table.clear()
            for a in self.app.services.accounts.list(platform):
                table.add_row(f"@{a.label}", status_markup(a.status, a.status_text), _date(a.expires_at), key=str(a.id))
            if table.row_count == 0:
                note = "OAuth app configured: press Connect" if summary[platform].configured else "Not set up (add the app credentials to .env)"
                table.add_row(f"[dim]No {PLATFORM_NAMES[platform]} accounts[/]", f"[dim]{note}[/]", "", key="none")

    def current(self) -> tuple[str, int | None]:
        platform = self.query_one(TabbedContent).active.removeprefix("tab-")
        table = self.query_one(f"#acct-{platform}", DataTable)
        if table.row_count == 0:
            return platform, None
        key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        return platform, None if key == "none" else int(key)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        platform, account_id = self.current()
        action = event.button.id
        if action in ("connect", "reconnect"):
            self.connect(platform)
        elif account_id is None:
            self.app.notify("Select an account first.", severity="warning")
        elif action == "disconnect":
            label = next(a.label for a in self.app.services.accounts.list(platform) if a.id == account_id)
            self.app.push_screen(ConfirmModal("Disconnect account?", f"Disconnect @{label}? Its login is revoked where "
                                              "the platform supports it. Published posts stay online.",
                                              "Disconnect", danger=True), lambda ok: self._disconnect(ok, account_id))
        elif action == "enable":
            self.app.services.accounts.enable(account_id)
            self.app.notify("Account enabled")
            self.refresh_data()

    def _disconnect(self, ok: bool | None, account_id: int) -> None:
        if ok:
            self.run_worker(lambda: self._disconnect_worker(account_id), thread=True, group="accounts")

    def _disconnect_worker(self, account_id: int) -> None:
        # Revocation runs its own event loop: never on the UI loop's thread.
        try:
            self.app.services.accounts.disconnect(account_id)
            self.app.call_from_thread(self.app.notify, "Account disconnected")
        except Exception as e:  # noqa: BLE001 - shown as a toast
            self.app.call_from_thread(self.app.notify, f"Could not disconnect: {type(e).__name__}", severity="error")
        self.app.call_from_thread(self.refresh_data)

    def connect(self, platform: str) -> None:
        """Existing OAuth flow on a worker thread (it runs its own event loop there); the UI stays live."""
        modal = ConnectModal(platform)
        self.app.push_screen(modal)
        self.run_worker(lambda: self._connect_worker(platform, modal), thread=True, exclusive=True, group="oauth")

    def _connect_worker(self, platform: str, modal: ConnectModal) -> None:
        result = self.app.services.accounts.connect(platform, redirect_prompt=modal.ask)
        self.app.call_from_thread(self._connected, result, modal)

    def _connected(self, result, modal: ConnectModal) -> None:
        if modal.is_attached:
            modal.dismiss(None)
        self.refresh_data()
        if result.ok:
            self.app.notify(f"✓ {result.message}")
            if result.missing_scopes:
                self.app.push_screen(MessageModal("Permission missing", "Connected, but publishing needs: "
                                                  + "; ".join(result.missing_scopes) + ". Reconnect and grant it."))
        else:
            self.app.push_screen(MessageModal("Connection failed", f"✗ {result.message}", result.details))


# ------------------------------------------------------------------------------------------------
# Published links
# ------------------------------------------------------------------------------------------------

class LinksScreen(Page):
    MODE = "links"
    BINDINGS: ClassVar[list] = [Binding("c", "copy", "Copy selected"), Binding("a", "copy_all", "Copy all visible"),
                Binding("slash", "search", "Search", show=False)]

    def body(self) -> ComposeResult:
        yield Static("Published Links", classes="title")
        yield Static("", id="links-summary", classes="subtitle")
        yield Input(placeholder="/ Search account or video", id="links-search")
        with TabbedContent(id="links-tabs"):
            for platform in PLATFORMS:
                with TabPane(PLATFORM_NAMES[platform], id=f"ltab-{platform}"):
                    yield DataTable(id=f"links-{platform}", cursor_type="row", zebra_stripes=True)
        yield Static("[dim]Enter open · C copy selected · A copy all visible · only permanent URLs are stored[/]")

    def on_mount(self) -> None:
        for platform in PLATFORMS:
            self.query_one(f"#links-{platform}", DataTable).add_columns("Account", "Permanent URL", "Video", "Date")
        self.refresh_data()

    def platform(self) -> str:
        return self.query_one(TabbedContent).active.removeprefix("ltab-")

    def refresh_data(self) -> None:
        needle = self.query_one("#links-search", Input).value
        self.link_rows: dict[str, list[dict]] = {}
        for platform in PLATFORMS:
            rows = self.app.services.links.records(platform, needle)
            self.link_rows[platform] = rows
            table = self.query_one(f"#links-{platform}", DataTable)
            table.clear()
            for index, r in enumerate(rows):
                video = str(r.get("video", ""))
                table.add_row(f"@{r.get('account', '?')}", r["url"], video[:14] + ("…" if len(video) > 14 else ""),
                              str(r.get("published_at", ""))[:16].replace("T", " "), key=str(index))
        counts = self.app.services.links.counts()
        self.query_one("#links-summary", Static).update(
            "   ".join(f"{platform_markup(p)} {counts[p]}" for p in PLATFORMS)
            + ("   [dim]TikTok returns no public post URL[/]" if not counts["tiktok"] else ""))

    def selected_row(self) -> dict | None:
        platform = self.platform()
        table = self.query_one(f"#links-{platform}", DataTable)
        rows = self.link_rows.get(platform, [])
        return rows[table.cursor_row] if rows and 0 <= table.cursor_row < len(rows) else None

    def on_input_changed(self, event: Input.Changed) -> None:
        self.refresh_data()

    def action_search(self) -> None:
        self.query_one("#links-search", Input).focus()

    def action_copy(self) -> None:
        row = self.selected_row()
        if row:
            self.app.copy_links(self.platform(), [row["url"]])

    def action_copy_all(self) -> None:
        rows = self.link_rows.get(self.platform(), [])
        if rows:
            self.app.copy_links(self.platform(), [r["url"] for r in rows])
        else:
            self.app.notify("No links to copy.", severity="warning")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = self.selected_row()
        if row:
            self.app.services.links.open(self.platform(), row["url"])
            self.app.notify("Opened in your browser")


# ------------------------------------------------------------------------------------------------
# Content inbox
# ------------------------------------------------------------------------------------------------

class ContentScreen(Page):
    MODE = "content"

    def body(self) -> ComposeResult:
        yield Static("Content", classes="title")
        yield Static("", id="content-root", classes="subtitle")
        with Horizontal(classes="cards"):
            for stage in ("incoming", "publishing", "published", "failed", "archive"):
                yield Static("", classes="stat", id=f"stage-{stage}")
        yield Static("PACKAGES IN THE INBOX", classes="section")
        yield DataTable(id="packages", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="buttons"):
            yield Button("Refresh", id="refresh")
            yield Button("Open Content Inbox (review & publish)", id="classic", variant="primary")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Package", "Status", "Video", "Cover", "Destinations", "Problems")
        self.refresh_data()

    def refresh_data(self) -> None:
        content = self.app.services.content
        self.query_one("#content-root", Static).update(f"[dim]{content.root}[/]  ·  drop a folder with video + caption.txt (+ cover.jpg) into incoming/")
        for stage, count in content.stage_counts().items():
            self.query_one(f"#stage-{stage}", Static).update(f"[b]{count}[/]\n{stage.title()}")
        table = self.query_one(DataTable)
        table.clear()
        for r in content.entries():
            table.add_row(r["name"], r["status"], r["video"], r["cover"], str(r["destinations"]), r["problems"])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "classic":
            from src.cli.content_menu import run_content_inbox

            run_classic(self.app, run_content_inbox, self.app.services.intake)
        self.refresh_data()


# ------------------------------------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------------------------------------

class SettingsScreen(Page):
    MODE = "settings"

    def body(self) -> ComposeResult:
        s = self.app.services.settings
        yield Static("Settings", classes="title")
        with TabbedContent(id="settings-tabs"):
            with TabPane("General", id="st-general"):
                yield Static("", id="general-info")
            with TabPane("Instagram", id="st-instagram"):
                for key, (label, description, low, high, _) in s.EDITABLE.items():
                    with Vertical(classes="panel"):
                        yield Label(f"[b]{label}[/]  [dim]({low}–{high})[/]")
                        yield Static(description, classes="muted")
                        yield Input(str(s.value(key)), id=f"set-{key}", type="integer")
                yield Button("Save", id="save", variant="primary")
                yield Static("[dim]Saved to .env (only these keys) and applied to new batches immediately.[/]")
            with TabPane("YouTube", id="st-youtube"):
                yield Static("", id="yt-info")
            with TabPane("TikTok", id="st-tiktok"):
                yield Static("", id="tt-info")
            with TabPane("Media", id="st-media"):
                yield Static("", id="media-info")
                yield Button("Run online checks", id="check")
                yield Static("", id="check-result")
            with TabPane("Advanced", id="st-advanced"):
                yield Static("Publishing profile (Content Inbox destinations, VERIFY/AUTO mode) and other classic "
                             "settings open in the terminal.", classes="muted")
                yield Button("Open classic settings", id="classic")

    def on_mount(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        s = self.app.services.settings
        oauth = s.oauth_status()
        self.query_one("#general-info", Static).update(
            f"Version  SOC_BOT v1.0.0\nContent  {self.app.services.content.root}\n"
            f"Links    {self.app.services.links.text_file('instagram').parent}\n"
            f"Log      logs/soc_bot.log (TUI diagnostics; never tokens or temporary URLs)")
        for platform, widget in (("youtube", "#yt-info"), ("tiktok", "#tt-info")):
            state = status_markup("available", "OAuth app configured") if oauth[platform] else \
                status_markup("not_configured", "OAuth app not configured (credentials in .env)")
            extra = ("\nPrivacy, title and 'made for kids' are chosen per post." if platform == "youtube" else
                     "\nPrivacy is chosen per post; unaudited apps can only post SELF_ONLY. TikTok returns no "
                     "public post URL, so no TikTok links are saved.")
            self.query_one(widget, Static).update(state + extra)
        self.query_one("#media-info", Static).update("\n".join(
            f"{status_markup(m.state)}  [b]{m.name}[/]  [dim]{m.text}[/]" for m in s.media_status()))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            s = self.app.services.settings
            try:
                for key in s.EDITABLE:
                    s.save(key, self.query_one(f"#set-{key}", Input).value)
            except ValueError as e:
                self.app.notify(str(e), severity="error")
                return
            self.app.notify("✓ Settings saved")
        elif event.button.id == "check":
            self.query_one("#check-result", Static).update("[dim]Checking…[/]")
            self.run_checks()
        elif event.button.id == "classic":
            from src.cli.content_menu import run_settings

            run_classic(self.app, run_settings, self.app.services.intake, self.app.services.account_manager)
            self.refresh_data()

    @work(thread=True, exclusive=True)
    def run_checks(self) -> None:
        from src.media_storage import create_media_provider

        try:
            message = create_media_provider().health_check()
            text = status_markup("available", "OK") + "\n" + message
        except Exception as e:  # noqa: BLE001 - shown to the user, no secrets in these messages
            text = status_markup("attention", "Problem") + f"\n{e}"
        self.app.call_from_thread(self.query_one("#check-result", Static).update, text)


__all__ = ["AccountsScreen", "BatchDetailScreen", "ContentScreen", "HistoryScreen", "LinksScreen", "QueueScreen",
           "SettingsScreen", "mb"]
