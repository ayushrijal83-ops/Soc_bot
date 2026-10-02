# ruff: noqa: F811, DTZ001, DTZ005 - fixtures imported from other modules; naive datetimes are wall-clock input
"""V2.2 phase 6: scheduling in the TUI (Create Post, Queue, Dashboard) and the held-aware presentation layer.

Everything goes through SchedulingService; the UI never writes schedule columns (checked below).
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.core.publish_lock import BUSY_MESSAGE
from src.services.scheduling import (
    SchedulingError,
    SchedulingService,
    all_zones,
    friendly_error,
)
from tests.unit.test_publish_lock import held_by_other_process
from tests.unit.test_publishing_engine import env  # noqa: F401
from tests.unit.test_tui import services, visible  # noqa: F401

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_user_timezone(monkeypatch):
    monkeypatch.delenv("SOC_BOT_TIMEZONE", raising=False)  # default zone falls back to UTC in these tests


def tomorrow(hour=19, minute=30, days=1):
    d = datetime.now() + timedelta(days=days)
    return d.strftime("%Y-%m-%d"), f"{hour:02d}:{minute:02d}"


def next_gap(zone="America/New_York"):
    """Next local date whose 02:30 does not exist (spring forward), from the installed tzdata."""
    tz, day = ZoneInfo(zone), datetime.now().date() + timedelta(days=3)
    for _ in range(366):
        wall = datetime(day.year, day.month, day.day, 2, 30)
        if wall.replace(tzinfo=tz).astimezone(UTC).astimezone(tz).replace(tzinfo=None) != wall:
            return wall
        day += timedelta(days=1)
    pytest.skip("no DST gap within a year")


def audience_id(services, name):
    return next(a.id for a in services.audiences.list() if a.name == name)


def scheduled_post(services, days=1, audience=None):
    p = services.publishing.plan(services.video, "hi", None, [services.accounts.list("instagram")[0].id])
    p.audience_id = audience_id(services, audience) if audience else None
    date, time = tomorrow(days=days)
    return services.scheduling.schedule(p, services.scheduling.parse_local(date, time), zone="UTC",
                                        include_already_published=True)


def run(services, script, size=(120, 40)):
    from src.tui.app import SocBotApp

    notices = []

    async def main():
        app = SocBotApp(services)
        app.notify = lambda message, **kw: notices.append(message)
        async with app.run_test(size=size) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            await script(app, pilot)
            app.stop_scheduler()
            await app.workers.wait_for_complete()

    asyncio.run(main())
    return notices


async def to_review(app, pilot, services, audience=None):
    from textual.widgets import Input, Select, SelectionList

    await pilot.press("c")
    await pilot.pause()
    s = app.screen
    s.query_one("#video-path", Input).value = services.video
    s.action_next()
    await pilot.pause()
    if audience:
        s.query_one("#audience", Select).value = audience_id(services, audience)
    s.action_next()
    await pilot.pause()
    s.toggle_platform("instagram")
    await pilot.pause()
    s.query_one("#accounts", SelectionList).select_all()
    await pilot.pause()
    s.action_next()
    await pilot.pause()
    s.action_next()
    await pilot.pause()
    assert s.step == 4
    return s


async def open_schedule(app, pilot, services, audience=None):
    s = await to_review(app, pilot, services, audience)
    s.query_one("#schedule").press()
    for _ in range(100):  # composing the full timezone list takes a moment; wait until mounted and laid out
        await pilot.pause(0.02)
        modal = app.screen
        if type(modal).__name__ == "ScheduleModal" and modal.recommendation is not None                 and modal.query_one("#sched-confirm").region.height:
            break
    assert type(app.screen).__name__ == "ScheduleModal"
    return app.screen


async def custom(app, pilot, modal, date, time, zone=None):
    from textual.widgets import Input, Select

    modal.query_one("#sched-date", Input).value = date
    modal.query_one("#sched-time", Input).value = time
    if zone:
        modal.query_one("#sched-zone", Select).value = zone
    await pilot.pause()
    modal.query_one("#sched-check").press()
    await pilot.pause()


def preview(modal):
    return str(modal.query_one("#sched-preview").render())


# --- presentation: held posts are not "pending" -------------------------------------------------

def test_held_posts_are_not_pending_anywhere(services):
    ordinary = services.publishing.create_batch(
        services.publishing.plan(services.video, "now", None, [services.accounts.list("instagram")[1].id]))
    held = scheduled_post(services)
    pub = services.publishing
    assert [b.post_id for b in pub.batches()] == [ordinary]  # the ready list excludes held posts
    assert {b.post_id for b in pub.batches(include_held=True)} == {ordinary, held.post_id}
    view = pub.batch(held.post_id)
    assert view.status == "scheduled" and {j.status for j in view.jobs} == {"scheduled"}
    assert view.schedule_status == "scheduled" and view.scheduled_at == held.scheduled_at
    counts = pub.queue_counts()
    assert counts["pending"] == 1 and counts["scheduled"] == 1 and counts["missed"] == 0
    statuses = {r["post_id"]: r["status"] for r in pub.history()}
    assert statuses == {ordinary: "pending", held.post_id: "scheduled"}
    assert [r["post_id"] for r in pub.history(status="pending")] == [ordinary]
    assert {a.status for a in pub.recent_activity()} == {"pending", "scheduled"}
    services.scheduling.cancel(held.post_id)
    assert pub.batch(held.post_id).status == "cancelled" and pub.queue_counts()["cancelled"] == 1


def test_service_helpers(services):
    sched = services.scheduling
    assert sched.parse_local("2026-10-15", " 19:30 ") == datetime(2026, 10, 15, 19, 30)
    for bad in (("15.10.2026", "19:30"), ("2026-10-15", "7pm"), ("", "")):
        with pytest.raises(SchedulingError, match="YYYY-MM-DD") as e:
            sched.parse_local(*bad)
        assert e.value.code == "invalid_input"
    assert sched.next_scheduled() is None
    later, sooner = scheduled_post(services, days=3), scheduled_post(services, days=1)
    assert sched.next_scheduled().post_id == sooner.post_id and later.post_id != sooner.post_id
    assert "Europe/London" in all_zones() and "Asia/Kathmandu" in all_zones() and len(all_zones()) > 400
    assert all_zones() is all_zones()  # computed once
    for code in ("invalid_input", "invalid_zone", "dst_gap", "too_soon", "too_far", "stale_suggestion",
                 "not_found", "not_scheduled", "invalid_state", "conflict"):
        text = friendly_error(SchedulingError(code, f"service text for {code}"))
        assert text and "Traceback" not in text and "SchedulingError" not in text
    assert "changed before your action completed" in friendly_error(SchedulingError("conflict", "x"))


def test_reschedule_from_suggestion_uses_start_utc(services):
    held = scheduled_post(services, audience="Japan")
    sched = services.scheduling
    rec = services.publishing.timing.recommend(services.audiences.for_post(held.post_id), datetime.now(UTC))
    moved = sched.reschedule_from_suggestion(held.post_id, rec, rec.slots[-1])
    assert moved.scheduled_at == rec.slots[-1].start_utc and moved.source == "suggestion"
    assert [h["action"] for h in moved.history] == ["scheduled", "rescheduled"]


# --- TUI: Create Post -> Schedule ---------------------------------------------------------------

def test_publish_now_path_is_unchanged(services):
    from tests.unit.test_tui import to_review as original

    async def script(app, pilot):
        s = await original(app, pilot, services)
        assert str(s.query_one("#publish").label) == "▶ PUBLISH NOW" and s.query_one("#schedule").display

    run(services, script)


def test_schedule_custom_time_with_zone_and_confirmation(services, monkeypatch):
    calls = []
    real = services.scheduling.schedule
    monkeypatch.setattr(services.scheduling, "schedule", lambda *a, **k: calls.append(1) or real(*a, **k))
    date, time = tomorrow()

    async def script(app, pilot):
        modal = await open_schedule(app, pilot, services)
        assert modal.query_one("#sched-confirm").disabled  # nothing to confirm yet
        assert len(modal.query_one("#sched-zone")._options) == len(all_zones())  # full tz database
        await custom(app, pilot, modal, date, time, zone="Asia/Kathmandu")
        text = preview(modal)
        assert "Schedule this post?" in text and "Asia/Kathmandu" in text and "UTC" in text
        assert not modal.query_one("#sched-confirm").disabled
        modal.query_one("#sched-confirm").press()
        await pilot.pause()
        assert app.current_mode == "queue"

    notices = run(services, script)
    (view,) = services.scheduling.scheduled()
    assert calls == [1] and view.zone == "Asia/Kathmandu" and view.zone_source == "explicit"
    assert view.local.strftime("%Y-%m-%d %H:%M") == f"{date} {time}" and view.destinations == 4
    assert services.fake.calls == [] and services.publishing.batches() == []  # nothing published
    assert any(f"Post #{view.post_id} scheduled" in n for n in notices)


def test_schedule_from_suggestion(services, monkeypatch):
    seen = []
    real = services.scheduling.schedule_from_suggestion

    def spy(plan, rec, slot, **kw):
        seen.append(slot)
        return real(plan, rec, slot, **kw)

    monkeypatch.setattr(services.scheduling, "schedule_from_suggestion", spy)

    async def script(app, pilot):
        from textual.widgets import OptionList

        modal = await open_schedule(app, pilot, services, audience="Japan")
        options = modal.query_one("#sched-suggestions", OptionList)
        assert options.option_count == len(modal.recommendation.slots) >= 1
        assert "Asia/Tokyo" in str(options.get_option_at_index(0).prompt)
        options.highlighted = 0
        options.action_select()
        await pilot.pause()
        assert "From the suggested times." in preview(modal)
        modal.query_one("#sched-confirm").press()
        await pilot.pause()

    run(services, script)
    (view,) = services.scheduling.scheduled()
    assert seen and view.scheduled_at == seen[0].start_utc and view.source == "suggestion"  # exact, not rebuilt


@pytest.mark.parametrize("case", ["dst_gap", "too_soon", "too_far", "bad_format"])
def test_errors_stay_inline_and_form_stays_open(services, case):
    if case == "dst_gap":
        gap = next_gap()
        args = (gap.strftime("%Y-%m-%d"), "02:30", "America/New_York")
    elif case == "too_soon":
        now = datetime.now(UTC)  # the form's default zone here is UTC (Global audience, no SOC_BOT_TIMEZONE)
        args = (now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), None)
    elif case == "too_far":
        args = (*tomorrow(days=400), None)
    else:
        args = ("tomorrow", "7pm", None)
    expected = {"dst_gap": "does not exist", "too_soon": "at least 2 minutes", "too_far": "within 365 days",
                "bad_format": "YYYY-MM-DD"}[case]

    async def script(app, pilot):
        modal = await open_schedule(app, pilot, services)
        await custom(app, pilot, modal, *args)
        assert expected in preview(modal) and modal.query_one("#sched-confirm").disabled
        assert type(app.screen).__name__ == "ScheduleModal"
        date, time = tomorrow()
        await custom(app, pilot, modal, date, time)  # fix it: the same form works
        assert "Schedule this post?" in preview(modal)

    run(services, script)
    assert services.scheduling.scheduled() == []


def test_stale_suggestion_refreshes_and_stays_open(services, monkeypatch):
    def stale(*a, **k):
        raise SchedulingError("stale_suggestion", "too close")

    monkeypatch.setattr(services.scheduling, "schedule_from_suggestion", stale)

    async def script(app, pilot):
        from textual.widgets import OptionList

        modal = await open_schedule(app, pilot, services, audience="Japan")
        first = modal.recommendation
        options = modal.query_one("#sched-suggestions", OptionList)
        options.highlighted = 0
        options.action_select()
        await pilot.pause()
        modal.query_one("#sched-confirm").press()
        await pilot.pause()
        assert "suggestions were refreshed" in preview(modal) and modal.recommendation is not first
        assert type(app.screen).__name__ == "ScheduleModal" and modal.query_one("#sched-confirm").disabled

    run(services, script)


# --- TUI: Queue scheduled section + actions -----------------------------------------------------

async def to_queue(app, pilot):
    await pilot.press("q")
    await pilot.pause()
    return app.screen


def test_queue_shows_scheduled_section_not_as_pending(services):
    view = scheduled_post(services, audience="Japan")

    async def script(app, pilot):
        from textual.widgets import DataTable

        q = await to_queue(app, pilot)
        table = q.query_one("#scheduled", DataTable)
        assert table.row_count == 1 and table.display
        row = " ".join(str(c) for c in table.get_row_at(0))
        assert f"#{view.post_id}" in row and "UTC" in row and "Scheduled" in row and "in " in row
        assert not q.query("BatchCard")  # not in the ready/batch list
        summary = str(q.query_one("#queue-summary").render())
        assert "0 open job(s)" in summary and "1 scheduled" in summary
        assert not q.query_one("#resume").display

    run(services, script)


def act(q, view, action):
    q.scheduled_action(view, action)


def test_cancel_from_queue(services):
    view = scheduled_post(services)

    async def script(app, pilot):
        q = await to_queue(app, pilot)
        act(q, view, "cancel")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert q.query_one("#scheduled").row_count == 0

    notices = run(services, script)
    assert services.scheduling.view(view.post_id).status == "cancelled"
    assert any("Schedule cancelled" in n for n in notices)
    assert services.publishing.batch(view.post_id).jobs  # nothing deleted


def test_reschedule_from_queue_keeps_jobs(services):
    view = scheduled_post(services)
    jobs = [j.id for j in services.engine.store.jobs_for_post(view.post_id)]
    date, time = tomorrow(hour=8, minute=15, days=2)

    async def script(app, pilot):
        q = await to_queue(app, pilot)
        act(q, view, "reschedule")
        await pilot.pause()
        modal = app.screen
        assert type(modal).__name__ == "ScheduleModal" and "Currently" in str(modal.query_one("#sched-info").render())
        await custom(app, pilot, modal, date, time)
        modal.query_one("#sched-confirm").press()
        await pilot.pause()

    run(services, script)
    moved = services.scheduling.view(view.post_id)
    assert moved.local.strftime("%Y-%m-%d %H:%M") == f"{date} {time}"
    assert [j.id for j in services.engine.store.jobs_for_post(view.post_id)] == jobs
    assert [h["action"] for h in moved.history] == ["scheduled", "rescheduled"]


def test_publish_now_from_queue(services):
    view = scheduled_post(services)

    async def script(app, pilot):
        q = await to_queue(app, pilot)
        act(q, view, "publish_now")
        await pilot.pause()
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()

    run(services, script)
    assert services.scheduling.view(view.post_id).status == "released"
    assert services.publishing.batch(view.post_id).status == "completed"


def test_publish_now_lock_busy_leaves_post_scheduled(services):
    view = scheduled_post(services)

    async def script(app, pilot):
        q = await to_queue(app, pilot)
        with held_by_other_process(services.publishing.lock.path):
            act(q, view, "publish_now")
            await pilot.pause()
            await pilot.press("y")
            await app.workers.wait_for_complete()
            await pilot.pause()

    notices = run(services, script)
    assert BUSY_MESSAGE in notices and not any("PublishLockBusy" in n for n in notices)
    assert services.scheduling.view(view.post_id).status == "scheduled"  # never released without publishing
    assert services.fake.calls == []


def test_conflict_is_friendly_and_refreshes(services, monkeypatch):
    view = scheduled_post(services)

    def conflict(post_id):
        raise SchedulingError("conflict", "x")

    async def script(app, pilot):
        q = await to_queue(app, pilot)
        monkeypatch.setattr(services.scheduling, "cancel", conflict)
        refreshed = []
        real = q.refresh_data
        q.refresh_data = lambda: refreshed.append(1) or real()
        act(q, view, "cancel")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert refreshed

    notices = run(services, script)
    assert any("changed before your action completed" in n for n in notices)
    assert services.scheduling.view(view.post_id).status == "scheduled"


def test_actions_modal_shows_details(services):
    view = scheduled_post(services, audience="Japan")

    async def script(app, pilot):
        from src.tui.screens.schedule import ScheduledActionsModal

        app.push_screen(ScheduledActionsModal(services.scheduling.view(view.post_id)))
        await pilot.pause()
        text = str(app.screen.query_one("#sa-details").render())
        assert "UTC" in text and "Japan" in text and "scheduled" in text and "destination" in text
        await pilot.press("escape")

    run(services, script)


# --- Dashboard ------------------------------------------------------------------------------------

def test_dashboard_next_scheduled(services):
    async def script(app, pilot):
        info = str(app.screen.query_one("#publishing-title").render())
        assert "Next scheduled: none" in info
        view = scheduled_post(services)
        app.screen.refresh_data()
        await pilot.pause()
        info = str(app.screen.query_one("#publishing-title").render())
        assert f"Next scheduled: #{view.post_id}" in info and "UTC" in info

    run(services, script)


# --- 80 x 24 ------------------------------------------------------------------------------------

@pytest.mark.parametrize("size", [(80, 24), (100, 30)])
def test_small_terminal_layouts(services, size):
    view = scheduled_post(services, audience="Japan")

    async def script(app, pilot):
        from src.tui.screens.schedule import ScheduledActionsModal

        title = app.screen.query_one("#publishing-title")
        assert "Next scheduled" in str(title.render()) and visible(app, title, size)
        q = await to_queue(app, pilot)
        assert visible(app, q.query_one("#scheduled"), size)
        app.push_screen(ScheduledActionsModal(services.scheduling.view(view.post_id)))
        await pilot.pause()
        for button in ("#reschedule", "#publish_now", "#cancel", "#back"):
            assert visible(app, app.screen.query_one(button), size), button
        await pilot.press("escape")
        await pilot.pause()
        modal = await open_schedule(app, pilot, services, audience="Japan")
        for button in ("#sched-confirm", "#sched-back"):
            assert visible(app, modal.query_one(button), size), button
        await custom(app, pilot, modal, *tomorrow())
        await pilot.pause(0.1)
        assert visible(app, modal.query_one("#sched-confirm"), size) and not modal.query_one("#sched-confirm").disabled
        preview, body = modal.query_one("#sched-preview"), modal.query_one("#sched-body")
        assert body.region.y <= preview.region.y < body.region.bottom  # the confirmation is scrolled into view

    run(services, script, size)


# --- architecture: the UI never writes schedule state itself ------------------------------------

def test_ui_and_cli_never_write_schedule_columns():
    pattern = re.compile(r"(schedule_status|scheduled_at|schedule_json)\s*=(?!=)|update\(Post|import Post\b")
    offenders = []
    for folder in ("src/tui", "src/cli"):
        for path in (ROOT / folder).rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == []
    assert SchedulingService  # the only writer of schedule state (plus DueScheduler for releases / missed)
