# ruff: noqa: F811 - pytest fixtures imported from other test modules
"""V2.2 phase 4: process-level publishing lock (real OS locks, real child processes)."""

import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import update

from src.core import publish_lock
from src.core.publish_lock import (
    BUSY_MESSAGE,
    PROJECT_ROOT,
    PublishLock,
    PublishLockBusy,
    default_lock,
)
from src.services.due_scheduler import DueScheduler
from src.services.scheduling import SchedulingService
from src.storage.database import Post, PublishJob
from tests.unit.test_cli_publish import setup  # noqa: F401
from tests.unit.test_content import env as content_env  # noqa: F401
from tests.unit.test_publishing_engine import env  # noqa: F401
from tests.unit.test_tui import services  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
from src.core.publish_lock import PublishLock, PublishLockBusy
lock = PublishLock(sys.argv[2])
try:
    lock.acquire()
except PublishLockBusy:
    print("busy", flush=True)
    sys.exit(0)
print("held" if sys.argv[3] == "hold" else "acquired", flush=True)
if sys.argv[3] == "hold":
    sys.stdin.read()          # keep holding until the parent closes stdin or kills us
"""


def child_try(path) -> str:
    """A separate process tries to take the lock once (never waits)."""
    out = subprocess.run([sys.executable, "-c", CHILD, str(ROOT), str(path), "try"], capture_output=True,
                         text=True, timeout=60, check=True)
    return out.stdout.strip()


@contextmanager
def held_by_other_process(path):
    proc = subprocess.Popen([sys.executable, "-c", CHILD, str(ROOT), str(path), "hold"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        yield proc
    finally:
        proc.kill()
        proc.wait(timeout=30)


@pytest.fixture
def lock(tmp_path):
    return PublishLock(tmp_path / "publishing.lock")


# --- unit behaviour ---------------------------------------------------------------------------

def test_acquire_release_and_handle_cleanup(lock):
    lock.acquire()
    assert lock.held and lock._state.depth == 1 and lock._state.fd is not None
    lock.release()
    assert not lock.held and lock._state.fd is None and lock._state.owner is None
    assert lock.path.exists()  # the file may stay; it means nothing on its own
    with lock:
        assert lock.held


def test_reentrant_depth_and_nested_contexts(lock):
    with lock:
        with lock:
            lock.acquire()
            assert lock._state.depth == 3
            lock.release()
        assert lock.held and lock._state.depth == 1
        assert child_try(lock.path) == "busy"  # inner releases kept the OS lock
    assert not lock.held and child_try(lock.path) == "acquired"  # final release freed it


@pytest.mark.parametrize("error", [ValueError, KeyboardInterrupt, SystemExit])
def test_exceptions_release_and_propagate(lock, error):
    with pytest.raises(error), lock, lock:
        raise error()
    assert not lock.held and lock._state.depth == 0 and child_try(lock.path) == "acquired"


def test_double_release_is_a_controlled_error(lock):
    with pytest.raises(RuntimeError):
        lock.release()
    lock.acquire()
    lock.release()
    with pytest.raises(RuntimeError):
        lock.release()


def test_other_thread_of_same_process_is_busy_and_cannot_release(lock):
    lock.acquire()
    outcome = {}

    def other():
        try:
            lock.acquire()
            outcome["acquire"] = "acquired"
        except PublishLockBusy:
            outcome["acquire"] = "busy"
        try:
            lock.release()
        except RuntimeError:
            outcome["release"] = "refused"

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert outcome == {"acquire": "busy", "release": "refused"} and lock.held
    lock.release()
    t2 = threading.Thread(target=lambda: (lock.acquire(), lock.release()))
    t2.start()
    t2.join()  # once free, any thread can take it


def test_same_path_shares_state_across_instances(tmp_path):
    a, b = PublishLock(tmp_path / "x.lock"), PublishLock(tmp_path / "x.lock")
    with a, b:  # nested through two handles: one OS lock, depth 2
        assert a._state is b._state and a._state.depth == 2


def test_path_is_anchored_to_the_project_not_the_cwd(tmp_path):
    expected = ROOT / "data" / "publishing.lock"
    assert PROJECT_ROOT == ROOT
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from src.core.publish_lock import default_lock;"
            " print(default_lock().path)")
    out = subprocess.run([sys.executable, "-c", code, str(ROOT)], cwd=tmp_path, capture_output=True, text=True,
                         timeout=60, check=True)
    assert Path(out.stdout.strip()) == expected.resolve()
    assert not (tmp_path / "data").exists()  # nothing created in the working directory


def test_tests_use_a_private_lock(tmp_path):
    assert default_lock().path == (tmp_path / "publishing.lock").resolve() != (ROOT / "data" / "publishing.lock")


# --- real cross-process behaviour --------------------------------------------------------------

def test_parent_holds_child_busy_then_free(lock):
    with lock:
        assert child_try(lock.path) == "busy"
    assert child_try(lock.path) == "acquired"


def test_child_holds_parent_busy_immediately(lock):
    with held_by_other_process(lock.path):
        started = time.monotonic()
        with pytest.raises(PublishLockBusy):
            lock.acquire()
        assert time.monotonic() - started < 5.0  # non-blocking (a waiting lock, e.g. msvcrt LK_LOCK, takes ~10 s)
        assert not lock.held and lock._state.fd is None  # failed attempt leaves nothing open


def test_dead_process_releases_its_lock(lock):
    with held_by_other_process(lock.path) as proc:
        with pytest.raises(PublishLockBusy):
            lock.acquire()
        proc.kill()           # dies WITHOUT releasing
        proc.wait(timeout=30)
        lock.acquire()        # the OS dropped the lock with the process
        assert lock.held
        lock.release()


# --- PublishingService ------------------------------------------------------------------------

def plan(services, n=1):
    return services.publishing.plan(services.video, "hi", None, [a.id for a in services.accounts.list("instagram")][:n])


def job_rows(services):
    with services.engine.store.database.session() as session:
        return sorted((j.id, j.status) for j in session.query(PublishJob))


def test_publish_batch_busy_changes_nothing(services):
    post_id = services.publishing.create_batch(plan(services, 2))
    before = job_rows(services)
    with held_by_other_process(services.publishing.lock.path), pytest.raises(PublishLockBusy):
        services.publishing.publish_batch(post_id)
    assert services.fake.calls == [] and job_rows(services) == before
    assert services.engine.store.attempts(before[0][0]) == []


def test_retry_job_busy_changes_nothing(services):
    post_id = services.publishing.create_batch(plan(services))
    job_id = services.engine.store.jobs_for_post(post_id)[0].id
    with services.engine.store.database.session() as session:
        session.execute(update(PublishJob).where(PublishJob.id == job_id).values(status="failed"))
        session.commit()
    with held_by_other_process(services.publishing.lock.path), pytest.raises(PublishLockBusy):
        services.publishing.retry_job(job_id)
    assert services.engine.store.get_job(job_id).status == "failed" and services.fake.calls == []


def test_resume_open_busy_changes_nothing(services):
    services.publishing.create_batch(plan(services, 2))
    before = job_rows(services)
    with held_by_other_process(services.publishing.lock.path), pytest.raises(PublishLockBusy):
        services.publishing.resume_open()
    assert services.fake.calls == [] and job_rows(services) == before


def test_normal_publishing_takes_and_frees_the_lock(services):
    post_id = services.publishing.create_batch(plan(services))
    seen = []
    real = services.engine.publish_post

    def spy(pid, **kwargs):
        seen.append((services.publishing.lock.held, child_try(services.publishing.lock.path)))
        return real(pid, **kwargs)

    services.engine.publish_post = spy
    assert services.publishing.publish_batch(post_id).status == "completed"
    assert seen == [(True, "busy")]  # held (and visible to other processes) while the engine runs
    assert not services.publishing.lock.held and child_try(services.publishing.lock.path) == "acquired"


# --- DueScheduler -----------------------------------------------------------------------------

T0, S = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc), datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def scheduled_post(services, at):
    sched = SchedulingService(services.publishing, clock=lambda: T0)
    return sched.schedule(plan(services), at.replace(tzinfo=None), zone="UTC", include_already_published=True).post_id


def schedule_status(services, post_id):
    with services.engine.store.database.session() as session:
        return session.get(Post, post_id).schedule_status


def test_scheduler_holds_lock_for_whole_pass_and_nests_publish_batch(services):
    post_id = scheduled_post(services, S)
    lock = services.publishing.lock
    seen = []
    real = services.publishing._publish_batch

    def spy(pid, *args):
        seen.append((lock._state.depth, child_try(lock.path)))
        return real(pid, *args)

    services.publishing._publish_batch = spy
    result = DueScheduler(services.publishing).run_due(S)
    assert result.published == [post_id] and not result.busy
    assert seen == [(2, "busy")]  # scheduler depth 1 + publish_batch depth 2, one OS lock
    assert not lock.held and child_try(lock.path) == "acquired"


def test_busy_scheduler_returns_at_once_and_touches_nothing(services):
    due = scheduled_post(services, S)
    overdue = scheduled_post(services, S - timedelta(hours=3))
    events = []
    with held_by_other_process(services.publishing.lock.path):
        started = time.monotonic()
        result = DueScheduler(services.publishing).run_due(S, on_event=events.append)
        assert time.monotonic() - started < 5.0  # returns at once (generous bound for slow machines)
    assert result.busy and result.released == result.missed == result.published == []
    assert [e.kind for e in events] == ["busy"] and events[0].message == BUSY_MESSAGE
    assert schedule_status(services, due) == schedule_status(services, overdue) == "scheduled"  # NOT missed
    assert services.fake.calls == []
    after = DueScheduler(services.publishing).run_due(S)  # next run, lock free: normal behaviour
    assert after.missed == [overdue] and after.published == [due]


def test_scheduler_lock_is_the_publishing_services_lock(services):
    assert DueScheduler(services.publishing).lock is services.publishing.lock
    assert services.due_scheduler.lock.path == services.publishing.lock.path


# --- CLI and Content Inbox --------------------------------------------------------------------

def test_cli_create_post_busy_creates_nothing(setup, capsys):
    from src.core.jobs import JobStore
    from tests.unit.test_cli_publish import run
    from tests.unit.test_publishing_engine import FakePublisher

    fake = FakePublisher("tiktok")
    with held_by_other_process(publish_lock.LOCK_PATH):
        # video, no cover, caption, audience (Global), account 1, continue, privacy SELF_ONLY, publish 1 = Now
        run(setup, [setup[2], "", "Hello", "", "1", "", "4", "1"], fake)
    assert BUSY_MESSAGE in capsys.readouterr().out
    assert fake.calls == [] and JobStore(setup[0]).recent_jobs() == []


def test_cli_queue_busy_publishes_nothing(setup, capsys):
    from unittest.mock import patch

    from src.cli.publish_menu import run_publishing_queue
    from src.core.jobs import JobStore
    from src.core.publisher import PublisherEngine
    from tests.unit.test_publishing_engine import FakePublisher

    db, accounts, video = setup
    JobStore(db).create_post(video, "x", [(accounts.get_active_accounts()[0].id, {"privacy_level": "SELF_ONLY"})])
    fake = FakePublisher("tiktok")
    engine = PublisherEngine(db, accounts, publishers={"tiktok": fake}, sleep=lambda s: None, probe_media=False)
    with held_by_other_process(publish_lock.LOCK_PATH), patch("builtins.input", side_effect=["1"]),             patch("src.cli.publish_menu.clear_screen"):
        run_publishing_queue(engine)
    assert BUSY_MESSAGE in capsys.readouterr().out and fake.calls == []
    assert {j.status for j in engine.store.recent_jobs()} == {"pending"}


def test_content_inbox_busy_skips_package_untouched(content_env):
    from tests.unit import test_content

    test_content.save_profile(content_env)
    test_content.make_package(content_env[3])
    pubs = test_content.TestIntakePublishing().pubs()
    intake = test_content.intake_with(content_env, pubs)
    (entry,) = intake.scan()
    with held_by_other_process(publish_lock.LOCK_PATH):
        result = intake.publish(entry.package)
        ready = intake.publish_ready()
    assert result.outcome == "skipped" and result.message == BUSY_MESSAGE
    assert all(r.outcome == "skipped" for r in ready)
    assert (content_env[3] / "incoming" / "post_001").exists()  # not moved
    assert all(p.calls == [] for p in pubs.values())
    assert intake.publish(entry.package).outcome == "published"  # lock free again: normal behaviour
