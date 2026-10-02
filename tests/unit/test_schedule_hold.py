# ruff: noqa: F811 - pytest fixtures imported from other test modules
"""V2.2 phase 1: scheduling columns (migration 006) and the JobStore hold gate.

Invariant: a held post (schedule_status scheduled / missed / cancelled) keeps ordinary pending jobs that
can never be claimed, resumed or auto-retried, so nothing can publish it early.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, text, update

from src.core.jobs import JobError, JobStore
from src.storage.database import Database, Post, PublishJob
from tests.unit.test_publishing_engine import (  # noqa: F401
    OPTIONS,
    FakePublisher,
    engine,
    env,
    ok,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "storage" / "migrations"
FUTURE = datetime.now(timezone.utc) + timedelta(days=3)
PLATFORMS = ("instagram", "tiktok", "youtube")


def create(env, schedule_status=None, scheduled_at=None, platforms=PLATFORMS, **kw):
    db, _, ids, video = env
    return JobStore(db).create_post(video, "Hello", [(ids[p], OPTIONS[p]) for p in platforms],
                                    scheduled_at=scheduled_at, schedule_status=schedule_status, **kw)


def scheduled(env, **kw):
    return create(env, "scheduled", FUTURE, **kw)


def set_status(env, post_id, status):
    with env[0].session() as session:
        session.execute(update(Post).where(Post.id == post_id).values(schedule_status=status))
        session.commit()


def post_row(env, post_id):
    with env[0].session() as session:
        return session.get(Post, post_id)


def fail_job(env, job_id):
    with env[0].session() as session:
        session.execute(update(PublishJob).where(PublishJob.id == job_id).values(status="failed"))
        session.commit()


def publishers():
    return {p: FakePublisher(p, ok(f"{p}-1")) for p in PLATFORMS}


# --- migration --------------------------------------------------------------------------------

def columns(db, table="posts"):
    with db.session() as session:
        return {r[1] for r in session.execute(text(f"PRAGMA table_info({table})"))}


def indexes(db):
    with db.session() as session:
        return {r[1] for r in session.execute(text("PRAGMA index_list(posts)"))}


def test_fresh_database_has_schedule_columns(env):
    db = env[0]
    assert {"scheduled_at", "schedule_status", "schedule_json"} <= columns(db)
    assert "idx_posts_schedule" in indexes(db)
    assert db.get_applied_migrations()[-1] == 6
    post_id = create(env)
    with db.session() as session, pytest.raises(Exception, match="CHECK"):
        session.execute(text("UPDATE posts SET schedule_status = 'bogus' WHERE id = :id"), {"id": post_id})


@pytest.mark.parametrize("upto", [1, 5])
def test_upgrade_keeps_existing_posts_unscheduled(tmp_path, upto):
    db = Database(f"sqlite:///{tmp_path / 'old.db'}")
    for path in sorted(MIGRATIONS.glob("*.sql"))[:upto]:
        db.apply_migration(int(path.stem.split("_")[0]), path.stem, path.read_text())
    with db.session() as session:
        session.execute(text("INSERT INTO videos (filename, path, size_bytes) VALUES ('a.mp4', 'a.mp4', 1)"))
        session.execute(text("INSERT INTO posts (video_id, caption) VALUES (1, 'old')"))
        session.commit()
    assert "scheduled_at" not in columns(db)

    db.create_all()            # what main.py does on every start
    db.migrate(verbose=False)
    db.migrate(verbose=False)  # second run: nothing left to apply
    assert db.get_applied_migrations() == [1, 2, 3, 4, 5, 6]
    assert {"scheduled_at", "schedule_status", "schedule_json"} <= columns(db) and "idx_posts_schedule" in indexes(db)
    with db.session() as session:
        old = session.get(Post, 1)
        assert old.caption == "old" and (old.scheduled_at, old.schedule_status, old.schedule_json) == (None,) * 3
    db.engine.dispose()


def test_existing_posts_behave_as_before(env):
    post_id = create(env)
    row = post_row(env, post_id)
    assert (row.scheduled_at, row.schedule_status, row.schedule_json) == (None, None, None)
    store = JobStore(env[0])
    assert store.open_post_ids() == [post_id]
    pubs = publishers()
    result = engine(env, pubs).publish_post(post_id)
    assert result.status == "completed" and all(len(p.calls) == 1 for p in pubs.values())


# --- hold gate: claim -------------------------------------------------------------------------

@pytest.mark.parametrize("status, claimable", [("scheduled", False), ("missed", False), ("cancelled", False),
                                               ("released", True), (None, True)])
def test_claim_respects_schedule_hold(env, status, claimable):
    post_id = scheduled(env) if status else create(env)
    if status:
        set_status(env, post_id, status)
    store = JobStore(env[0])
    job = store.jobs_for_post(post_id)[0]
    assert store.claim(job.id) is claimable
    assert store.get_job(job.id).status == ("uploading" if claimable else "pending")


def test_claim_also_blocks_retrying_jobs_of_held_posts(env):
    post_id = scheduled(env, platforms=("instagram",))
    store = JobStore(env[0])
    job = store.jobs_for_post(post_id)[0]
    with env[0].session() as session:
        session.execute(update(PublishJob).where(PublishJob.id == job.id).values(status="retrying"))
        session.commit()
    assert store.claim(job.id) is False and store.get_job(job.id).status == "retrying"


# --- hold gate: open / retry-owed -------------------------------------------------------------

@pytest.mark.parametrize("status, listed", [("scheduled", False), ("missed", False), ("cancelled", False),
                                            ("released", True)])
def test_open_post_ids_excludes_held_posts(env, status, listed):
    ordinary = create(env, platforms=("instagram",))
    post_id = scheduled(env, platforms=("tiktok",))
    set_status(env, post_id, status)
    assert JobStore(env[0]).open_post_ids() == ([ordinary, post_id] if listed else [ordinary])


def test_retry_owed_excludes_held_posts_and_keeps_ordinary_behaviour(env):
    store = JobStore(env[0])
    ordinary = create(env, platforms=("instagram",), auto_retry=True)
    held = {s: scheduled(env, platforms=("instagram",), auto_retry=True) for s in ("scheduled", "missed", "cancelled")}
    released = scheduled(env, platforms=("instagram",), auto_retry=True)
    for status, post_id in held.items():
        set_status(env, post_id, status)
    set_status(env, released, "released")
    for post_id in (ordinary, released, *held.values()):
        fail_job(env, store.jobs_for_post(post_id)[0].id)
    assert store.retry_owed_post_ids(("instagram",)) == [ordinary, released]
    # Ordinary posts: once the automatic retry is used, they are no longer owed (unchanged behaviour).
    store.mark_auto_retry_used(store.jobs_for_post(ordinary)[0].id)
    assert store.retry_owed_post_ids(("instagram",)) == [released]


# --- atomic creation --------------------------------------------------------------------------

def test_scheduled_post_and_jobs_commit_together(env):
    db = env[0]
    commits = []
    listener = lambda session: commits.append(1)
    event.listen(db.Session, "after_commit", listener)
    try:
        post_id = scheduled(env, schedule_json={"source": "manual", "zone": "Asia/Tokyo"})
    finally:
        event.remove(db.Session, "after_commit", listener)
    assert len(commits) == 1  # post, schedule and jobs in ONE transaction
    row = post_row(env, post_id)
    assert row.schedule_status == "scheduled" and row.schedule_json == '{"source": "manual", "zone": "Asia/Tokyo"}'
    assert row.scheduled_at == FUTURE.replace(tzinfo=None)  # stored as naive UTC
    store = JobStore(db)
    jobs = store.jobs_for_post(post_id)
    assert len(jobs) == 3 and {j.status for j in jobs} == {"pending"}
    assert not any(store.claim(j.id) for j in jobs)


def test_failed_scheduled_creation_leaves_nothing(env):
    db, _, ids, video = env
    with pytest.raises(JobError, match="not found"):
        JobStore(db).create_post(video, "x", [(ids["instagram"], {}), (9999, {})],
                                 scheduled_at=FUTURE, schedule_status="scheduled")
    with db.session() as session:
        assert session.query(Post).count() == 0 and session.query(PublishJob).count() == 0


def test_schedule_arguments_validated(env):
    with pytest.raises(JobError, match="Unknown schedule status"):
        create(env, "later", FUTURE)
    with pytest.raises(JobError, match="needs scheduled_at"):
        create(env, "scheduled", None)
    naive = datetime(2026, 12, 1, 9, 30)  # noqa: DTZ001 - deliberately naive (UTC by convention)
    post_id = create(env, "scheduled", naive)  # naive = UTC (project convention)
    assert post_row(env, post_id).scheduled_at == naive
    tokyo = datetime(2026, 12, 1, 18, 30, tzinfo=timezone(timedelta(hours=9)))
    assert post_row(env, create(env, "scheduled", tokyo)).scheduled_at == naive


# --- the audit hazard: direct publishing of a held post ---------------------------------------

@pytest.mark.parametrize("status", ["scheduled", "missed", "cancelled"])
def test_publish_post_on_held_post_calls_no_adapter(env, status):
    post_id = scheduled(env)
    set_status(env, post_id, status)
    pubs = publishers()
    eng = engine(env, pubs)
    result = eng.publish_post(post_id)
    assert all(p.calls == [] for p in pubs.values())
    assert {j.status for j in result.jobs} == {"pending"}
    assert {j.status for j in eng.store.jobs_for_post(post_id)} == {"pending"}
    assert post_row(env, post_id).schedule_status == status
    assert eng.resume_open_jobs() == [] and all(p.calls == [] for p in pubs.values())


def test_released_post_publishes_normally(env):
    post_id = scheduled(env)
    set_status(env, post_id, "released")
    pubs = publishers()
    result = engine(env, pubs).publish_post(post_id)
    assert result.status == "completed" and all(len(p.calls) == 1 for p in pubs.values())
    assert post_row(env, post_id).schedule_status == "released"
