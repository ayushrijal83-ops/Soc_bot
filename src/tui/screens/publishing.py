"""Live publishing: runs one batch (or a manual retry / resume) in a worker thread and shows progress.

The engine calls back from its worker threads; events are handed to the UI thread with
``call_from_thread``. No temporary URL, token or container id ever reaches this screen.
"""

from __future__ import annotations

from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, ProgressBar, Static

from src.services import BatchEvent
from src.tui.theme import (
    AMBER,
    BLUE,
    GREEN,
    PLATFORM_NAMES,
    RED,
    platform_markup,
    status_markup,
)
from src.tui.widgets import MessageModal


class PublishingScreen(Screen):
    BINDINGS: ClassVar[list] = [Binding("escape", "leave", "Back"), Binding("enter", "details", "Details", show=False)]

    def __init__(self, post_id: int | None = None, retry_job_id: int | None = None, resume: bool = False):
        super().__init__()
        self.post_id, self.retry_job_id, self.resume = post_id, retry_job_id, resume
        self.rows: dict[int, dict] = {}
        self.running = True
        self.final: dict | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="content"):
            yield Static("PUBLISHING BATCH", classes="title", id="pub-title")
            yield Static("", id="pub-info")
            with Horizontal(classes="row"):
                yield ProgressBar(total=1, show_eta=False, id="overall")
            yield Static("", id="counts")
            yield Static("", id="media")
            yield Static("", id="round", classes="panel")
            yield DataTable(id="jobs", cursor_type="row", zebra_stripes=True)
            with Horizontal(classes="buttons", id="final-buttons"):
                yield Button("View links", id="links", variant="primary")
                yield Button("View failed", id="failed")
                yield Button("Queue", id="queue")
                yield Button("Dashboard", id="dashboard")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#jobs", DataTable)
        table.add_columns("", "Account", "Platform", "Status", "Detail")
        self.query_one("#round").display = False
        self.query_one("#final-buttons").display = False
        self.app.publishing_active = True
        if self.post_id is not None:
            self.load_batch(self.post_id)
        self.run_batch()

    def load_batch(self, post_id: int) -> None:
        view = self.app.services.publishing.batch(post_id)
        platforms = ", ".join(f"{PLATFORM_NAMES.get(p, p)} {sum(j.platform == p for j in view.jobs)}"
                              for p in view.platforms)
        self.query_one("#pub-info", Static).update(
            f"Video [b]{view.video}[/]   ·   Cover {view.cover or 'none'}   ·   {platforms}")
        for job in view.jobs:
            self.rows.setdefault(job.job_id, {"account": job.account_label, "platform": job.platform,
                                              "status": job.status, "detail": "", "details": None})
        self.render_rows()

    @work(thread=True, exclusive=True, group="publish")
    def run_batch(self) -> None:
        service = self.app.services.publishing

        def emit(event: BatchEvent) -> None:
            self.app.call_from_thread(self.handle_event, event)

        try:
            if self.retry_job_id is not None:
                service.retry_job(self.retry_job_id, emit)
            elif self.resume:
                service.resume_open(emit)
            else:
                service.publish_batch(self.post_id, emit)
        except Exception as e:  # noqa: BLE001 - never crash the UI; the engine already isolates jobs
            self.app.call_from_thread(self.app.notify, f"Publishing stopped: {type(e).__name__}", severity="error")
        finally:
            self.app.call_from_thread(self.finished)

    # --- events (UI thread) ----------------------------------------------------------------

    def handle_event(self, event: BatchEvent) -> None:
        if event.kind == "batch_started":
            has_instagram = any(r["platform"] == "instagram" for r in self.rows.values()) or self.resume
            self.query_one("#media", Static).update(
                "MEDIA DELIVERY  " + status_markup("ready", "Active (one shared session for this batch)")
                if has_instagram else "")
        elif event.kind == "retry_started":
            s = event.summary or {}
            round_panel = self.query_one("#round", Static)
            round_panel.display = True
            round_panel.update(f"[b]INITIAL ROUND COMPLETE[/]   ✓ Published {s.get('published', 0)}   "
                               f"✗ Failed {s.get('failed', 0)}\n"
                               "↻ Starting automatic retry: failed accounts are retried [b]once[/] (same tunnel).")
            self.app.notify("Initial round complete: starting automatic retry", severity="warning")
        elif event.kind == "batch_finished":
            self.final = event.summary or {}
        elif event.job_id is not None:
            row = self.rows.setdefault(event.job_id, {"account": event.account, "platform": event.platform,
                                                      "status": "pending", "detail": "", "details": None})
            row["status"] = event.status
            if event.status == "failed":
                row["detail"], row["details"] = event.message or "Failed", event.details
                self.app.notify(f"@{event.account}: {event.message}", severity="error", timeout=6)
            elif event.status == "retrying":
                row["detail"] = "automatic retry" if event.details is None else ""
            elif event.status == "published":
                row["detail"], row["details"] = "", None
        self.render_rows()

    def render_rows(self) -> None:
        table = self.query_one("#jobs", DataTable)
        cursor = table.cursor_row
        table.clear()
        for job_id, row in self.rows.items():
            icon = status_markup(row["status"]).split(" ")[0] + "[/]"
            table.add_row(icon, f"@{row['account']}", platform_markup(row["platform"]),
                          status_markup(row["status"]), row["detail"] or "", key=str(job_id))
        if self.rows:
            table.move_cursor(row=min(cursor, len(self.rows) - 1))
        statuses = [r["status"] for r in self.rows.values()]
        total = len(statuses)
        published, failed = statuses.count("published"), statuses.count("failed")
        running = sum(s in ("uploading", "processing") for s in statuses)
        self.query_one("#overall", ProgressBar).update(total=max(total, 1), progress=published + failed)
        limit = self.app.services.publishing.engine.max_concurrent
        from src.core.publisher import max_concurrent_publishes

        limit = limit or max_concurrent_publishes()
        self.query_one("#counts", Static).update(
            f"[{GREEN}]✓ Published {published}[/]   [{BLUE}]→ Running {running}[/] (active {running}/{limit})   "
            f"[{AMBER}]↻ Retrying {statuses.count('retrying')}[/]   ○ Pending {statuses.count('pending')}   "
            f"[{RED}]✗ Failed {failed}[/]")

    def finished(self) -> None:
        self.running = False
        self.app.publishing_active = False
        if self.post_id is not None:
            view = self.app.services.publishing.batch(self.post_id)
            for job in view.jobs:
                row = self.rows.setdefault(job.job_id, {"account": job.account_label, "platform": job.platform,
                                                        "status": job.status, "detail": "", "details": None})
                row["status"] = job.status
                if job.status == "failed":
                    row["detail"], row["details"] = job.error or "Failed", job.error_details
        self.render_rows()
        s = self.final or {}
        statuses = [r["status"] for r in self.rows.values()]
        published, failed = statuses.count("published"), statuses.count("failed")
        lines = ["[b]PUBLISH COMPLETE[/]" if not failed else "[b]PUBLISH COMPLETE WITH FAILURES[/]",
                 f"✓ Published: {published}    ✗ Failed: {failed}    Links saved: {s.get('links_saved', 0)}"]
        if s.get("retried"):
            lines.append(f"RETRY ROUND COMPLETE   ↻ Recovered {s.get('recovered', 0)}   ✗ Still failed {s.get('still_failed', 0)}")
        if any(r["platform"] == "instagram" for r in self.rows.values()):
            lines.append("Media session: " + status_markup("disconnected", "Closed"))
            self.query_one("#media", Static).update("MEDIA DELIVERY  " + status_markup("disconnected", "Closed"))
        panel = self.query_one("#round", Static)
        panel.display = True
        panel.update("\n".join(lines))
        self.query_one("#final-buttons").display = True
        self.query_one("#pub-title", Static).update("BATCH FINISHED")
        severity = "information" if not failed else "warning"
        self.app.notify(f"✓ {published} published" + (f", ✗ {failed} failed" if failed else ""), severity=severity)

    # --- actions ---------------------------------------------------------------------------------

    def action_details(self) -> None:
        table = self.query_one("#jobs", DataTable)
        if not self.rows or table.cursor_row < 0:
            return
        job_id = int(table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value)
        row = self.rows[job_id]
        if row["status"] == "failed":
            self.app.push_screen(MessageModal(f"@{row['account']}", f"✗ Publishing failed\n\n{row['detail']}",
                                              row.get("details")))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_details()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        target = event.button.id
        if target == "failed":
            failed = [r for r in self.rows.values() if r["status"] == "failed"]
            text = "\n".join(f"✗ @{r['account']}: {r['detail']}" for r in failed) or "No failed accounts."
            self.app.push_screen(MessageModal("Failed accounts", text))
            return
        self.app.pop_screen()
        self.app.navigate(target)

    def action_leave(self) -> None:
        if self.running:
            self.app.notify("Publishing continues in the background. Open Queue to follow it.", severity="information")
        self.app.pop_screen()
