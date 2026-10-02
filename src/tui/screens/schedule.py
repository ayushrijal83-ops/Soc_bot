"""Scheduling dialogs (V2.2 phase 6). Presentation only: every decision is SchedulingService's.

ScheduleModal is used for "Schedule" (Create Post Review) and "Reschedule" (Queue). It shows the V2.1
suggestions and a custom date / time / timezone form; "Check time" asks SchedulingService.resolve_time
(range, DST gap, overlap) and shows the confirmation; SCHEDULE then calls the submit callback the caller
supplied (schedule / schedule_from_suggestion / reschedule / reschedule_from_suggestion). Errors stay inline
and the dialog stays open.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import ClassVar
from zoneinfo import ZoneInfo

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

from src.services.scheduling import (
    ScheduleView,
    SchedulingError,
    all_zones,
    friendly_error,
)
from src.tui.theme import status_markup

UTC = timezone.utc
NO_SUGGESTIONS = "none"


def until(moment: datetime | None, now: datetime | None = None) -> str:
    """'in 5h 12m' / 'overdue 3h 02m' (display only)."""
    if moment is None:
        return ""
    seconds = int((moment - (now or datetime.now(UTC))).total_seconds())
    minutes = abs(seconds) // 60
    days, hours, mins = minutes // 1440, minutes % 1440 // 60, minutes % 60
    text = f"{days}d {hours}h" if days else (f"{hours}h {mins:02d}m" if hours else f"{mins}m")
    return f"in {text}" if seconds >= 0 else f"overdue {text}"


def when_text(view: ScheduleView) -> str:
    if view.local is None:
        return "-"
    return f"{view.local:%Y-%m-%d %H:%M} {view.zone}  ({view.scheduled_at:%H:%M} UTC)"


class ScheduleModal(ModalScreen[ScheduleView | None]):
    BINDINGS: ClassVar[list] = [Binding("escape", "back", "Back")]
    DEFAULT_CSS = """
    #schedule-modal { height: 22; max-height: 95%; width: 76; }
    #sched-body { height: 1fr; }
    #sched-suggestions { height: auto; max-height: 5; }
    #sched-when Input { width: 1fr; }
    #sched-zone { width: 1fr; }
    #sched-preview { height: auto; min-height: 2; }
    """

    def __init__(self, title: str, snapshot: dict | None, submit_custom: Callable[[datetime, str | None], ScheduleView],
                 submit_suggestion: Callable, current: ScheduleView | None = None):
        super().__init__()
        self.title_text, self.snapshot, self.current = title, snapshot, current
        self.submit_custom, self.submit_suggestion = submit_custom, submit_suggestion
        self.recommendation = None
        self.choice = None  # ("suggestion", slot) or ("custom", local, zone-or-None) once checked

    @property
    def scheduling(self):
        return self.app.services.scheduling

    def compose(self) -> ComposeResult:
        zone, source, notes = self.scheduling.resolve_zone(self.snapshot)
        self.default_zone = zone
        audience = (self.snapshot or {}).get("name") or "none (Global)"
        info = [f"Audience  {escape(audience)}   ·   Timezone  [b]{zone}[/] [dim]({source.replace('_', ' ')})[/]"]
        info += [f"[dim]{escape(n)}[/]" for n in notes]
        if self.current is not None:
            info.insert(0, f"Currently {status_markup(self.current.status or '')}  {when_text(self.current)}")
        with Vertical(classes="modal", id="schedule-modal"):
            yield Label(self.title_text, classes="modal-title")
            yield Static("\n".join(info), id="sched-info")
            with VerticalScroll(id="sched-body"):
                yield Static("SUGGESTED TIMES  [dim](strategy only — does not control distribution)[/]",
                             classes="section")
                yield OptionList(id="sched-suggestions")
                yield Static("CUSTOM TIME", classes="section")
                with Horizontal(id="sched-when", classes="row"):
                    yield Input(placeholder="YYYY-MM-DD", id="sched-date", max_length=10)
                    yield Input(placeholder="HH:MM", id="sched-time", max_length=5)
                with Horizontal(classes="row"):
                    yield Select([(z, z) for z in all_zones()], value=zone, allow_blank=False, id="sched-zone")
                    yield Button("Check time", id="sched-check")
                yield Static("[dim]Pick a suggestion, or enter a date and time and press Check time.[/]",
                             id="sched-preview")
            with Horizontal(classes="buttons"):
                yield Button("⏰ SCHEDULE", variant="success", id="sched-confirm", disabled=True)
                yield Button("← Back", id="sched-back")

    def on_mount(self) -> None:
        self.load_suggestions()
        self.query_one("#sched-date", Input).focus()

    # --- suggestions ---------------------------------------------------------------------------

    def load_suggestions(self) -> None:
        self.recommendation = self.app.services.publishing.timing.recommend(self.snapshot, datetime.now(UTC))
        options = self.query_one("#sched-suggestions", OptionList)
        options.clear_options()
        zone = ZoneInfo(self.default_zone)
        for index, slot in enumerate(self.recommendation.slots):
            local = slot.start_utc.astimezone(zone)
            options.add_option(Option(f"{index + 1}. {local:%a %Y-%m-%d %H:%M} {self.default_zone}  ·  "
                                      f"{slot.start_utc:%H:%M}–{slot.end_utc:%H:%M} UTC", id=str(index)))
        if not self.recommendation.slots:
            note = self.recommendation.notes[0] if self.recommendation.notes else "No suggestions."
            options.add_option(Option(f"[dim]{escape(note)}[/]", id=NO_SUGGESTIONS, disabled=True))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id in (None, NO_SUGGESTIONS):
            return
        slot = self.recommendation.slots[int(event.option.id)]
        local = slot.start_utc.astimezone(ZoneInfo(self.default_zone))
        self.choice = ("suggestion", slot)
        self.show_confirmation(local, self.default_zone, slot.start_utc, ["From the suggested times."])

    # --- custom time ---------------------------------------------------------------------------

    def zone_choice(self) -> str | None:
        """None = let the service resolve (the default shown); otherwise the explicitly chosen zone."""
        value = self.query_one("#sched-zone", Select).value
        return None if value == self.default_zone else str(value)

    def check_custom(self) -> None:
        date = self.query_one("#sched-date", Input).value
        time = self.query_one("#sched-time", Input).value
        try:
            local = self.scheduling.parse_local(date, time)
            when = self.scheduling.resolve_time(local, self.snapshot, self.zone_choice())
        except SchedulingError as e:
            self.show_error(e)
            return
        self.choice = ("custom", local, self.zone_choice())
        self.show_confirmation(when.local, when.zone, when.utc, list(when.notes))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "sched-date":
            self.query_one("#sched-time", Input).focus()
        else:
            self.check_custom()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.reset_choice()

    def on_select_changed(self, event: Select.Changed) -> None:
        self.reset_choice()

    def reset_choice(self) -> None:
        if self.choice is not None and self.choice[0] == "custom":
            self.choice = None
            self.query_one("#sched-confirm", Button).disabled = True

    # --- confirmation / submit -----------------------------------------------------------------

    def show_confirmation(self, local: datetime, zone: str, utc: datetime, notes: list[str]) -> None:
        audience = (self.snapshot or {}).get("name") or "none (Global)"
        lines = ["[b]Schedule this post?[/]",
                 f"Date {local:%Y-%m-%d}   Time {local:%H:%M}   Timezone {zone}",
                 f"UTC  {utc.astimezone(UTC):%Y-%m-%d %H:%M}   Audience {escape(audience)}"]
        lines += [f"[dim]{escape(n)}[/]" for n in notes]
        self.query_one("#sched-preview", Static).update("\n".join(lines))
        button = self.query_one("#sched-confirm", Button)
        button.disabled = False
        button.focus()
        self.reveal_preview()  # the confirmation must be on screen before SCHEDULE can be pressed

    def show_error(self, error: SchedulingError) -> None:
        self.query_one("#sched-preview", Static).update(f"[$error]✗ {escape(friendly_error(error))}[/]")
        self.query_one("#sched-confirm", Button).disabled = True
        self.reveal_preview()

    def reveal_preview(self) -> None:
        body, preview = self.query_one("#sched-body", VerticalScroll), self.query_one("#sched-preview", Static)
        self.call_after_refresh(lambda: body.scroll_to_widget(preview, animate=False))

    def confirm(self) -> None:
        if self.choice is None:
            return
        try:
            if self.choice[0] == "suggestion":
                view = self.submit_suggestion(self.recommendation, self.choice[1])
            else:
                view = self.submit_custom(self.choice[1], self.choice[2])
        except SchedulingError as e:
            self.choice = None
            if e.code == "stale_suggestion":
                self.load_suggestions()
            self.show_error(e)
            if e.code in ("conflict", "invalid_state", "not_found"):
                self.app.notify(friendly_error(e), severity="warning", timeout=6)
                self.dismiss(None)
            return
        self.dismiss(view)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button == "sched-check":
            self.check_custom()
        elif button == "sched-confirm":
            self.confirm()
        elif button == "sched-back":
            self.dismiss(None)

    def action_back(self) -> None:
        self.dismiss(None)


class ScheduledActionsModal(ModalScreen[str | None]):
    """View a scheduled / missed post and choose: reschedule, publish now, cancel schedule, back."""

    BINDINGS: ClassVar[list] = [Binding("escape", "back", "Back")]
    DEFAULT_CSS = "#scheduled-actions { max-height: 95%; } #sa-details { height: auto; max-height: 12; }"

    def __init__(self, view: ScheduleView):
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        v = self.view
        history = " → ".join(h.get("action", "?") for h in v.history) or "-"
        lines = [f"{status_markup(v.status or '')}   {when_text(v)}   [dim]{until(v.scheduled_at)}[/]",
                 f"Video     {escape(v.video or '-')}",
                 f"Audience  {escape(v.audience or 'none (Global)')}   ·   {v.destinations} destination(s)",
                 f"Source    {v.source or '-'}" + (f"   ·   [dim]{escape(v.overlap_note)}[/]" if v.overlap_note else ""),
                 f"History   [dim]{escape(history)}[/]"]
        with Vertical(classes="modal", id="scheduled-actions"):
            yield Label(f"Scheduled post #{v.post_id}", classes="modal-title")
            yield Static("\n".join(lines), id="sa-details")
            with Horizontal(classes="buttons"):
                yield Button("Reschedule", id="reschedule", variant="primary")
                yield Button("Publish now", id="publish_now", variant="success")
                yield Button("Cancel schedule", id="cancel", variant="error")
                yield Button("Back", id="back")

    def on_mount(self) -> None:
        self.query_one("#reschedule", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "back" else event.button.id)

    def action_back(self) -> None:
        self.dismiss(None)


__all__ = ["ScheduleModal", "ScheduledActionsModal", "until", "when_text"]
