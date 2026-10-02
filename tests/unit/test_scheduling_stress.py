# ruff: noqa: F811, DTZ001 - fixtures imported from other modules; naive datetimes are wall-clock input
"""V2.3 phase 3: multi-post scheduling stress / reliability (fakes only; no real platforms).

Outcomes are keyed by caption (not by call order), so ordering, isolation and duplicate checks are exact.
Cross-process tests run real `main.py --run-due` processes against one temporary database and lock file.
"""

import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.jobs import JobStore
from src.core.publish_lock import PublishLockBusy
from src.core.publisher import PublisherEngine
from src.platforms.base import PublishError
from src.services.due_scheduler import DueScheduler
from src.services.publishing import PublishingService
from src.services.scheduling import SchedulingError, SchedulingService
from src.storage.database import Database, Post, PublishAttempt, PublishJob
from tests.unit.test_publishing_engine import (  # noqa: F401
    OPTIONS,
    FakePublisher,
    env,
    ok,
)

UTC = timezone.utc
S = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


class Scripted(FakePublisher):
    """Fake adapter whose outcome depends on the post's caption. Unscripted captions publish.
    Captions starting with INVALID fail validation (before any provider call). Optional delay and a file that
    records every provider call (one caption per line), so calls can be counted across processes."""

    def __init__(self, platform, script=None, delay=0.0, record=None, restart_safe=True):
        super().__init__(platform, restart_safe=restart_safe)
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.delay, self.record, self.guard = delay, record, threading.Lock()

    def validate(self, caption, options, media):
        return ["caption rejected by test"] if caption.startswith("INVALID") else []

    def publish(self, ctx, on_progress):
        with self.guard:
            self.calls.append(ctx)
            if self.record:
                with open(self.record, "a", encoding="utf-8") as f:
                    f.write(ctx.caption + "\n")
            actions = self.script.get(ctx.caption)
            action = actions.pop(0) if actions else ok(f"M-{ctx.caption}")
        on_progress("uploading", {"ref": ctx.caption})
        if self.delay:
            time.sleep(self.delay)
        if isinstance(action, PublishError):
            raise action
        return action

    def calls_for(self, caption):
        return sum(1 for c in self.calls if c.caption == caption)


def stack(env, publishers, database=None, max_posts=None, **kw):
    db, accounts, _, _ = env
    eng = PublisherEngine(database or db, accounts, publishers=publishers, sleep=lambda s: None,
                          retry_delays=kw.pop("retry_delays", (1, 2, 3)), probe_media=False, **kw)
    publishing = PublishingService(eng, accounts)
    sched = DueScheduler(publishing, **({"max_posts": max_posts} if max_posts else {}))
    return eng, publishing, sched


def schedule(env, caption, at, platform="instagram", auto_retry=True, database=None):
    db, _, ids, video = env
    return JobStore(database or db).create_post(video, caption, [(ids[platform], OPTIONS[platform])],
                                                auto_retry=auto_retry, scheduled_at=at, schedule_status="scheduled",
                                                schedule_json={"history": []})


def state(db, post_id):
    with db.session() as s:
        p = s.get(Post, post_id)
        history = [h["action"] for h in json.loads(p.schedule_json or "{}").get("history", [])]
        jobs = s.query(PublishJob).filter_by(post_id=post_id).all()
        return {"status": p.schedule_status, "history": history, "jobs": [j.status for j in jobs],
                "attempts": s.query(PublishAttempt).filter(PublishAttempt.job_id.in_([j.id for j in jobs])).count()}


# --- A. many due posts in one pass ------------------------------------------------------------------

def test_ten_due_posts_oldest_first_each_once(env):
    db = env[0]
    fake = Scripted("instagram")
    _eng, _publishing, sched = stack(env, {"instagram": fake})
    offsets = [7, 2, 9, 2, 5, 0, 8, 1, 4, 3]           # minutes before S; two posts share 2 minutes
    posts = {f"P{i}": schedule(env, f"P{i}", S - timedelta(minutes=m)) for i, m in enumerate(offsets)}
    expected = sorted(posts.values(), key=lambda pid: (-offsets[int(_caption(db, pid)[1:])], pid))
    result = sched.run_due(S)
    assert result.released == expected and result.published == expected and not result.failed
    for caption, pid in posts.items():
        st = state(db, pid)
        assert st == {"status": "released", "history": ["released"], "jobs": ["published"], "attempts": 1}
        assert fake.calls_for(caption) == 1
        assert [j.post_id for j in JobStore(db).jobs_for_post(pid)] == [pid]  # job belongs to its post
    assert sched.run_due(S + timedelta(minutes=1)).released == [] and len(fake.calls) == 10


def _caption(db, pid):
    with db.session() as s:
        return s.get(Post, pid).caption


# --- B. mixed outcomes ------------------------------------------------------------------------------

def test_mixed_outcomes_are_isolated(env):
    db = env[0]
    script = {
        "DEFINITE": [PublishError("rejected", code="forbidden"), PublishError("rejected", code="forbidden")],
        "RETRYABLE": [PublishError("reset", code="network", retryable=True)],
        "UNCERTAIN": [PublishError("lost connection after sending", code="network", uncertain=True)],
    }
    fake = Scripted("instagram", script)
    eng, _publishing, sched = stack(env, {"instagram": fake})
    order = ["OK1", "DEFINITE", "RETRYABLE", "UNCERTAIN", "INVALID-caption", "OK2", "OK3"]
    posts = {c: schedule(env, c, S - timedelta(minutes=10 - i)) for i, c in enumerate(order)}
    missed = schedule(env, "MISSED", S - timedelta(hours=2))
    result = sched.run_due(S)

    assert result.missed == [missed] and fake.calls_for("MISSED") == 0
    assert state(db, missed) == {"status": "missed", "history": ["missed"], "jobs": ["pending"], "attempts": 0}
    assert result.released == [posts[c] for c in order]                     # every due post processed, in order
    assert set(result.published) == {posts[c] for c in ("OK1", "RETRYABLE", "OK2", "OK3")}
    assert set(result.failed) == {posts[c] for c in ("DEFINITE", "UNCERTAIN", "INVALID-caption")}
    assert fake.calls_for("DEFINITE") == 2                     # definite failure: initial + its ONE automatic retry
    assert fake.calls_for("RETRYABLE") == 2                                  # engine in-place retry only
    assert fake.calls_for("UNCERTAIN") == 1                                  # never retried automatically
    assert fake.calls_for("INVALID-caption") == 0                            # failed before any provider call
    for caption in ("OK1", "OK2", "OK3"):
        assert fake.calls_for(caption) == 1
    uncertain_job = JobStore(db).jobs_for_post(posts["UNCERTAIN"])[0]
    assert uncertain_job.status == "failed" and uncertain_job.auto_retry_used
    assert eng.store.last_error(uncertain_job.id)["uncertain"] is True
    invalid_job = JobStore(db).jobs_for_post(posts["INVALID-caption"])[0]
    assert eng.store.last_error(invalid_job.id)["code"] == "validation_failed"   # Phase 2.5: recorded w/o attempt
    assert eng.store.retry_owed_post_ids(("instagram",)) == []
    assert sched.run_due(S + timedelta(minutes=5)).released == [] and len(fake.calls) == 8  # nothing re-run


# --- C. scheduler concurrency -----------------------------------------------------------------------

def test_many_scheduler_threads_one_winner_per_pass(env):
    db = env[0]
    fake = Scripted("instagram", delay=0.05)
    _eng, publishing, _ = stack(env, {"instagram": fake})
    posts = [schedule(env, f"T{i}", S - timedelta(minutes=i)) for i in range(6)]
    barrier, results = threading.Barrier(5), []

    def run():
        barrier.wait()
        results.append(DueScheduler(publishing).run_due(S))

    threads = [threading.Thread(target=run) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    winners = [r for r in results if not r.busy]
    assert len(results) == 5 and sum(len(r.released) for r in results) == 6
    assert all(r.released == [] and r.missed == [] for r in results if r.busy)   # losers changed nothing
    assert len(winners) >= 1 and len(fake.calls) == 6
    for i, pid in enumerate(posts):
        assert fake.calls_for(f"T{i}") == 1 and state(db, pid)["history"] == ["released"]


WRAPPER = """
import sys
from pathlib import Path
root, lock_path, env_file, record, delay, restart_safe = sys.argv[1:7]
sys.path.insert(0, root)
from src.core import publish_lock
publish_lock.LOCK_PATH = Path(lock_path)
import src.core.publisher as publisher
from tests.unit.test_scheduling_stress import Scripted
fake = Scripted("instagram", delay=float(delay), record=record, restart_safe=restart_safe == "1")
publisher.default_publishers = lambda: {"instagram": fake}
_init = publisher.PublisherEngine.__init__
def _no_probe(self, *a, **k):
    k["probe_media"] = False
    _init(self, *a, **k)
publisher.PublisherEngine.__init__ = _no_probe
import main
main.ENV_FILE = Path(env_file)
sys.argv = ["main.py", "--run-due"]
sys.exit(main.main())
"""


@pytest.fixture
def proc_env(tmp_path):
    """One temporary database + lock shared by real `main.py --run-due` processes."""
    import os

    from src.accounts.manager import AccountManager
    from src.storage.tokens import TokenEncryption, generate_key

    key = generate_key()
    url = f"sqlite:///{tmp_path / 'stress.db'}"
    db = Database(url, encryption=TokenEncryption(key.encode()))
    db.create_all()
    db.migrate(verbose=False)
    accounts = AccountManager(db, TokenEncryption(key.encode()))
    account = accounts.create_account(platform="instagram", platform_account_id="1784", username="ig_user",
                                      access_token="TOKEN", expires_at=datetime.now(UTC) + timedelta(days=30))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v" * 2000)
    environ = {k: v for k, v in os.environ.items() if not k.startswith(("SOC_BOT_", "DATABASE_URL", "CONTENT_ROOT"))}
    environ.update(DATABASE_URL=url, ENCRYPTION_KEY=key, CONTENT_ROOT=str(tmp_path / "content"))
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(WRAPPER, encoding="utf-8")

    def schedule_many(captions, offset):
        now = datetime.now(UTC)
        return {c: JobStore(db).create_post(str(video), c, [(account.id, {})], auto_retry=True,
                                            scheduled_at=now + offset - timedelta(seconds=i), schedule_status="scheduled",
                                            schedule_json={"history": []}) for i, c in enumerate(captions)}

    def start(delay=0.0, restart_safe=True):
        return subprocess.Popen([sys.executable, str(wrapper), str(ROOT), str(tmp_path / "publishing.lock"),
                                 str(tmp_path / "none.env"), str(tmp_path / "calls.txt"), str(delay),
                                 "1" if restart_safe else "0"], cwd=tmp_path, env=environ, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def calls():
        path = tmp_path / "calls.txt"
        return path.read_text(encoding="utf-8").split() if path.exists() else []

    yield db, schedule_many, start, calls
    db.engine.dispose()


def test_two_scheduler_processes_one_publisher(proc_env):
    db, schedule_many, start, calls = proc_env
    posts = schedule_many([f"C{i}" for i in range(5)], timedelta(minutes=-1))
    first = start(delay=0.6)                                 # holds the lock for ~3 s while publishing
    for _ in range(200):
        if calls():
            break
        time.sleep(0.05)
    second = start()
    out2, _ = second.communicate(timeout=120)
    out1, _ = first.communicate(timeout=120)
    assert (first.returncode, second.returncode) == (0, 3), out1 + out2
    assert "Scheduled posts were not changed" in out2 and "Published: 5" in out1
    assert sorted(calls()) == sorted(posts)                  # exactly one provider call per post, across processes
    for pid in posts.values():
        assert state(db, pid) == {"status": "released", "history": ["released"], "jobs": ["published"], "attempts": 1}
    again = start()
    again.communicate(timeout=120)
    assert again.returncode == 0 and len(calls()) == 5      # repeated runs never publish again


# --- D. manual publishing vs scheduler -----------------------------------------------------------------

def test_manual_publish_holds_lock_scheduler_busy(env):
    db = env[0]
    started, release = threading.Event(), threading.Event()

    class Slow(Scripted):
        def publish(self, ctx, on_progress):
            if ctx.caption == "MANUAL":
                started.set()
                release.wait(10)
            return super().publish(ctx, on_progress)

    fake = Slow("instagram")
    _eng, publishing, sched = stack(env, {"instagram": fake})
    manual = JobStore(db).create_post(env[3], "MANUAL", [(env[2]["instagram"], {})], auto_retry=True)
    due = [schedule(env, f"D{i}", S - timedelta(minutes=i + 1)) for i in range(3)]
    overdue = schedule(env, "LATE", S - timedelta(hours=3))
    worker = threading.Thread(target=publishing.publish_batch, args=(manual,))
    worker.start()
    assert started.wait(10)
    busy = sched.run_due(S)
    assert busy.busy and busy.released == busy.missed == []
    assert all(state(db, p)["status"] == "scheduled" for p in [*due, overdue])
    release.set()
    worker.join(30)
    assert publishing.batch(manual).status == "completed" and fake.calls_for("MANUAL") == 1  # unaffected
    done = sched.run_due(S)
    assert sorted(done.released) == sorted(due) and done.missed == [overdue]
    assert all(fake.calls_for(f"D{i}") == 1 for i in range(3))


def test_scheduler_holds_lock_manual_publish_busy(env):
    db = env[0]
    started, release = threading.Event(), threading.Event()

    class Slow(Scripted):
        def publish(self, ctx, on_progress):
            if ctx.caption == "S0":
                started.set()
                release.wait(10)
            return super().publish(ctx, on_progress)

    fake = Slow("instagram")
    _eng, publishing, sched = stack(env, {"instagram": fake})
    due = [schedule(env, f"S{i}", S - timedelta(minutes=5 - i)) for i in range(3)]
    manual = JobStore(db).create_post(env[3], "MANUAL", [(env[2]["instagram"], {})], auto_retry=True)
    holder = threading.Thread(target=sched.run_due, args=(S,))
    holder.start()
    assert started.wait(10)
    with pytest.raises(PublishLockBusy):        # the UI turns this into "Another Soc_bot window is publishing…"
        publishing.publish_batch(manual)
    assert state(db, manual)["jobs"] == ["pending"] and fake.calls_for("MANUAL") == 0
    release.set()
    holder.join(30)
    assert all(state(db, p)["jobs"] == ["published"] for p in due)
    assert [fake.calls_for(f"S{i}") for i in range(3)] == [1, 1, 1]
    assert publishing.publish_batch(manual).status == "completed" and fake.calls_for("MANUAL") == 1


# --- E. restarts --------------------------------------------------------------------------------------

def test_restart_with_fresh_processes_and_interrupted_release(env):
    db = env[0]
    future = [schedule(env, f"F{i}", S + timedelta(minutes=10 + i)) for i in range(4)]
    for now in (S, S + timedelta(minutes=5)):                       # restarts before any is due
        fresh = Database(str(db.engine.url))
        _, _, sched = stack(env, {"instagram": Scripted("instagram")}, database=fresh)
        assert sched.run_due(now).released == []
        fresh.engine.dispose()
    assert all(state(db, p)["status"] == "scheduled" for p in future)

    fake = Scripted("instagram")
    fresh = Database(str(db.engine.url))
    _, publishing, sched = stack(env, {"instagram": fake}, database=fresh)
    real = publishing.publish_batch
    crash_on = future[0]

    def crash_first(pid, *a, **k):
        if pid == crash_on:
            raise KeyboardInterrupt                                  # process killed after releasing F0
        return real(pid, *a, **k)

    publishing.publish_batch = crash_first
    with pytest.raises(KeyboardInterrupt):
        sched.run_due(S + timedelta(minutes=20))
    fresh.engine.dispose()
    assert state(db, crash_on)["status"] == "released" and state(db, crash_on)["jobs"] == ["pending"]
    assert all(state(db, p)["status"] == "scheduled" for p in future[1:])

    fresh = Database(str(db.engine.url))                            # restart: later posts handled, F0 not (D5)
    eng, publishing, sched = stack(env, {"instagram": fake}, database=fresh)
    result = sched.run_due(S + timedelta(minutes=21))
    assert result.released == future[1:] and fake.calls_for("F0") == 0
    assert eng.store.open_post_ids() == [crash_on]                  # visible for manual Resume only
    publishing.resume_open()                                         # explicit recovery
    assert fake.calls_for("F0") == 1 and [fake.calls_for(f"F{i}") for i in range(1, 4)] == [1, 1, 1]
    fresh.engine.dispose()


def test_killed_scheduler_process_then_restart(proc_env):
    """A real process is killed mid-upload (restart-unsafe adapter): its post is not continued automatically, the
    lock is freed by the OS, later due posts are published, and Resume records an uncertain failure (no re-upload)."""
    db, schedule_many, start, calls = proc_env
    posts = schedule_many(["K0", "K1", "K2"], timedelta(minutes=-1))     # K0 is oldest
    victim = start(delay=30, restart_safe=False)
    for _ in range(400):
        if calls() == ["K2"] or (calls() and state(db, posts[calls()[0]])["jobs"] == ["uploading"]):
            break
        time.sleep(0.05)
    first = calls()[0]
    victim.kill()
    victim.wait(30)
    assert state(db, posts[first])["status"] == "released" and state(db, posts[first])["jobs"] == ["uploading"]
    rerun = start(restart_safe=False)
    out, _ = rerun.communicate(timeout=120)
    assert rerun.returncode == 0, out                                      # OS released the dead process's lock
    others = [c for c in posts if c != first]
    assert all(state(db, posts[c])["jobs"] == ["published"] for c in others)
    assert state(db, posts[first])["jobs"] == ["uploading"]               # D5: not continued by the scheduler
    accounts = __import__("src.accounts.manager", fromlist=["AccountManager"]).AccountManager(db, db.encryption)
    eng = PublisherEngine(db, accounts, publishers={"instagram": Scripted("instagram", restart_safe=False)},
                          sleep=lambda s: None, probe_media=False)
    PublishingService(eng, accounts).resume_open()                         # manual Resume
    job = JobStore(db).jobs_for_post(posts[first])[0]
    assert job.status == "failed" and eng.store.last_error(job.id)["uncertain"] is True and job.auto_retry_used
    assert sorted(calls()) == sorted(posts)                                # one call each (victim's, then rerun's)


# --- F. max_posts --------------------------------------------------------------------------------------

def test_max_posts_bounds_a_pass_and_nothing_is_lost(env):
    db = env[0]
    fake = Scripted("instagram")
    _eng, _publishing, sched = stack(env, {"instagram": fake}, max_posts=3)
    posts = [schedule(env, f"M{i}", S - timedelta(minutes=30 - i)) for i in range(7)]   # M0 oldest
    passes = [sched.run_due(S + timedelta(minutes=k)).released for k in range(4)]
    assert passes == [posts[0:3], posts[3:6], posts[6:7], []]
    assert [fake.calls_for(f"M{i}") for i in range(7)] == [1] * 7
    late = [schedule(env, f"L{i}", S - timedelta(hours=3, minutes=i)) for i in range(5)]
    first = sched.run_due(S + timedelta(minutes=5))
    assert len(first.missed) == 3 and sched.run_due(S + timedelta(minutes=6)).missed and \
        all(state(db, p)["status"] == "missed" for p in late)


# --- G. state invariants across a busy queue -------------------------------------------------------------

def test_state_invariants_after_mixed_queue(env):
    db = env[0]
    fake = Scripted("instagram", {"BAD": [PublishError("rejected", code="forbidden")] * 2})
    eng, publishing, sched = stack(env, {"instagram": fake})
    service = SchedulingService(publishing, clock=lambda: S - timedelta(hours=1))
    good, bad = schedule(env, "GOOD", S - timedelta(minutes=2)), schedule(env, "BAD", S - timedelta(minutes=1))
    cancelled, future = schedule(env, "CANCEL", S), schedule(env, "FUTURE", S + timedelta(hours=1))
    service.cancel(cancelled)
    assert set(eng.store.open_post_ids()) == set()                       # held posts are not open work
    sched.run_due(S)
    sched.run_due(S + timedelta(minutes=30))
    assert state(db, cancelled)["status"] == "cancelled" and fake.calls_for("CANCEL") == 0
    assert state(db, future)["status"] == "scheduled"
    for pid in (good, bad):
        assert state(db, pid)["history"] == ["released"]                  # never re-released
        with pytest.raises(SchedulingError) as e:
            service.reschedule(pid, datetime(2026, 10, 5, 9, 0), zone="UTC")  # published / released: no reschedule
        assert e.value.code == "invalid_state"
    bad_job = JobStore(db).jobs_for_post(bad)[0]
    assert bad_job.status == "failed" and fake.calls_for("BAD") == 2       # initial + its ONE automatic retry
    assert publishing.retry_job(bad_job.id).status == "completed"          # manual retry still works
    with db.session() as s:
        counts = [s.query(PublishJob).filter_by(post_id=p).count() for p in (good, bad, cancelled, future)]
    assert counts == [1, 1, 1, 1]                                          # no duplicate job rows
