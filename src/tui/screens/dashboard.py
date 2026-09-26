from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ProgressBar, Static

from src.tui.theme import PLATFORM_NAMES, platform_markup, status_markup
from src.tui.widgets import Page


class DashboardScreen(Page):
    MODE = "dashboard"
    SCROLL = True

    def body(self) -> ComposeResult:
        yield Static("Dashboard", classes="title")
        with Horizontal(classes="cards", id="platform-cards"):
            for platform in ("instagram", "youtube", "tiktok"):
                yield Static("", classes="card", id=f"pc-{platform}")
        yield Static("PUBLISHING", classes="section")
        with Horizontal(classes="cards"):
            for key in ("running", "pending", "retrying", "published", "failed"):
                yield Static("", classes="stat", id=f"stat-{key}")
        with Horizontal(classes="row"):
            with Vertical(classes="panel", id="batch-panel"):
                yield Static("Current batch", classes="panel-title")
                yield Static("", id="batch-info")
                yield ProgressBar(total=1, show_eta=False, id="batch-progress")
            with Vertical(classes="panel", id="system-panel"):
                yield Static("System", classes="panel-title")
                yield Static("", id="system-info")
        with Vertical(classes="panel"):
            yield Static("Recent activity", classes="panel-title")
            yield Static("", id="activity")

    def on_mount(self) -> None:
        self.refresh_data()
        self.set_interval(3, self.refresh_data)

    def refresh_data(self) -> None:
        services = self.app.services
        for summary in services.accounts.summary():
            name = PLATFORM_NAMES[summary.platform]
            unit = "channels" if summary.platform == "youtube" else "accounts"
            if summary.connected:
                state = status_markup("ready", f"{summary.ready} ready")
            elif summary.configured:
                state = status_markup("not_configured", "Connect an account")
            else:
                state = status_markup("not_configured", "Not set up")
            self.query_one(f"#pc-{summary.platform}", Static).update(
                f"{platform_markup(summary.platform)}\n[b]{summary.connected}[/] {unit}\n{state}")
            self.query_one(f"#pc-{summary.platform}").tooltip = f"{name}: {summary.connected} connected"
        counts = services.publishing.queue_counts()
        running = counts["uploading"] + counts["processing"]
        values = {"running": running, "pending": counts["pending"], "retrying": counts["retrying"],
                  "published": counts["published"], "failed": counts["failed"]}
        labels = {"running": "→ Running", "pending": "○ Pending", "retrying": "↻ Retrying",
                  "published": "✓ Published", "failed": "✗ Failed"}
        for key, value in values.items():
            self.query_one(f"#stat-{key}", Static).update(f"[b]{value}[/]\n{labels[key]}")
        batches = services.publishing.batches(limit=1)
        info = self.query_one("#batch-info", Static)
        bar = self.query_one("#batch-progress", ProgressBar)
        if batches:
            b = batches[0]
            platforms = ", ".join(PLATFORM_NAMES.get(p, p) for p in b.platforms)
            info.update(f"[b]{b.video[:36]}[/]\n{platforms}  ·  {b.total} account(s)\n"
                        f"{status_markup(b.status)}   ✓ {b.counts.get('published', 0)} published   "
                        f"✗ {b.counts.get('failed', 0)} failed   ○ {b.counts.get('pending', 0)} pending")
            bar.update(total=max(b.total, 1), progress=b.done)
        else:
            info.update("[dim]No batches yet. Press C to create a post.[/]")
            bar.update(total=1, progress=0)
        media = {m.name: m for m in services.settings.media_status()}
        tunnel = media["Cloudflare Quick Tunnel"]
        self.query_one("#system-info", Static).update(
            f"{status_markup('ready', 'System ready')}\n"
            f"Media delivery: {status_markup(tunnel.state, 'Cloudflare ' + ('available' if tunnel.state == 'available' else 'not set up'))}\n"
            f"Links saved: {sum(services.links.counts().values())}")
        rows = []
        for job in services.publishing.recent_activity(limit=8):
            rows.append(f"{status_markup(job.status)}  {platform_markup(job.platform)}  @{job.account_label}")
        self.query_one("#activity", Static).update("\n".join(rows) or "[dim]No activity yet.[/]")
