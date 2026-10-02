"""Classic-menu scheduling (V2.2 phase 6): choose a time, and act on scheduled posts.

Presentation only. Times, zones, DST, ranges and every state change belong to SchedulingService; this module
only asks, shows and calls it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.cli.display import (
    confirm,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
)
from src.cli.prompts import prompt_choice, prompt_int, prompt_text
from src.core.publish_lock import BUSY_MESSAGE, PublishLockBusy, default_lock
from src.services.scheduling import (
    ScheduleView,
    SchedulingError,
    SchedulingService,
    friendly_error,
)

UTC = timezone.utc


def when_text(view: ScheduleView) -> str:
    if view.local is None:
        return "-"
    return f"{view.local:%Y-%m-%d %H:%M} {view.zone} ({view.scheduled_at:%Y-%m-%d %H:%M} UTC)"


def ask_schedule_time(sched: SchedulingService, snapshot: dict | None,
                      submit_custom: Callable[[datetime, str | None], ScheduleView], submit_suggestion: Callable,
                      current: ScheduleView | None = None) -> ScheduleView | None:
    """Suggested or custom time -> confirmation -> submit. Errors are shown and the user may try again.
    Returns the new schedule, or None if the user cancelled."""
    zone, source, notes = sched.resolve_zone(snapshot)
    print_header("SCHEDULE POST" if current is None else f"RESCHEDULE POST #{current.post_id}")
    if current is not None:
        print_info(f"Currently: {current.status} at {when_text(current)}")
    print_info(f"Audience: {(snapshot or {}).get('name') or 'none (Global)'}   Timezone: {zone} "
               f"({source.replace('_', ' ')})")
    for note in notes:
        print_info(note)
    while True:
        rec = sched.timing.recommend(snapshot, datetime.now(UTC))
        labels = []
        for slot in rec.slots:
            local = slot.start_utc.astimezone(ZoneInfo(zone))
            labels.append(f"{local:%a %Y-%m-%d %H:%M} {zone}  ({slot.start_utc:%H:%M}-{slot.end_utc:%H:%M} UTC, "
                          "suggested)")
        if not rec.slots and rec.notes:
            print_info(rec.notes[0])
        options = [*labels, "Custom date/time", "Cancel"]
        print_info("Suggested times are strategy only: they do not control who sees the post.")
        choice = prompt_choice("When", options, default=len(labels) + 1)
        if choice is None or choice == len(options):
            return None
        if choice <= len(labels):
            slot = rec.slots[choice - 1]
            preview = slot.start_utc.astimezone(ZoneInfo(zone)), zone, slot.start_utc, []
            submit = lambda s=slot, r=rec: submit_suggestion(r, s)
        else:
            date = prompt_text("Date (YYYY-MM-DD)")
            time = prompt_text("Time (HH:MM)")
            chosen = prompt_text(f"Timezone (IANA name, Enter = {zone})", required=False, default="")
            if date is None or time is None or chosen is None:
                return None
            explicit = chosen.strip() or None
            try:
                local = sched.parse_local(date, time)
                when = sched.resolve_time(local, snapshot, explicit)
            except SchedulingError as e:
                print_error(friendly_error(e))
                continue
            preview = when.local, when.zone, when.utc, list(when.notes)
            submit = lambda l=local, z=explicit: submit_custom(l, z)
        local, shown_zone, utc, extra = preview
        print_header("CONFIRM")
        print(f"Date:     {local:%Y-%m-%d}\nTime:     {local:%H:%M}\nTimezone: {shown_zone}\n"
              f"UTC:      {utc.astimezone(UTC):%Y-%m-%d %H:%M}\n"
              f"Audience: {(snapshot or {}).get('name') or 'none (Global)'}")
        for note in extra:
            print_info(note)
        step = prompt_choice("Schedule this post?", ["SCHEDULE", "Back", "Cancel"], default=1)
        if step is None or step == 3:
            return None
        if step == 2:
            continue
        try:
            return submit()
        except SchedulingError as e:
            print_error(friendly_error(e))
            if e.code in ("conflict", "invalid_state", "not_found"):
                return None


def print_scheduled(sched: SchedulingService) -> list[ScheduleView]:
    views = sched.scheduled(("scheduled", "missed"))
    if views:
        print_header("SCHEDULED (waiting for their time, not ready to publish)")
        for v in views:
            print(f"  #{v.post_id:<5} {v.status.upper():<9} {when_text(v)}  {(v.video or '-')[:30]}"
                  f"  {v.destinations} destination(s)")
    return views


def run_scheduled_actions(sched: SchedulingService, engine, on_update=None, summary=None) -> None:
    """View / reschedule / publish now / cancel one scheduled post."""
    post_id = prompt_int("Scheduled post ID")
    if post_id is None:
        return
    try:
        view = sched.view(post_id)
    except SchedulingError as e:
        print_error(friendly_error(e))
        return
    if view.status not in ("scheduled", "missed"):
        print_warning(f"Post #{post_id} is {view.status or 'not scheduled'}; only scheduled or missed posts can be "
                      "changed here.")
        return
    print_header(f"SCHEDULED POST #{post_id}")
    print(f"Status:   {view.status}\nWhen:     {when_text(view)}\nVideo:    {view.video}\n"
          f"Audience: {view.audience or 'none (Global)'}\nTargets:  {view.destinations} destination(s)\n"
          f"History:  {' -> '.join(h.get('action', '?') for h in view.history)}")
    if view.overlap_note:
        print_info(view.overlap_note)
    action = prompt_choice("Action", ["Reschedule", "Publish now", "Cancel schedule", "Back"], default=4)
    try:
        if action == 1:
            new = ask_schedule_time(
                sched, sched.audiences.for_post(post_id), current=view,
                submit_custom=lambda local, zone: sched.reschedule(post_id, local, zone),
                submit_suggestion=lambda rec, slot: sched.reschedule_from_suggestion(post_id, rec, slot))
            if new is not None:
                print_success(f"Rescheduled: {when_text(new)}")
        elif action == 2 and confirm(f"Publish post #{post_id} now? This creates real posts.", default=False):
            # Lock BEFORE releasing: if another window publishes, the post stays scheduled.
            with default_lock():
                sched.publish_now(post_id)
                result = engine.publish_post(post_id, on_update=on_update)
            if summary:
                summary(result.jobs, engine)
        elif action == 3 and confirm(f"Cancel the schedule of post #{post_id}? Nothing is deleted.", default=False):
            sched.cancel(post_id)
            print_success("Schedule cancelled. The post will not be published automatically.")
    except PublishLockBusy:
        print_error(BUSY_MESSAGE)
    except SchedulingError as e:
        print_error(friendly_error(e))
