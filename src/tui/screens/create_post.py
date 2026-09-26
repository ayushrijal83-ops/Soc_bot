"""Create Post: a 5-step wizard (Media → Caption → Destinations → Options → Review).

Everything here is local validation through the service layer. Nothing is uploaded, no tunnel is
started and no job is created until Publish is confirmed.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    Input,
    Label,
    Select,
    SelectionList,
    Static,
    TextArea,
)
from textual.widgets.selection_list import Selection

from src.platforms.tiktok.publisher import PRIVACY_LEVELS
from src.platforms.youtube.publisher import PRIVACY_STATUSES
from src.tui.theme import PLATFORM_NAMES, mb, platform_markup, status_markup
from src.tui.widgets import ConfirmModal, FilePickerModal, Page

STEPS = ["Media", "Caption", "Destinations", "Options", "Review"]
VIDEO_EXT = (".mp4", ".mov", ".webm")


class CreatePostScreen(Page):
    MODE = "create"
    BINDINGS: ClassVar[list] = [
        Binding("ctrl+n", "next", "Next"),
        Binding("p", "publish", "PUBLISH"),
        Binding("ctrl+enter", "publish", "Publish", show=False),
        Binding("ctrl+b", "back", "Back"),
        Binding("escape", "back", "Back", show=False),
        Binding("slash", "search", "Search", show=False),
        Binding("a", "select_all", "All", show=False),
        Binding("n", "select_none", "None", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.step = 0
        self.video = None
        self.cover = None
        self.platforms: set[str] = set()
        self.selected: set[int] = set()
        self.plan = None

    def body(self) -> ComposeResult:
        yield Static("Create Post", classes="title")
        yield Static("", id="steps")
        with ContentSwitcher(initial="step-0", id="switcher"):
            with VerticalScroll(id="step-0"):
                yield Static("VIDEO", classes="section")
                with Horizontal(classes="row"):
                    yield Input(placeholder="Path to the video (MP4/MOV)", id="video-path")
                    yield Button("Browse…", id="browse-video")
                yield Static("[dim]No video selected.[/]", id="video-card", classes="panel")
                yield Static("COVER (optional)", classes="section")
                with Horizontal(classes="row"):
                    yield Input(placeholder="Path to cover.jpg (optional)", id="cover-path")
                    yield Button("Browse…", id="browse-cover")
                    yield Button("Clear", id="clear-cover")
                yield Static("[dim]No custom cover selected.[/]", id="cover-card", classes="panel")
                yield Static("One cover will be used for every selected Instagram account.", classes="muted")
            with Vertical(id="step-1"):
                yield Static("CAPTION", classes="section")
                yield TextArea(id="caption", classes="fill")
                yield Static("0 characters", id="caption-count", classes="muted")
            with Vertical(id="step-2"):
                yield Static("PLATFORMS", classes="section")
                with Horizontal(classes="cards", id="platform-picker"):
                    for platform in ("instagram", "youtube", "tiktok"):
                        yield Button("", id=f"pick-{platform}", classes="card")
                yield Input(placeholder="/ Search accounts", id="search")
                yield SelectionList[int](id="accounts", classes="fill")
                yield Static("", id="selected-count", classes="muted")
                yield Static("[dim]Space toggle · A all · N none · / search[/]", classes="muted")
            with VerticalScroll(id="step-3"):
                yield Static("", id="options-intro", classes="muted")
                with Vertical(id="yt-options", classes="panel"):
                    yield Static(platform_markup("youtube") + " options", classes="panel-title")
                    yield Label("Title")
                    yield Input(id="yt-title", max_length=100)
                    yield Label("Privacy")
                    yield Select([(p.title(), p) for p in PRIVACY_STATUSES], value="private", allow_blank=False,
                                 id="yt-privacy")
                    yield Checkbox("Made for kids", id="yt-kids")
                with Vertical(id="tt-options", classes="panel"):
                    yield Static(platform_markup("tiktok") + " options", classes="panel-title")
                    yield Label("Privacy (unaudited apps can only use SELF_ONLY)")
                    yield Select([(p, p) for p in PRIVACY_LEVELS], value="SELF_ONLY", allow_blank=False,
                                 id="tt-privacy")
                with Vertical(id="ig-options", classes="panel"):
                    yield Static(platform_markup("instagram") + " options", classes="panel-title")
                    yield Static("Caption and cover from the previous steps. No other options needed.", classes="muted")
            with VerticalScroll(id="step-4"):
                yield Static("", id="review", classes="panel")
                yield Checkbox("Also publish to accounts that already have this video", id="include-dups")
        # Docked action bar: always visible, whatever the (scrolling) step content needs.
        with Horizontal(id="actions"):
            yield Button("▶ PUBLISH NOW", variant="success", id="publish")
            yield Button("Next →", variant="primary", id="next")
            yield Button("← Back", id="back")
            yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.refresh_platform_cards()
        self.show_step(0)

    # --- step handling ------------------------------------------------------------------------

    def show_step(self, step: int) -> None:
        self.step = step
        self.query_one("#switcher", ContentSwitcher).current = f"step-{step}"
        parts = []
        for index, name in enumerate(STEPS):
            if index < step:
                parts.append(f"[#4ade80]✓ {name}[/]")
            elif index == step:
                parts.append(f"[b reverse] {index + 1} {name} [/]")
            else:
                parts.append(f"[dim]{index + 1} {name}[/]")
        self.query_one("#steps", Static).update("  ›  ".join(parts))
        self.query_one("#back", Button).disabled = step == 0
        self.query_one("#next", Button).display = step < len(STEPS) - 1
        self.query_one("#publish", Button).display = step == len(STEPS) - 1
        self.refresh_bindings()  # footer: Next on steps 1-4, PUBLISH on Review
        if step == 1:
            self.query_one("#caption", TextArea).focus()
        if step == 2:
            self.refresh_accounts()
        if step == 3:
            self.prepare_options()
        if step == 4:
            self.build_review()

    def check_action(self, action: str, parameters) -> bool | None:
        last = self.step == len(STEPS) - 1
        if action == "next":
            return not last
        if action == "publish":
            return last
        return True

    def action_publish(self) -> None:
        button = self.query_one("#publish", Button)
        if self.step == len(STEPS) - 1 and not button.disabled:
            button.focus()
            self.confirm_publish()

    def action_next(self) -> None:
        problem = self.step_problem()
        if problem:
            self.app.notify(problem, severity="warning")
            return
        if self.step < len(STEPS) - 1:
            self.show_step(self.step + 1)

    def action_back(self) -> None:
        if self.step > 0:
            self.show_step(self.step - 1)

    def step_problem(self) -> str | None:
        if self.step == 0:
            self.load_video()
            self.load_cover()
            if self.video is None or not self.video.ok:
                return "Choose a valid video first."
            if self.cover is not None and not self.cover.ok:
                return "The cover is invalid. Fix it or clear it."
        if self.step == 2 and not self.selected:
            return "Select at least one account."
        return None

    # --- step 1: media -------------------------------------------------------------------------

    def load_video(self) -> None:
        path = self.query_one("#video-path", Input).value.strip()
        card = self.query_one("#video-card", Static)
        if not path:
            self.video = None
            card.update("[dim]No video selected.[/]")
            return
        self.video = self.app.services.publishing.inspect_video(path)
        v = self.video
        lines = [f"[b]{v.name}[/]", f"{mb(v.size_bytes)}  ·  {v.mime_type or 'unknown type'}"]
        if v.duration:
            lines.append(f"Duration {v.duration:.0f} s")
        if v.resolution:
            lines.append(f"Resolution {v.resolution}")
        lines.append(status_markup("ready", "Valid") if v.ok else status_markup("failed", "; ".join(v.errors)))
        for warning in v.warnings:
            lines.append(f"[dim]{warning}[/]")
        card.update("\n".join(lines))

    def load_cover(self) -> None:
        path = self.query_one("#cover-path", Input).value.strip()
        card = self.query_one("#cover-card", Static)
        if not path:
            self.cover = None
            card.update("[dim]No custom cover selected.[/]")
            return
        self.cover = self.app.services.publishing.inspect_cover(path)
        c = self.cover
        dims = f"{c.width} × {c.height}" if c.width else "?"
        state = status_markup("ready", "Valid") if c.ok else status_markup("failed", "; ".join(c.errors))
        card.update(f"[b]{c.name}[/]\n{dims}  ·  JPEG  ·  {mb(c.size_bytes)}\n{state}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "video-path":
            self.load_video()
        elif event.input.id == "cover-path":
            self.load_cover()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.refresh_accounts()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id or ""
        if button == "next":
            self.action_next()
        elif button == "back":
            self.action_back()
        elif button == "cancel":
            self.app.push_screen(ConfirmModal("Cancel post?", "Discard this post? Nothing was published.",
                                              "Discard", danger=True), self._cancelled)
        elif button == "publish":
            self.confirm_publish()
        elif button == "browse-video":
            self.app.push_screen(FilePickerModal("Choose a video", self.start_dir(), VIDEO_EXT), self._video_picked)
        elif button == "browse-cover":
            self.app.push_screen(FilePickerModal("Choose a cover (JPEG)", self.start_dir(), (".jpg", ".jpeg")),
                                 self._cover_picked)
        elif button == "clear-cover":
            self.query_one("#cover-path", Input).value = ""
            self.load_cover()
        elif button.startswith("pick-"):
            self.toggle_platform(button.removeprefix("pick-"))

    def start_dir(self) -> Path:
        current = self.query_one("#video-path", Input).value.strip().strip('"')
        if current and Path(current).parent.is_dir():
            return Path(current).parent
        videos = Path(__file__).resolve().parents[3] / "videos"
        return videos if videos.is_dir() else Path.home()

    def _video_picked(self, path: str | None) -> None:
        if path:
            self.query_one("#video-path", Input).value = path
            self.load_video()

    def _cover_picked(self, path: str | None) -> None:
        if path:
            self.query_one("#cover-path", Input).value = path
            self.load_cover()

    def _cancelled(self, discard: bool | None) -> None:
        if discard:
            self.reset_form()
            self.app.navigate("dashboard")

    # --- step 2: caption --------------------------------------------------------------------

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        text = event.text_area.text
        self.query_one("#caption-count", Static).update(f"{len(text)} characters  ·  {text.count('#')} hashtags")

    # --- step 3: destinations -------------------------------------------------------------------

    def refresh_platform_cards(self) -> None:
        summaries = {s.platform: s for s in self.app.services.accounts.summary()}
        for platform in ("instagram", "youtube", "tiktok"):
            s = summaries[platform]
            button = self.query_one(f"#pick-{platform}", Button)
            state = "✓ Selected" if platform in self.platforms else ("● Ready" if s.ready else "○ Not set up")
            button.label = f"{PLATFORM_NAMES[platform]}\n{s.connected} connected\n{state}"
            button.disabled = s.ready == 0
            button.set_class(platform in self.platforms, "selected")

    def toggle_platform(self, platform: str) -> None:
        if platform in self.platforms:
            self.platforms.discard(platform)
            self.selected = {a for a in self.selected if self._platform_of(a) != platform}
        else:
            self.platforms.add(platform)
        self.refresh_platform_cards()
        self.refresh_accounts()

    def _platform_of(self, account_id: int) -> str:
        return next((a.platform for a in self.app.services.accounts.list() if a.id == account_id), "")

    def refresh_accounts(self) -> None:
        lst = self.query_one("#accounts", SelectionList)
        needle = self.query_one("#search", Input).value.strip().lower()
        views = [a for a in self.app.services.accounts.list() if a.platform in self.platforms and a.status != "disconnected"]
        self._shown_accounts = [a for a in views if not needle or needle in a.label.lower() or needle in a.platform]
        with lst.prevent(SelectionList.SelectedChanged):
            lst.clear_options()
            lst.add_options([Selection(f"{platform_markup(a.platform)}  @{a.label}   {status_markup(a.status, a.status_text)}",
                                       a.id, a.id in self.selected) for a in self._shown_accounts])
        total = len(views)
        self.query_one("#selected-count", Static).update(
            f"Selected: [b]{len(self.selected)}[/] / {total}" if self.platforms else "Choose one or more platforms above.")

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        visible = {a.id for a in getattr(self, "_shown_accounts", [])}
        self.selected = (self.selected - visible) | set(event.selection_list.selected)
        total = len([a for a in self.app.services.accounts.list() if a.platform in self.platforms and a.status != "disconnected"])
        self.query_one("#selected-count", Static).update(f"Selected: [b]{len(self.selected)}[/] / {total}")

    def action_search(self) -> None:
        if self.step == 2:
            self.query_one("#search", Input).focus()

    def action_select_all(self) -> None:
        if self.step == 2:
            self.query_one("#accounts", SelectionList).select_all()
        else:
            self.app.navigate("accounts")  # "A" is Accounts everywhere except the account selector

    def action_select_none(self) -> None:
        if self.step == 2:
            self.query_one("#accounts", SelectionList).deselect_all()

    # --- step 4: options ----------------------------------------------------------------------

    def chosen_platforms(self) -> set[str]:
        return {self._platform_of(a) for a in self.selected}

    def prepare_options(self) -> None:
        platforms = self.chosen_platforms()
        self.query_one("#yt-options").display = "youtube" in platforms
        self.query_one("#tt-options").display = "tiktok" in platforms
        self.query_one("#ig-options").display = "instagram" in platforms
        title = self.query_one("#yt-title", Input)
        if not title.value:
            caption = self.query_one("#caption", TextArea).text
            title.value = (caption.splitlines()[0] if caption.strip() else (self.video.name if self.video else ""))[:100]
        self.query_one("#options-intro", Static).update("Only the options your selected platforms need.")

    def platform_options(self) -> dict[str, dict]:
        return {
            "youtube": {"title": self.query_one("#yt-title", Input).value.strip() or (self.video.name if self.video else ""),
                        "privacy_status": self.query_one("#yt-privacy", Select).value,
                        "made_for_kids": self.query_one("#yt-kids", Checkbox).value},
            "tiktok": {"privacy_level": self.query_one("#tt-privacy", Select).value},
            "instagram": {},
        }

    # --- step 5: review ------------------------------------------------------------------------

    def build_review(self) -> None:
        caption = self.query_one("#caption", TextArea).text
        self.plan = self.app.services.publishing.plan(
            self.video.path, caption, self.cover.path if self.cover and self.cover.ok else None,
            sorted(self.selected), self.platform_options())
        p = self.plan
        lines = ["[b $primary]READY TO PUBLISH[/]", "",
                 f"Video        [b]{self.video.name}[/]  ({mb(p.size_bytes)})",
                 f"Cover        {(self.cover.name + '  →  same cover for all Instagram accounts') if self.cover else 'none'}",
                 f"Caption      {len(caption)} characters", ""]
        for platform in ("instagram", "youtube", "tiktok"):
            if p.count(platform):
                lines.append(f"{platform_markup(platform):<30} {p.count(platform)} account(s)")
        if p.instagram_jobs:
            lines += ["", (f"Instagram concurrency   {p.concurrency} at a time  (initial running {p.initial_active}, "
                           f"pending {p.instagram_jobs - p.initial_active})"),
                      f"Media delivery          {p.provider_name}"
                      + ("  ·  1 shared tunnel for this batch" if p.shared_tunnel else ""),
                      f"Automatic retry         failed Instagram jobs retried once, after {p.retry_delay:g} s",
                      f"Approx. upload traffic  {mb(p.transfer_bytes)} (each account fetches its own copy)",
                      "Temporary URLs          created only after you publish, never saved"]
        if p.invalid:
            lines += ["", f"[b $error]✗ {len(p.invalid)} account(s) blocked:[/]"]
            lines += [f"  ✗ @{d.account_label}: {'; '.join(d.errors)[:140]}" for d in p.invalid]
        dups = [d for d in p.valid if d.already_published]
        self.query_one("#include-dups", Checkbox).display = bool(dups)
        if dups:
            lines += ["", f"[b $warning]⚠ Already published to {len(dups)} account(s):[/] "
                          + ", ".join("@" + d.account_label for d in dups[:10]) + (" …" if len(dups) > 10 else ""),
                      "[dim]They are skipped unless you tick the box below.[/]"]
        self.query_one("#review", Static).update("\n".join(lines))
        self.query_one("#publish", Button).disabled = not p.valid
        self.query_one("#publish", Button).focus()

    def targets(self) -> int:
        include = self.query_one("#include-dups", Checkbox).value
        return sum(1 for d in self.plan.valid if include or not d.already_published)

    def confirm_publish(self) -> None:
        if not self.plan or not self.plan.valid:
            self.app.notify("Nothing to publish.", severity="warning")
            return
        count = self.targets()
        if not count:
            self.app.notify("Every selected account already has this video.", severity="warning")
            return
        self.app.push_screen(ConfirmModal("Publish now?", f"Publish to [b]{count}[/] account(s)? "
                                          "This creates real posts.", "Publish"), self._publish)

    def _publish(self, confirmed: bool | None) -> None:
        if not confirmed:
            return
        include = self.query_one("#include-dups", Checkbox).value
        post_id = self.app.services.publishing.create_batch(self.plan, include_already_published=include)
        from src.tui.screens.publishing import PublishingScreen

        self.reset_form()
        self.app.push_screen(PublishingScreen(post_id=post_id))

    def reset_form(self) -> None:
        """A fresh, empty post for next time (nothing kept from the published one)."""
        for selector in ("#video-path", "#cover-path", "#search", "#yt-title"):
            self.query_one(selector, Input).value = ""
        self.query_one("#caption", TextArea).text = ""
        self.query_one("#include-dups", Checkbox).value = False
        self.video = self.cover = self.plan = None
        self.platforms, self.selected = set(), set()
        self.load_video()
        self.load_cover()
        self.refresh_platform_cards()
        self.show_step(0)
