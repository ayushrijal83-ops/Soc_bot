# ruff: noqa: F811 - pytest fixtures imported from other test modules
"""Locked Instagram batch architecture: ONE shared media session/tunnel per batch, max N active jobs,
one automatic retry round for the batch's failed jobs, resume safety. No network: fakes + mocked Graph."""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from src.core.jobs import JobStore
from src.core.published_links import PublishedLinks
from src.core.publisher import PublisherEngine
from src.media_storage import MediaStorageError, SharedMediaSession
from src.media_storage.provider import MediaHandle, MediaSourceProvider
from src.media_storage.tempfile import TempFileMediaStorage
from src.platforms.base import PlatformPublisher, PublishError, PublishOutcome
from src.platforms.instagram.publisher import InstagramPublisher
from tests.unit.test_cloudflare_tunnel import (  # noqa: F401 - exe fixture
    FakePopen,
    exe,
    provider,
)
from tests.unit.test_fanout import jpeg_bytes
from tests.unit.test_publishing_engine import env  # noqa: F401

IG = "https://graph.instagram.com/v25.0"


@pytest.fixture
def cover(tmp_path):
    p = tmp_path / "cover.jpg"
    p.write_bytes(jpeg_bytes())
    return p


def add_accounts(services, n, prefix="ig"):
    _, accounts, _, _ = services
    future = datetime.now(timezone.utc) + timedelta(days=30)
    return [accounts.create_account(platform="instagram", platform_account_id=f"{prefix}{i:03d}",
                                    username=f"{prefix}_account{i:02d}", access_token="T", expires_at=future).id
            for i in range(1, n + 1)]


def batch(services, n, cover=None, auto_retry=True, prefix="ig"):
    db, _, _, video = services
    ids = add_accounts(services, n, prefix)
    options = {"cover_path": str(cover)} if cover else {}
    return JobStore(db).create_post(video, "caption", [(i, dict(options)) for i in ids], auto_retry=auto_retry), ids


class Graph:
    """Thread-safe fake Instagram Graph API for many accounts. ``errors``: account -> container ERRORs to return."""

    def __init__(self, errors=None, on_publish=None):
        self.errors = dict(errors or {})
        self.on_publish = on_publish
        self.lock = threading.Lock()
        self.containers = {}  # container id -> (account, status)
        self.bodies = []
        self.seq = 0

    def handler(self, request):
        path = request.url.path.removeprefix("/v25.0/")
        with self.lock:
            if request.method == "POST" and path.endswith("/media"):
                account = path.split("/")[0]
                self.seq += 1
                cid = f"C{self.seq}-{account}"
                failing = self.errors.get(account, 0) > 0
                if failing:
                    self.errors[account] -= 1
                self.containers[cid] = (account, "ERROR" if failing else "FINISHED")
                self.bodies.append((account, parse_qs(request.content.decode())))
                return httpx.Response(200, json={"id": cid})
            if request.method == "POST" and path.endswith("/media_publish"):
                cid = parse_qs(request.content.decode())["creation_id"][0]
                if self.on_publish:
                    self.on_publish()
                return httpx.Response(200, json={"id": "M" + cid})
            if request.method == "GET" and path in self.containers:
                return httpx.Response(200, json={"status_code": self.containers[path][1]})
            if request.method == "GET" and path.startswith("MC"):
                return httpx.Response(200, json={"permalink": f"https://www.instagram.com/reel/{path}/"})
        raise AssertionError(f"unexpected {request.method} {path}")

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def ig_engine(services, graph, factory, limit=5, links=None):
    db, accounts, _, _ = services
    ig = InstagramPublisher(client=graph.client(), sleep=lambda s: None, poll_interval=0, media_provider=factory)
    sleeps = []
    eng = PublisherEngine(db, accounts, publishers={"instagram": ig}, sleep=sleeps.append, probe_media=False,
                          max_concurrent=limit, links=links)
    eng.sleeps = sleeps
    return eng


# ---------------------------------------------------------------------------------------------
# A/B/I/K. One shared tunnel per batch, shared URLs, cleanup only after the whole batch
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("accounts", [2, 5, 11])
def test_one_batch_one_tunnel_shared_urls(env, exe, cover, tmp_path, accounts):
    popen = FakePopen()
    alive_at_publish = []
    graph = Graph(on_publish=lambda: alive_at_publish.append(not popen.processes[0].terminated))
    links = PublishedLinks(tmp_path / "links")
    post_id, _ = batch(env, accounts, cover)
    result = ig_engine(env, graph, lambda: provider(exe, popen), links=links).publish_post(post_id)
    assert result.count("published") == accounts
    assert len(popen.calls) == 1  # ONE cloudflared for the whole batch
    videos = {b["video_url"][0] for _, b in graph.bodies}
    covers = {b["cover_url"][0] for _, b in graph.bodies}
    assert len(videos) == 1 and len(covers) == 1  # one video route + one cover route, shared by every job
    assert all(alive_at_publish) and len(alive_at_publish) == accounts  # never stopped after a single job
    assert popen.processes[0].terminated  # stopped once, after the batch
    records = links.records("instagram")
    assert len(records) == accounts and not any("trycloudflare" in r["url"] for r in records)
    assert links.text_path("instagram").read_text(encoding="utf-8").splitlines() == [r["url"] for r in records]
    assert list(cover.parent.glob("cover*")) == [cover]  # one cover file, never copied


def test_shared_session_survives_the_retry_round_and_closes_after_it(env, exe, cover, tmp_path):
    popen = FakePopen()
    states = []
    graph = Graph(errors={"ig003": 1}, on_publish=lambda: states.append(not popen.processes[0].terminated))
    links = PublishedLinks(tmp_path / "links")
    post_id, _ = batch(env, 5, cover)
    eng = ig_engine(env, graph, lambda: provider(exe, popen), links=links)
    result = eng.publish_post(post_id)
    assert result.count("published") == 5 and result.status == "completed"
    assert len(popen.calls) == 1 and all(states) and len(states) == 5  # retry used the SAME tunnel
    assert popen.processes[0].terminated
    ig003 = [b for a, b in graph.bodies if a == "ig003"]
    assert len(ig003) == 2  # retry = a NEW container (the ERROR one is not reused)
    assert ig003[0]["video_url"] == ig003[1]["video_url"] and ig003[0]["cover_url"] == ig003[1]["cover_url"]
    job = next(j for j in eng.store.jobs_for_post(post_id) if j.account_label == "ig_account03")
    attempts = eng.store.attempts(job.id)
    assert [a.status for a in attempts] == ["failed", "success"]  # original failure kept in history
    first = json.loads(attempts[0].response_json)["provider_state"]["container_id"]
    second = json.loads(attempts[1].response_json)["provider_state"]["container_id"]
    assert first != second
    assert eng.sleeps == [5.0]  # short cooldown before the retry round
    assert len(links.records("instagram")) == 5  # retry success saved once, failure saved nothing


def test_dead_shared_tunnel_is_replaced_once_for_the_batch(env, exe, cover):
    popen = FakePopen()
    graph = Graph()

    def kill_tunnel():  # cloudflared dies while the batch runs
        if len(graph.bodies) == 2 and len(popen.processes) == 1:
            popen.processes[0].terminate()

    graph.on_publish = kill_tunnel
    post_id, _ = batch(env, 6, cover)
    result = ig_engine(env, graph, lambda: provider(exe, popen), limit=1).publish_post(post_id)
    assert result.count("published") == 6
    assert len(popen.calls) == 2  # original + ONE replacement, never per job


# ---------------------------------------------------------------------------------------------
# E/F/G/H. Automatic retry round (deterministic fault injection)
# ---------------------------------------------------------------------------------------------

class FlakyInstagram(PlatformPublisher):
    """Fake Instagram: fails an account's first ``fails[account]`` publishes; counts concurrency and sessions."""

    PLATFORM = "instagram"

    def __init__(self, fails=None, hold=0.0, uncertain=()):
        super().__init__(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        self.fails, self.hold, self.uncertain = dict(fails or {}), hold, set(uncertain)
        self.lock = threading.Lock()
        self.calls, self.sessions = [], []
        self.active = self.max_active = 0
        self.closed = []

    def validate(self, caption, options, media):
        return []

    def batch_media(self):
        publisher = self

        class Session:
            def close(self):
                publisher.closed.append(len(publisher.calls))

        session = Session()
        self.sessions.append(session)
        return session

    def publish(self, ctx, on_progress):
        account = ctx.platform_account_id
        with self.lock:
            self.calls.append((account, ctx.media_provider))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            failing = self.fails.get(account, 0) > 0
            if failing:
                self.fails[account] -= 1
        try:
            time.sleep(self.hold)
            if failing:
                on_progress("processing", {"container_id": f"C-{account}-{len(self.calls)}"})
                raise PublishError("Instagram media processing failed (status ERROR)", code="processing_failed",
                                   uncertain=account in self.uncertain)
            return PublishOutcome("published", f"M-{account}",
                                  {"permalink": f"https://www.instagram.com/reel/M{account}/"})
        finally:
            with self.lock:
                self.active -= 1

    def published_url(self, platform_media_id, state):
        return state.get("permalink")


def engine_for(services, publisher, limit=5, links=None):
    db, accounts, _, _ = services
    sleeps = []
    eng = PublisherEngine(db, accounts, publishers={"instagram": publisher}, sleep=sleeps.append,
                          probe_media=False, max_concurrent=limit, links=links)
    eng.sleeps = sleeps
    return eng


def calls_per_account(publisher):
    counts = {}
    for account, _ in publisher.calls:
        counts[account] = counts.get(account, 0) + 1
    return counts


class TestAutomaticRetry:
    def test_no_failures_no_retry_round(self, env):
        post_id, _ = batch(env, 5)
        fake = FlakyInstagram()
        eng = engine_for(env, fake)
        assert eng.publish_post(post_id).count("published") == 5
        assert len(fake.calls) == 5 and eng.sleeps == []

    def test_four_ok_one_failure_retried_once_and_recovers(self, env, tmp_path):
        post_id, _ = batch(env, 5)
        fake = FlakyInstagram(fails={"ig003": 1})
        links = PublishedLinks(tmp_path / "links")
        eng = engine_for(env, fake, links=links)
        result = eng.publish_post(post_id)
        assert result.count("published") == 5 and result.status == "completed"
        assert calls_per_account(fake) == {"ig001": 1, "ig002": 1, "ig003": 2, "ig004": 1, "ig005": 1}
        assert len({id(session) for _, session in fake.calls}) == 1  # both rounds: the same shared session
        assert fake.closed == [6]  # closed ONCE, after the retry round (6 publishes done)
        assert len(links.records("instagram")) == 5

    def test_retry_fails_again_stays_failed_exactly_two_attempts(self, env, tmp_path):
        post_id, _ = batch(env, 1)
        fake = FlakyInstagram(fails={"ig001": 5})
        links = PublishedLinks(tmp_path / "links")
        eng = engine_for(env, fake, links=links)
        result = eng.publish_post(post_id)
        (job,) = eng.store.jobs_for_post(post_id)
        assert result.status == "failed" and job.status == "failed" and job.auto_retry_used
        assert len(fake.calls) == 2 and len(eng.store.attempts(job.id)) == 2  # never a third
        eng.publish_post(post_id)  # running the batch again never adds a third automatic attempt
        eng.resume_open_jobs()
        assert len(fake.calls) == 2 and links.records("instagram") == []

    def test_multiple_failures_each_retried_once(self, env):
        post_id, _ = batch(env, 6)
        fake = FlakyInstagram(fails={"ig002": 1, "ig004": 2, "ig006": 1})
        result = engine_for(env, fake).publish_post(post_id)
        assert calls_per_account(fake) == {"ig001": 1, "ig002": 2, "ig003": 1, "ig004": 2, "ig005": 1, "ig006": 2}
        statuses = {j.account_label: j.status for j in result.jobs}
        assert statuses["ig_account04"] == "failed" and result.count("published") == 5
        assert result.status == "completed_with_failures"

    def test_retry_round_waits_for_the_whole_initial_round(self, env):
        post_id, _ = batch(env, 8)
        fake = FlakyInstagram(fails={"ig001": 1}, hold=0.05)
        engine_for(env, fake, limit=3).publish_post(post_id)
        order = [account for account, _ in fake.calls]
        positions = [i for i, account in enumerate(order) if account == "ig001"]
        assert len(positions) == 2 and positions[1] == 8  # its retry is the 9th publish: after all 8 initial jobs

    def test_retry_round_concurrency_is_limited(self, env):
        post_id, _ = batch(env, 13)
        fake = FlakyInstagram(fails={f"ig{i:03d}": 1 for i in range(1, 14)}, hold=0.3)
        result = engine_for(env, fake, limit=5).publish_post(post_id)
        assert result.count("published") == 13 and len(fake.calls) == 26
        assert fake.max_active == 5

    def test_retry_delay_is_configurable(self, env, monkeypatch):
        monkeypatch.setenv("INSTAGRAM_FAILURE_RETRY_DELAY_SECONDS", "2")
        post_id, _ = batch(env, 2)
        eng = engine_for(env, FlakyInstagram(fails={"ig001": 1}))
        eng.publish_post(post_id)
        assert eng.sleeps == [2.0]

    def test_uncertain_failures_are_not_retried_automatically(self, env):
        post_id, _ = batch(env, 2)
        fake = FlakyInstagram(fails={"ig001": 1}, uncertain={"ig001"})
        eng = engine_for(env, fake)
        eng.publish_post(post_id)
        assert calls_per_account(fake) == {"ig001": 1, "ig002": 1}
        assert eng.store.retry_owed_post_ids(("instagram",)) == []  # not owed forever either


class TestBatchIsolationAndResume:
    def test_old_and_legacy_failures_are_never_retried(self, env):
        old_post, _ = batch(env, 2, auto_retry=False, prefix="old")  # created before this feature
        engine_for(env, FlakyInstagram(fails={"old001": 1})).publish_post(old_post)
        new_post, _ = batch(env, 2, prefix="new")
        fake = FlakyInstagram(fails={"new002": 1})
        eng = engine_for(env, fake)
        eng.publish_post(new_post)
        eng.resume_open_jobs()
        assert calls_per_account(fake) == {"new001": 1, "new002": 2}  # old001 never touched
        old = {j.account_label: j.status for j in eng.store.jobs_for_post(old_post)}
        assert old == {"old_account01": "failed", "old_account02": "published"}

    def test_crash_before_retry_round_resumes_only_the_owed_retries(self, env):
        post_id, _ = batch(env, 4)
        store = JobStore(env[0])
        jobs = store.jobs_for_post(post_id)
        for job, final in zip(jobs, ("published", "published", "failed", "failed")):  # initial round done, then crash
            store.claim(job.id)
            store.transition(job.id, final, platform_media_id="OLD" if final == "published" else None)
        fake = FlakyInstagram()
        engine_for(env, fake).resume_open_jobs()
        assert calls_per_account(fake) == {"ig003": 1, "ig004": 1}
        final = {j.account_label: (j.status, j.platform_media_id) for j in store.jobs_for_post(post_id)}
        assert final["ig_account01"] == ("published", "OLD")  # never republished

    def test_crash_during_retry_round_never_gives_a_second_automatic_retry(self, env):
        post_id, _ = batch(env, 3)
        store = JobStore(env[0])
        a, b, c = store.jobs_for_post(post_id)
        for job in (a, b, c):
            store.claim(job.id)
            store.transition(job.id, "failed", error_message="x")
        store.mark_auto_retry_used(a.id)  # a: retry finished and failed again
        store.mark_auto_retry_used(b.id)  # b: retry started, app crashed mid-way
        store.transition(b.id, "retrying")
        fake = FlakyInstagram()
        engine_for(env, fake).resume_open_jobs()
        assert calls_per_account(fake) == {"ig002": 1, "ig003": 1}  # b continues once, c gets its retry, a: none
        assert store.get_job(a.id).status == "failed"

    def test_manual_retry_takes_over_the_automatic_one(self, env):
        post_id, _ = batch(env, 1)
        fake = FlakyInstagram(fails={"ig001": 1})
        eng = engine_for(env, fake)
        store = eng.store
        (job,) = store.jobs_for_post(post_id)
        store.claim(job.id)
        store.transition(job.id, "failed", error_message="x")  # initial failure, retry round not run yet
        eng.retry_job(job.id)  # user retries by hand (fails once more)
        assert store.get_job(job.id).auto_retry_used and store.get_job(job.id).status == "failed"
        eng.resume_open_jobs()
        assert len(fake.calls) == 1  # automation never follows the manual retry


# ---------------------------------------------------------------------------------------------
# SharedMediaSession unit behavior
# ---------------------------------------------------------------------------------------------

class CountingProvider(MediaSourceProvider):
    starts = 0

    def __init__(self, fail=None, alive=True):
        self.fail, self.alive, self.cleaned = fail, alive, []

    def prepare(self, video_path, content_type, cover_path=None):
        CountingProvider.starts += 1
        time.sleep(0.05)
        if self.fail:
            raise self.fail
        return MediaHandle(video_path, content_type, public_url="https://h.example/v.mp4",
                           cover_url="https://h.example/c.jpg" if cover_path else None)

    def get_public_url(self, handle):
        return handle.public_url

    def is_alive(self, handle):
        return self.alive

    def cleanup(self, handle):
        self.cleaned.append(handle)


class TestSharedSession:
    def test_concurrent_jobs_start_one_session(self, tmp_path):
        CountingProvider.starts = 0
        session = SharedMediaSession(CountingProvider)
        handles = []
        threads = [threading.Thread(target=lambda: handles.append(session.prepare(tmp_path / "v.mp4", "video/mp4")))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert CountingProvider.starts == 1 and session.starts == 1 and len({id(h) for h in handles}) == 1
        session.cleanup(handles[0])  # a job's cleanup is a no-op
        assert session.get_public_url(handles[0]) == "https://h.example/v.mp4"
        session.close()
        with pytest.raises(MediaStorageError, match="not part of this session"):
            session.get_public_url(handles[0])

    def test_close_releases_once(self, tmp_path):
        made = []

        def factory():
            made.append(CountingProvider())
            return made[-1]

        session = SharedMediaSession(factory)
        handle = session.prepare(tmp_path / "v.mp4", "video/mp4")
        session.close()
        session.close()
        assert made[0].cleaned == [handle]

    def test_failed_start_is_not_hammered(self, tmp_path):
        CountingProvider.starts = 0
        now = [100.0]
        session = SharedMediaSession(lambda: CountingProvider(fail=MediaStorageError("429", retryable=True)),
                                     clock=lambda: now[0])
        for _ in range(4):
            with pytest.raises(MediaStorageError, match="429") as e:
                session.prepare(tmp_path / "v.mp4", "video/mp4")
            assert e.value.retryable
        assert CountingProvider.starts == 1  # one provisioning attempt for 4 jobs
        now[0] += 31
        with pytest.raises(MediaStorageError):
            session.prepare(tmp_path / "v.mp4", "video/mp4")
        assert CountingProvider.starts == 2  # tried again after the cooldown

    def test_dead_session_replaced(self, tmp_path):
        providers = []

        def factory():
            providers.append(CountingProvider(alive=bool(providers)))  # the first one is "dead"
            return providers[-1]

        session = SharedMediaSession(factory)
        first = session.prepare(tmp_path / "v.mp4", "video/mp4")
        second = session.prepare(tmp_path / "v.mp4", "video/mp4")
        third = session.prepare(tmp_path / "v.mp4", "video/mp4")
        assert first is not second and second is third and session.starts == 2
        assert providers[0].cleaned == [first]


def test_tempfile_upload_is_refreshed_before_it_expires(tmp_path):
    storage = TempFileMediaStorage(expiry_hours=1, client=httpx.Client(transport=httpx.MockTransport(lambda r: None)))
    fresh = MediaHandle(tmp_path / "v.mp4", "video/mp4")
    old = MediaHandle(tmp_path / "v.mp4", "video/mp4")
    old.created_at -= 3100  # > 1 h - 10 min
    assert storage.is_alive(fresh) and not storage.is_alive(old)


def test_plan_mentions_shared_tunnel_and_starts_nothing(env, exe, monkeypatch, cover):
    from unittest.mock import patch

    monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
    db, accounts, _, video = env
    ids = add_accounts(env, 3)
    no_network = httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network")))
    eng = PublisherEngine(db, accounts, publishers={"instagram": InstagramPublisher(client=no_network)},
                          probe_media=False)
    with patch("src.media_storage.cloudflare_tunnel.subprocess.Popen", side_effect=AssertionError("cloudflared")):
        plan = eng.plan_destinations(video, "c", [(i, {"cover_path": str(cover)}) for i in ids])
    assert all(item.ready for item in plan)
    assert "ONE tunnel shared by all Instagram jobs of this batch" in " ".join(plan[0].notes)
    assert eng.store.recent_jobs() == []
