# ruff: noqa: F811 - pytest fixtures imported from other test modules
"""V2.2 phase 5: the callers of DueScheduler.run_due — TUI timer/worker and ``main.py --run-due``."""

import asyncio
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from main import RUN_DUE_BUSY_MESSAGE, report_due_run
from src.core.jobs import JobStore
from src.services.due_scheduler import DueEvent, DueRunResult, scheduler_poll_seconds
from src.storage.database import Database, Post
from tests.unit.test_publish_lock import held_by_other_process
from tests.unit.test_publishing_engine import env  # noqa: F401
from tests.unit.test_tui import services  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


# --- configuration ----------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [(None, 30), ("", 30), ("10", 10), ("30", 30), ("300", 300), ("9", 30),
                                           ("301", 30), ("0", 30), ("-1", 30), ("5", 30), ("abc", 30),
                                           ("12.5", 30), (" 45 ", 45)])
def test_poll_seconds(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("SOC_BOT_SCHEDULER_POLL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("SOC_BOT_SCHEDULER_POLL_SECONDS", raw)
    assert scheduler_poll_seconds() == expected


# --- TUI ----------------------------------------------------------------------------------------

class FakeScheduler:
    """Stands in for DueScheduler in the TUI: scripted results, optional blocking, call counting."""

    def __init__(self, results=None, work=True, gate=None, publish=False):
        self.results = list(results or [])
        self.work, self.gate, self.publish = work, gate, publish
        self.calls = 0
        self.started = threading.Event()

    def has_work(self):
        return self.work

    def run_due(self, on_event=None):
        self.calls += 1
        if self.publish and on_event:
            on_event(DueEvent("publishing_started", 7, NOW))
        self.started.set()
        if self.gate is not None:
            self.gate.wait(10)
        result = self.results.pop(0) if self.results else DueRunResult(started_at=NOW)
        if isinstance(result, Exception):
            raise result
        if on_event:
            for event in result.events:
                on_event(event)
        return result


def result(*events, busy=False):
    r = DueRunResult(started_at=NOW, busy=busy, events=list(events))
    r.errors = [e for e in events if e.kind == "error"]
    return r


def run_tui(services, fake, script, size=(120, 40)):
    from src.tui.app import SocBotApp

    services.due_scheduler = fake
    notices = []

    async def main():
        app = SocBotApp(services)
        app.notify = lambda message, **kw: notices.append((message, kw.get("severity", "information")))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await script(app, pilot)
            app.stop_scheduler()
            if getattr(fake, "gate", None) is not None:
                fake.gate.set()
            await app.workers.wait_for_complete()

    asyncio.run(main())
    return notices


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_initial_pass_on_mount_and_interval(services, monkeypatch):
    monkeypatch.setenv("SOC_BOT_SCHEDULER_POLL_SECONDS", "10")
    fake = FakeScheduler()

    async def script(app, pilot):
        await settle(app, pilot)
        assert fake.calls == 1  # right away, not after the first interval
        assert app.scheduler_interval == 10 and app._scheduler_timer is not None

    run_tui(services, fake, script)


def test_default_interval_is_30(services, monkeypatch):
    monkeypatch.delenv("SOC_BOT_SCHEDULER_POLL_SECONDS", raising=False)

    async def script(app, pilot):
        assert app.scheduler_interval == 30

    run_tui(services, FakeScheduler(), script)


def test_worker_group_no_overlap_and_checking_is_not_publishing(services):
    gate = threading.Event()
    fake = FakeScheduler(gate=gate)

    async def script(app, pilot):
        await asyncio.to_thread(fake.started.wait, 10)
        workers = [w for w in app.workers if w.group == "scheduler"]
        assert len(workers) == 1 and workers[0].name == "scheduler"
        assert app.publishing_active is False  # only checking: quitting is not blocked
        app.run_scheduler()
        app.run_scheduler()  # ticks while a pass runs are skipped, never queued
        assert fake.calls == 1 and len([w for w in app.workers if w.group == "scheduler"]) == 1
        gate.set()
        await settle(app, pilot)
        app.run_scheduler()
        await settle(app, pilot)
        assert fake.calls == 2

    run_tui(services, fake, script)


def test_scheduler_publishing_sets_publishing_active_and_blocks_quit(services):
    gate = threading.Event()
    fake = FakeScheduler(gate=gate, publish=True)

    async def script(app, pilot):
        await asyncio.to_thread(fake.started.wait, 10)
        await pilot.pause()
        assert app.publishing_active is True and app._manual_publishing is False
        app.request_exit()
        await pilot.pause()
        assert type(app.screen).__name__ != "ConfirmModal"  # refused: a post is publishing
        app.publishing_active = False  # a manual screen finishing must not clear the scheduler's flag
        assert app.publishing_active is True
        gate.set()
        await settle(app, pilot)
        assert app.publishing_active is False

    notices = run_tui(services, fake, script)
    assert any("is publishing" in m for m, _ in notices)


def test_nothing_due_means_no_run_and_no_lock(services):
    fake = FakeScheduler(work=False)

    async def script(app, pilot):
        await settle(app, pilot)
        assert fake.calls == 0

    run_tui(services, fake, script)


def test_stop_scheduler_cancels_timer_and_new_passes(services):
    fake = FakeScheduler()

    async def script(app, pilot):
        await settle(app, pilot)
        app.stop_scheduler()
        app.run_scheduler()
        await settle(app, pilot)
        assert fake.calls == 1 and app._scheduler_stopped
        assert not [w for w in app.workers if w.group == "scheduler" and not w.is_finished]

    run_tui(services, fake, script)


def test_exception_does_not_stop_future_ticks(services):
    fake = FakeScheduler(results=[RuntimeError("db gone"), result()])

    async def script(app, pilot):
        await settle(app, pilot)
        assert app._scheduler_running is False
        app.run_scheduler()
        await settle(app, pilot)
        assert fake.calls == 2

    notices = run_tui(services, fake, script)
    assert ("Scheduler check failed: RuntimeError", "error") in notices


def test_busy_notice_is_friendly_and_shown_once(services):
    fake = FakeScheduler(results=[result(busy=True), result(busy=True), result(), result(busy=True)])

    async def script(app, pilot):
        for _ in range(3):
            await settle(app, pilot)
            app.run_scheduler()
        await settle(app, pilot)

    notices = run_tui(services, fake, script)
    busy = [m for m, _ in notices if "Another Soc_bot window is publishing" in m]
    assert len(busy) == 2  # once, then again only after a non-busy run in between
    assert all("PublishLockBusy" not in m and "Publishing stopped" not in m for m, _ in notices)


def test_missed_published_failed_and_error_notices(services):
    fake = FakeScheduler(results=[result(
        DueEvent("missed", 11, NOW), DueEvent("released", 12, NOW), DueEvent("published", 12, NOW, "completed"),
        DueEvent("failed", 13, NOW, "completed_with_failures"),
        DueEvent("error", 14, NOW, message="Publishing stopped: boom access_token=SECRET123"),
        DueEvent("error", 14, NOW, message="Publishing stopped: boom access_token=SECRET123"))])

    async def script(app, pilot):
        await settle(app, pilot)

    notices = run_tui(services, fake, script)
    text = "\n".join(m for m, _ in notices)
    assert "Scheduled post #11 (2026-10-02 12:00 UTC) missed its window" in text
    assert "Scheduled post #12 published" in text and "#13 finished with problems (completed_with_failures)" in text
    assert "SECRET123" not in text and text.count("#14") == 1  # redacted, and repeated errors shown once
    assert "released" not in text  # internal events are not shown


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
def test_notices_keep_the_ui_usable(services, size):
    fake = FakeScheduler(results=[result(DueEvent("missed", 11, NOW), DueEvent("published", 12, NOW, "completed"))])

    async def script(app, pilot):
        await settle(app, pilot)
        for key, mode in (("c", "create"), ("q", "queue"), ("d", "dashboard")):
            await pilot.press(key)
            await pilot.pause()
            assert app.current_mode == mode
        assert app.has_class("narrow") == (size[0] < 100)

    run_tui(services, fake, script, size)


def test_real_scheduler_in_tui_publishes_due_post(services):
    """End to end in the TUI with the real DueScheduler: a due post is published by the timer pass."""
    from src.services.due_scheduler import DueScheduler
    from src.services.scheduling import SchedulingService

    sched = SchedulingService(services.publishing, clock=lambda: NOW - timedelta(hours=1))
    p = services.publishing.plan(services.video, "hi", None, [services.accounts.list("instagram")[0].id])
    post_id = sched.schedule(p, (NOW - timedelta(minutes=5)).replace(tzinfo=None), zone="UTC").post_id
    real = DueScheduler(services.publishing, clock=lambda: NOW)

    async def script(app, pilot):
        await settle(app, pilot)

    notices = run_tui(services, real, script)
    assert any(f"Scheduled post #{post_id} published" in m for m, _ in notices)
    assert services.publishing.batch(post_id).status == "completed"


# --- CLI: report_due_run (in process) --------------------------------------------------------

class Raising:
    def run_due(self):
        raise RuntimeError("database is locked token=SECRET999")


def test_cli_exit_codes_in_process(capsys):
    assert report_due_run(Raising()) == 1
    out = capsys.readouterr().out
    assert "could not run" in out and "SECRET999" not in out
    assert report_due_run(FakeScheduler(results=[result(busy=True)])) == 3
    assert RUN_DUE_BUSY_MESSAGE in capsys.readouterr().out
    failed = result(DueEvent("failed", 5, NOW, "failed"), DueEvent("missed", 6, NOW))
    failed.failed, failed.missed = [5], [6]
    assert report_due_run(FakeScheduler(results=[failed])) == 0  # a failed post is not a scheduler error
    out = capsys.readouterr().out
    assert "Failed:    1" in out and "Missed:    1" in out and "post #6" in out and "post #5" in out


def test_parse_args_run_due(monkeypatch):
    from main import parse_args

    monkeypatch.setattr(sys, "argv", ["main.py"])
    assert parse_args().run_due is False
    monkeypatch.setattr(sys, "argv", ["main.py", "--run-due"])
    assert parse_args().run_due is True


def test_main_run_due_infrastructure_error_exits_1(monkeypatch, tmp_path, capsys):
    import main

    monkeypatch.setattr(sys, "argv", ["main.py", "--run-due"])
    monkeypatch.setattr(main, "ENV_FILE", tmp_path / "none.env")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    assert main.main() == 1


# --- CLI: real subprocess ``python main.py --run-due`` ------------------------------------------

WRAPPER = """
import sys
from pathlib import Path
root, lock_path, env_file = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
from src.core import publish_lock
publish_lock.LOCK_PATH = Path(lock_path)          # isolated lock (never the project's data/publishing.lock)
import src.core.publisher as publisher
from tests.unit.test_publishing_engine import FakePublisher, ok
publisher.default_publishers = lambda: {"instagram": FakePublisher("instagram", ok("IG1"), ok("IG2"))}
_init = publisher.PublisherEngine.__init__
def _no_probe(self, *a, **k):
    k["probe_media"] = False
    _init(self, *a, **k)
publisher.PublisherEngine.__init__ = _no_probe
import main
main.ENV_FILE = Path(env_file)                     # never the developer's .env
sys.argv = ["main.py", "--run-due"]
sys.exit(main.main())
"""


@pytest.fixture
def cli_env(tmp_path):
    from src.accounts.manager import AccountManager
    from src.storage.tokens import TokenEncryption, generate_key

    key = generate_key()
    db_url = f"sqlite:///{tmp_path / 'cli.db'}"
    db = Database(db_url, encryption=TokenEncryption(key.encode()))
    db.create_all()
    db.migrate(verbose=False)
    accounts = AccountManager(db, TokenEncryption(key.encode()))
    account = accounts.create_account(platform="instagram", platform_account_id="1784", username="ig_user",
                                      access_token="TOKEN", expires_at=datetime.now(UTC) + timedelta(days=30))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v" * 2000)
    store, now = JobStore(db), datetime.now(UTC)

    def post(offset):
        return store.create_post(str(video), "x", [(account.id, {})], scheduled_at=now + offset,
                                 schedule_status="scheduled", schedule_json={"history": []})

    posts = {"due": post(timedelta(minutes=-1)), "future": post(timedelta(days=1)), "overdue": post(timedelta(hours=-3))}
    environ = {k: v for k, v in os.environ.items() if not k.startswith(("SOC_BOT_", "DATABASE_URL", "CONTENT_ROOT"))}
    environ.update(DATABASE_URL=db_url, ENCRYPTION_KEY=key, CONTENT_ROOT=str(tmp_path / "content"))
    wrapper = tmp_path / "run_due_wrapper.py"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    yield db, posts, environ, wrapper, tmp_path / "publishing.lock", tmp_path
    db.engine.dispose()


def run_cli(cli_env):
    _db, _posts, environ, wrapper, lock_path, tmp = cli_env
    return subprocess.run([sys.executable, str(wrapper), str(ROOT), str(lock_path), str(tmp / "none.env")],
                          cwd=tmp, env=environ, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=120, check=False)


def statuses(db, posts):
    with db.session() as session:
        return {name: session.get(Post, pid).schedule_status for name, pid in posts.items()}


def test_cli_run_due_subprocess_publishes_and_exits_0(cli_env):
    db, posts = cli_env[0], cli_env[1]
    out = run_cli(cli_env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Soc_bot scheduler" in out.stdout and "Published: 1" in out.stdout and "Missed:    1" in out.stdout
    assert statuses(db, posts) == {"due": "released", "future": "scheduled", "overdue": "missed"}
    assert {j.status for j in JobStore(db).jobs_for_post(posts["due"])} == {"published"}
    assert {j.status for j in JobStore(db).jobs_for_post(posts["overdue"])} == {"pending"}


def test_cli_run_due_subprocess_busy_exits_3_and_changes_nothing(cli_env):
    db, posts, lock_path = cli_env[0], cli_env[1], cli_env[4]
    with held_by_other_process(lock_path):
        out = run_cli(cli_env)
    assert out.returncode == 3, out.stdout + out.stderr
    assert RUN_DUE_BUSY_MESSAGE in out.stdout
    assert statuses(db, posts) == {"due": "scheduled", "future": "scheduled", "overdue": "scheduled"}  # NOT missed
    assert {j.status for p in posts.values() for j in JobStore(db).jobs_for_post(p)} == {"pending"}
