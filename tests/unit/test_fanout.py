# ruff: noqa: F811, RUF059 - pytest fixtures imported from other test modules
"""One video + one cover + many Instagram accounts: covers, routing, selection, 5-at-a-time fan-out, resume.

No network: fake publishers/cloudflared, mocked Graph API, real loopback HTTP server.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from src.core.jobs import JobError, JobStore
from src.core.published_links import PublishedLinks
from src.core.publisher import PublisherEngine, batch_status, max_concurrent_publishes
from src.core.validation import jpeg_info
from src.media_storage import (
    StorageSettings,
    build_router,
    delivery_provider,
    media_delivery_description,
    media_provider_problems,
)
from src.platforms.base import PlatformPublisher, PublishError, PublishOutcome
from src.platforms.instagram.publisher import InstagramPublisher
from tests.unit.test_cloudflare_tunnel import (  # noqa: F401 - exe fixture
    TUNNEL,
    FakePopen,
    exe,
    local_url,
    provider,
)
from tests.unit.test_media_router import S3_ENV
from tests.unit.test_publishers import IG, Progress, ctx, resp
from tests.unit.test_publishers import Provider as GraphProvider
from tests.unit.test_publishing_engine import env  # noqa: F401


def jpeg_bytes(width=1080, height=1920, scan=b"\x12\x34" * 50) -> bytes:
    """Structurally valid baseline JPEG: SOI, APP0, SOF0 (size), SOS, scan data, EOI."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = (b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big")
            + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    sos = b"\xff\xda" + (12).to_bytes(2, "big") + b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00"
    return b"\xff\xd8" + app0 + sof0 + sos + scan + b"\xff\xd9"


@pytest.fixture
def cover(tmp_path):
    p = tmp_path / "cover.jpg"
    p.write_bytes(jpeg_bytes())
    return p


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"v" * 300_000)
    return p


# ---------------------------------------------------------------------------------------------
# A. Cover validation
# ---------------------------------------------------------------------------------------------

class TestCoverValidation:
    def test_valid_jpeg(self, cover):
        assert jpeg_info(cover, 8 * 1024 * 1024) == ([], (1080, 1920))
        assert InstagramPublisher().validate_cover(str(cover)) == []

    @pytest.mark.parametrize("name,data,needle", [
        ("empty.jpg", b"", "empty"),
        ("png.jpg", b"\x89PNG\r\n\x1a\n" + b"x" * 50, "not a JPEG"),
        ("cut.jpg", jpeg_bytes()[:-2], "no JPEG end marker"),
        ("broken.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 3 + b"\xff\xd9", "corrupt"),
        ("noframe.jpg", b"\xff\xd8\xff\xda\x00\x02" + b"\x00" * 4 + b"\xff\xd9", "no JPEG frame header"),
        ("zero.jpg", jpeg_bytes(width=0), "no JPEG frame header"),
    ])
    def test_invalid(self, tmp_path, name, data, needle):
        p = tmp_path / name
        p.write_bytes(data)
        problems = InstagramPublisher().validate_cover(str(p))
        assert problems and needle in problems[0]

    def test_missing_and_oversized(self, tmp_path):
        assert "could not be read" in InstagramPublisher().validate_cover(str(tmp_path / "nope.jpg"))[0]
        big = tmp_path / "big.jpg"
        with open(big, "wb") as f:
            f.write(jpeg_bytes())
            f.truncate(9 * 1024 * 1024)
        assert "max 8 MB" in InstagramPublisher().validate_cover(str(big))[0]

    def test_non_jpeg_extension_is_blocked_not_converted(self, tmp_path):
        p = tmp_path / "cover.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50)
        assert "must be JPEG" in InstagramPublisher().validate_cover(str(p))[0]

    def test_validation_never_modifies_the_cover(self, cover):
        before = (cover.read_bytes(), cover.stat().st_mtime)
        InstagramPublisher().validate_cover(str(cover))
        assert (cover.read_bytes(), cover.stat().st_mtime) == before

    def test_invalid_cover_blocks_the_instagram_destination(self, exe, monkeypatch, tmp_path, video):
        from src.core.validation import MediaInfo

        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        bad = tmp_path / "cover.jpg"
        bad.write_bytes(b"garbage")
        errors = InstagramPublisher().validate("c", {"cover_path": str(bad)}, MediaInfo(str(video), 300_000, "video/mp4"))
        assert any("Instagram cover" in e for e in errors)

    def test_multiple_cover_candidates_block_a_package(self, tmp_path):
        from src.content.detector import ContentDetector

        pkg = tmp_path / "incoming" / "post_001"
        pkg.mkdir(parents=True)
        (pkg / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 100)
        (pkg / "caption.txt").write_text("hi", encoding="utf-8")
        (pkg / "cover.jpg").write_bytes(jpeg_bytes())
        (pkg / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50)
        detected = ContentDetector(tmp_path / "incoming").detect(pkg, "incoming")
        assert not detected.valid and any("multiple cover" in e for e in detected.validation_errors)


# ---------------------------------------------------------------------------------------------
# B/C/I. Video + cover on one local server and one tunnel; per-job isolation
# ---------------------------------------------------------------------------------------------

class TestTunnelWithCover:
    def test_video_and_cover_on_the_same_tunnel(self, exe, video, cover):
        popen = FakePopen()
        p = provider(exe, popen)
        handle = p.prepare(video, "video/mp4", cover_path=cover)
        try:
            assert len(popen.calls) == 1  # ONE tunnel for video + cover
            assert handle.public_url.startswith(TUNNEL + "/media/") and handle.cover_url.startswith(TUNNEL + "/media/")
            assert handle.cover_url.endswith(".jpg") and handle.cover_url != handle.public_url
            assert "cover" not in handle.cover_url  # no file name in the URL
            origin = local_url(handle).rsplit("/media/", 1)[0]
            cover_local = origin + handle.cover_url.removeprefix(TUNNEL)
            r = httpx.get(cover_local)
            assert r.status_code == 200 and r.content == cover.read_bytes()
            assert r.headers["content-type"] == "image/jpeg" and r.headers["content-length"] == str(cover.stat().st_size)
            h = httpx.head(cover_local)
            assert h.status_code == 200 and h.content == b"" and h.headers["content-length"] == str(cover.stat().st_size)
            v = httpx.get(local_url(handle))
            assert v.status_code == 200 and v.headers["content-type"] == "video/mp4" and v.content == video.read_bytes()
            assert httpx.get(local_url(handle), headers={"Range": "bytes=0-9"}).status_code == 206
            for path in ("/media/cover.jpg", "/cover.jpg", "/media/../cover.jpg", "/"):
                assert httpx.get(origin + path).status_code == 404
        finally:
            p.cleanup(handle)
        assert handle.cover_url is None and popen.processes[0].terminated

    def test_missing_cover_file_fails_before_anything_starts(self, exe, video, tmp_path):
        popen = FakePopen()
        with pytest.raises(Exception, match="Cover image not found"):
            provider(exe, popen).prepare(video, "video/mp4", cover_path=tmp_path / "nope.jpg")
        assert popen.calls == []

    def test_same_cover_file_separate_urls_per_job(self, exe, video, cover):
        p = provider(exe)
        a = p.prepare(video, "video/mp4", cover_path=cover)
        b = p.prepare(video, "video/mp4", cover_path=cover)
        try:
            assert a.cover_url != b.cover_url and a.public_url != b.public_url
            origin_b = local_url(b).rsplit("/media/", 1)[0]
            p.cleanup(a)  # job A done: B's video and cover still served
            assert httpx.get(origin_b + b.cover_url.removeprefix(TUNNEL)).content == cover.read_bytes()
            assert httpx.get(local_url(b)).status_code == 200
            assert list(cover.parent.glob("cover*")) == [cover]  # never copied
        finally:
            p.cleanup(b)

    def test_cover_token_never_logged(self, exe, video, cover, caplog, capfd):
        import logging

        p = provider(exe)
        with caplog.at_level(logging.DEBUG):
            handle = p.prepare(video, "video/mp4", cover_path=cover)
            token = handle.cover_url.rsplit("/", 1)[1]
            httpx.get(local_url(handle).rsplit("/media/", 1)[0] + "/media/" + token)
            p.cleanup(handle)
        out = capfd.readouterr()
        assert token not in caplog.text + out.out + out.err


# ---------------------------------------------------------------------------------------------
# D. AUTO routing considers video AND cover
# ---------------------------------------------------------------------------------------------

class TestRoutingWithCover:
    def test_small_video_with_cover_needs_the_tunnel(self, exe, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        assert delivery_provider(10_000_000) == "tempfile"
        assert delivery_provider(10_000_000, needs_cover=True) == "cloudflare_tunnel"
        text = media_delivery_description(10_000_000, needs_cover=True)
        assert "Cloudflare Quick Tunnel" in text and "video + cover need a provider that serves both" in text

    def test_large_video_with_and_without_cover(self, exe, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
        assert delivery_provider(131_925_281) == delivery_provider(131_925_281, True) == "cloudflare_tunnel"

    def test_provider_without_cover_support_never_chosen(self, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")  # conftest: no cloudflared
        for k, v in S3_ENV.items():
            monkeypatch.setenv(k, v)
        router = build_router(StorageSettings.from_env())
        assert router.select(131_925_281).name == "s3"  # without cover: S3 fallback
        problems = media_provider_problems(10_000_000, needs_cover=True)
        assert problems and "can't deliver a cover image" in problems[0]

    def test_explicit_single_provider_without_cover_support(self, monkeypatch):
        monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "tempfile")
        assert "can't deliver a cover image" in media_provider_problems(10_000_000, needs_cover=True)[0]
        assert media_provider_problems(10_000_000) == []


# ---------------------------------------------------------------------------------------------
# Instagram adapter: cover_url on the container only when there is a cover
# ---------------------------------------------------------------------------------------------

def graph_ok(media_id="REEL_1"):
    return (GraphProvider()
            .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": media_id}))
            .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "C1"}))
            .add("GET", f"{IG}/C1", resp(200, {"status_code": "FINISHED"}))
            .add("GET", f"{IG}/{media_id}", resp(200, {"permalink": "https://www.instagram.com/reel/ABC/"})))


class TestInstagramCover:
    def test_cover_url_sent_with_the_video_and_never_persisted(self, exe, video, cover):
        graph = graph_ok()
        popen = FakePopen()
        pub = InstagramPublisher(client=graph.client(), sleep=lambda s: None, poll_interval=0,
                                 media_provider=lambda: provider(exe, popen))
        outcome = pub.publish(ctx(video, {"cover_path": str(cover)}), Progress())
        body = graph.calls("POST", f"{IG}/17841400000/media")[0].content.decode()
        assert "cover_url=https%3A%2F%2Fquiet-river-demo-1234.trycloudflare.com%2Fmedia%2F" in body
        assert "video_url=https%3A%2F%2Fquiet-river-demo-1234.trycloudflare.com%2Fmedia%2F" in body
        assert len(popen.calls) == 1 and popen.processes[0].terminated
        assert outcome.status == "published" and outcome.cover_status == "published"
        assert "trycloudflare" not in json.dumps(outcome.state) and outcome.state["cover_sent"] is True

    def test_no_cover_no_cover_url(self, exe, video):
        graph = graph_ok()
        pub = InstagramPublisher(client=graph.client(), sleep=lambda s: None, poll_interval=0,
                                 media_provider=lambda: provider(exe))
        outcome = pub.publish(ctx(video, {}), Progress())
        body = graph.calls("POST", f"{IG}/17841400000/media")[0].content.decode()
        assert "cover_url" not in body and outcome.cover_status is None

    def test_provider_that_cannot_deliver_cover_fails_the_job(self, video, cover):
        from src.media_storage.provider import MediaHandle

        class NoCover:
            def prepare(self, path, content_type, cover_path=None):
                return MediaHandle(Path(path), content_type, public_url="https://h.example/v.mp4")

            def get_public_url(self, handle):
                return handle.public_url

            def cleanup(self, handle):
                pass

        pub = InstagramPublisher(client=graph_ok().client(), sleep=lambda s: None, poll_interval=0,
                                 media_provider=NoCover)
        with pytest.raises(PublishError, match="did not deliver the cover"):
            pub.publish(ctx(video, {"cover_path": str(cover)}), Progress())


# ---------------------------------------------------------------------------------------------
# E. Account multi-select
# ---------------------------------------------------------------------------------------------

def fake_accounts(n):
    return [SimpleNamespace(platform="instagram", display_name=None, username=f"account{i:02d}") for i in range(1, n + 1)]


def choose(accounts, answers):
    from src.cli.publish_menu import select_accounts

    with patch("builtins.input", side_effect=answers):
        return [a.username for a in select_accounts(accounts)]


class TestSelection:
    def test_one_and_many(self):
        accounts = fake_accounts(5)
        assert choose(accounts, ["2", ""]) == ["account02"]
        assert choose(accounts, ["1,3", "5", ""]) == ["account01", "account03", "account05"]
        assert choose(accounts, ["2-4", ""]) == ["account02", "account03", "account04"]

    def test_all_none_toggle(self):
        accounts = fake_accounts(5)
        assert len(choose(accounts, ["a", ""])) == 5
        assert choose(accounts, ["a", "n", ""]) == []
        assert choose(accounts, ["a", "2", ""]) == ["account01", "account03", "account04", "account05"]
        assert choose(accounts, ["1", "1", ""]) == []  # toggled off again: never selected twice

    def test_more_than_fifty(self, capsys):
        accounts = fake_accounts(73)
        assert len(choose(accounts, ["A", ""])) == 73
        assert "Selected: 73 account(s)" in capsys.readouterr().out

    def test_invalid_input(self, capsys):
        assert choose(fake_accounts(3), ["9", "x", ""]) == []
        assert "numbers between 1 and 3" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------
# F/G/H/J/L. Fan-out engine
# ---------------------------------------------------------------------------------------------

class ProbeInstagram(PlatformPublisher):
    """Thread-safe fake Instagram: tracks concurrency; per-account scripted failures."""

    PLATFORM = "instagram"

    def __init__(self, fail_accounts=(), hold=0.03, until_active=None):
        super().__init__(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        self.fail_accounts, self.hold = set(fail_accounts), hold
        self.until_active = until_active  # hold each job until this many run at once (max 3 s): no timing flakes
        self.lock = threading.Lock()
        self.active = self.max_active = 0
        self.events, self.calls = [], []

    def validate(self, caption, options, media):
        return []

    def publish(self, ctx, on_progress):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.events.append(("start", ctx.platform_account_id))
            self.calls.append(ctx)
        try:
            on_progress("processing", {"container_id": f"C-{ctx.platform_account_id}"})
            time.sleep(self.hold)
            deadline = time.monotonic() + 3
            while self.until_active and self.max_active < self.until_active and time.monotonic() < deadline:
                time.sleep(0.01)
            if ctx.platform_account_id in self.fail_accounts:
                raise PublishError("Instagram said no", code="permission_denied")
            media_id = f"M-{ctx.platform_account_id}"
            return PublishOutcome("published", media_id,
                                  {"container_id": "C", "permalink": f"https://www.instagram.com/reel/{media_id}/"})
        finally:
            with self.lock:
                self.active -= 1
                self.events.append(("end", ctx.platform_account_id))

    def published_url(self, platform_media_id, state):
        return state.get("permalink")


def instagram_batch(services, n, cover=None):
    db, accounts, _, video = services
    future = datetime.now(timezone.utc) + timedelta(days=30)
    ids = [accounts.create_account(platform="instagram", platform_account_id=f"ig{i:03d}", username=f"account{i:02d}",
                                   access_token="T", expires_at=future).id for i in range(1, n + 1)]
    options = {"cover_path": str(cover)} if cover else {}
    return JobStore(db).create_post(video, "caption", [(i, dict(options)) for i in ids]), ids


def make_engine(services, publisher, limit=None, links=None):
    db, accounts, _, _ = services
    return PublisherEngine(db, accounts, publishers={"instagram": publisher}, sleep=lambda s: None,
                           probe_media=False, max_concurrent=limit, links=links)


class TestFanOut:
    def test_fifty_accounts_fifty_jobs_same_video_cover_caption(self, env, cover, tmp_path):
        post_id, ids = instagram_batch(env, 50, cover)
        probe = ProbeInstagram(hold=0.005)
        links = PublishedLinks(tmp_path / "links")
        result = make_engine(env, probe, links=links).publish_post(post_id)
        assert len(result.jobs) == 50 and result.count("published") == 50 and result.status == "completed"
        assert probe.max_active <= 5
        assert {c.options["cover_path"] for c in probe.calls} == {str(cover)}  # ONE cover file for all
        assert {c.video_path for c in probe.calls} == {env[3]} and {c.caption for c in probe.calls} == {"caption"}
        records = links.records("instagram")
        assert len(records) == 50 and len({r["account"] for r in records}) == 50  # lock: no lost writes
        assert not any("trycloudflare" in r["url"] or "tempfile" in r["url"] for r in records)

    def test_duplicate_rules(self, env):
        db, accounts, _, video = env
        post_id, ids = instagram_batch(env, 3)
        make_engine(env, ProbeInstagram(hold=0)).publish_post(post_id)
        store = JobStore(db)
        assert all(store.already_published(video, i) for i in ids)  # same video, other accounts: allowed
        with pytest.raises(JobError, match="only be selected once"):
            store.create_post(video, "c", [(ids[0], {}), (ids[0], {})])

    @pytest.mark.parametrize("limit", [1, 2, 5, 10])
    def test_never_more_than_the_limit(self, env, limit):
        post_id, _ = instagram_batch(env, 12)
        probe = ProbeInstagram(hold=0.05, until_active=min(limit, 12))
        result = make_engine(env, probe, limit=limit).publish_post(post_id)
        assert result.count("published") == 12
        assert probe.max_active == min(limit, 12)

    def test_next_job_starts_only_after_one_finishes(self, env):
        post_id, _ = instagram_batch(env, 4)
        probe = ProbeInstagram(hold=0.05)
        make_engine(env, probe, limit=2).publish_post(post_id)
        kinds = [k for k, _ in probe.events]
        third_start = [i for i, k in enumerate(kinds) if k == "start"][2]
        assert "end" in kinds[:third_start]

    def test_default_limit_is_five(self, monkeypatch):
        monkeypatch.delenv("INSTAGRAM_MAX_CONCURRENT_PUBLISHES", raising=False)
        assert max_concurrent_publishes() == 5
        for value, expected in (("3", 3), ("20", 20), ("0", 5), ("50", 5), ("x", 5)):
            monkeypatch.setenv("INSTAGRAM_MAX_CONCURRENT_PUBLISHES", value)
            assert max_concurrent_publishes() == expected

    def test_account_three_fails_others_continue(self, env, tmp_path):
        post_id, ids = instagram_batch(env, 5)
        links = PublishedLinks(tmp_path / "links")
        result = make_engine(env, ProbeInstagram(fail_accounts={"ig003"}), links=links).publish_post(post_id)
        by_account = {j.account_label: j.status for j in result.jobs}
        assert by_account == {"account01": "published", "account02": "published", "account03": "failed",
                              "account04": "published", "account05": "published"}
        assert result.status == "completed_with_failures"
        assert len(links.records("instagram")) == 4 and "account03" not in {r["account"] for r in links.records("instagram")}

    def test_transient_errors_retry_inside_the_limit(self, env):
        post_id, _ = instagram_batch(env, 6)
        probe = ProbeInstagram(hold=0.01)
        original = probe.publish
        seen = set()

        def flaky(ctx, on_progress):
            if ctx.platform_account_id not in seen:
                seen.add(ctx.platform_account_id)
                with probe.lock:
                    probe.active += 1
                    probe.max_active = max(probe.max_active, probe.active)
                    probe.active -= 1
                raise PublishError("HTTP 503", code="server_error", retryable=True)
            return original(ctx, on_progress)

        probe.publish = flaky
        result = make_engine(env, probe, limit=2).publish_post(post_id)
        assert result.count("published") == 6 and probe.max_active <= 2

    def test_unexpected_crash_in_one_job_is_isolated(self, env):
        post_id, _ = instagram_batch(env, 3)
        probe = ProbeInstagram(hold=0)
        original = probe.publish

        def crash_second(ctx, on_progress):
            if ctx.platform_account_id == "ig002":
                raise RuntimeError("bug")
            return original(ctx, on_progress)

        probe.publish = crash_second
        result = make_engine(env, probe).publish_post(post_id)
        assert sorted(j.status for j in result.jobs) == ["failed", "published", "published"]


class TestResume:
    def test_published_and_failed_never_rerun_pending_and_retrying_resume(self, env):
        db = env[0]
        post_id, ids = instagram_batch(env, 5)
        store = JobStore(db)
        jobs = store.jobs_for_post(post_id)
        store.claim(jobs[0].id)
        store.transition(jobs[0].id, "published", platform_media_id="OLD")
        store.claim(jobs[1].id)
        store.transition(jobs[1].id, "failed", error_message="x")
        store.claim(jobs[2].id)
        store.transition(jobs[2].id, "failed", error_message="503")
        store.transition(jobs[2].id, "retrying")
        store.claim(jobs[3].id)  # crashed while processing a known container
        attempt = store.start_attempt(jobs[3].id)
        store.update_attempt(attempt, "processing", provider_state={"container_id": "C-KEEP"})
        store.transition(jobs[3].id, "processing")
        probe = ProbeInstagram(hold=0)
        results = make_engine(env, probe).resume_open_jobs()
        called = {c.platform_account_id for c in probe.calls}
        assert called == {"ig003", "ig004", "ig005"}  # retrying, processing, pending
        assert next(c for c in probe.calls if c.platform_account_id == "ig004").state == {"container_id": "C-KEEP"}
        final = {j.id: j for j in store.jobs_for_post(post_id)}
        assert final[jobs[0].id].platform_media_id == "OLD"  # untouched, not republished
        assert final[jobs[1].id].status == "failed"
        assert results[0].status == "completed_with_failures"

    def test_resume_of_finished_batch_does_nothing(self, env):
        post_id, _ = instagram_batch(env, 3)
        make_engine(env, ProbeInstagram(hold=0)).publish_post(post_id)
        again = ProbeInstagram(hold=0)
        make_engine(env, again).publish_post(post_id)
        assert again.calls == []


def test_batch_status():
    assert batch_status([]) == "pending"
    assert batch_status(["pending", "pending"]) == "pending"
    assert batch_status(["published", "pending"]) == "running"
    assert batch_status(["published", "processing"]) == "running"
    assert batch_status(["published", "published"]) == "completed"
    assert batch_status(["failed", "failed"]) == "failed"
    assert batch_status(["published", "failed"]) == "completed_with_failures"


# ---------------------------------------------------------------------------------------------
# K. Planning / dry-run: nothing starts, nothing is created
# ---------------------------------------------------------------------------------------------

def test_plan_for_fifty_accounts_with_cover_starts_nothing(env, exe, monkeypatch, cover, tmp_path):
    monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
    db, accounts, _, _ = env
    future = datetime.now(timezone.utc) + timedelta(days=30)
    ids = [accounts.create_account(platform="instagram", platform_account_id=f"p{i}", username=f"a{i}",
                                   access_token="T", expires_at=future).id for i in range(50)]
    big = tmp_path / "big.mp4"
    with open(big, "wb") as f:
        f.truncate(131_925_281)
    no_network = httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network")))
    eng = PublisherEngine(db, accounts, publishers={"instagram": InstagramPublisher(client=no_network)}, probe_media=False)
    with patch("src.media_storage.cloudflare_tunnel.subprocess.Popen", side_effect=AssertionError("cloudflared")), \
            patch("src.media_storage.cloudflare_tunnel.http.server.ThreadingHTTPServer",
                  side_effect=AssertionError("server")):
        plan = eng.plan_destinations(str(big), "c", [(i, {"cover_path": str(cover)}) for i in ids])
    assert len(plan) == 50 and all(item.ready for item in plan)
    notes = " ".join(plan[0].notes)
    assert "Cloudflare Quick Tunnel" in notes and "cover served with the video" in notes and "cover.jpg" in notes
    assert eng.store.recent_jobs() == []


# ---------------------------------------------------------------------------------------------
# CLI: one cover, many accounts, one confirmation
# ---------------------------------------------------------------------------------------------

def test_create_post_one_cover_many_accounts_one_confirmation(env, exe, monkeypatch, cover, capsys):
    from src.cli.publish_menu import run_create_post

    monkeypatch.setenv("MEDIA_STORAGE_PROVIDER", "auto")
    db, accounts, _, video = env
    future = datetime.now(timezone.utc) + timedelta(days=30)
    for i in range(1, 4):
        accounts.create_account(platform="instagram", platform_account_id=f"x{i}", username=f"extra{i}",
                                access_token="T", expires_at=future)
    probe = ProbeInstagram(hold=0)
    real = InstagramPublisher()
    probe.validate = real.validate
    probe.delivery_notes = real.delivery_notes
    probe.cover_plan = real.cover_plan
    eng = make_engine(env, probe)
    instagram_numbers = [str(i) for i, a in enumerate(accounts.get_active_accounts(), 1) if a.platform == "instagram"]
    prompts = []
    answers = iter([video, str(cover), "Hello", ",".join(instagram_numbers), "", "y"])

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(answers)

    with patch("builtins.input", fake_input), patch("src.cli.publish_menu.clear_screen"):
        run_create_post(accounts, eng)
    out = capsys.readouterr().out
    assert sum("Publish now?" in p for p in prompts) == 1 and len(prompts) == 6  # ONE confirmation
    assert "4 selected, 4 valid, 0 invalid" in out and "at most 5 at a time" in out
    assert "cover.jpg (same cover for all selected accounts" in out and "AUTO -> Cloudflare Quick Tunnel" in out
    assert "outbound for Instagram" in out
    assert len(probe.calls) == 4 and {c.options["cover_path"] for c in probe.calls} == {str(cover.resolve())}
    assert "trycloudflare" not in out


def test_queue_shows_batches_without_urls(env, capsys):
    from src.cli.publish_menu import run_publishing_queue

    post_id, _ = instagram_batch(env, 5)
    make_engine(env, ProbeInstagram(fail_accounts={"ig004"}, hold=0)).publish_post(post_id)
    with patch("builtins.input", side_effect=["3"]), patch("src.cli.publish_menu.clear_screen"):
        run_publishing_queue(make_engine(env, ProbeInstagram()))
    out = capsys.readouterr().out
    assert f"BATCH #{post_id}  COMPLETED_WITH_FAILURES" in out
    assert "Published: 4   Running: 0   Pending: 0   Failed: 1" in out
    assert "✓ job" in out and "✗ job" in out and "account04" in out
    assert "http" not in out


def test_ctrl_c_leaves_queued_jobs_pending(env):
    post_id, _ = instagram_batch(env, 6)
    probe = ProbeInstagram(hold=0.3)
    eng = make_engine(env, probe, limit=2)
    real_as_completed = __import__("concurrent.futures").futures.as_completed

    def interrupted(futures):
        time.sleep(0.05)  # workers have started the first 2 jobs
        raise KeyboardInterrupt
        yield from real_as_completed(futures)

    with patch("src.core.publisher.as_completed", interrupted), pytest.raises(KeyboardInterrupt):
        eng.publish_post(post_id)
    statuses = sorted(j.status for j in JobStore(env[0]).jobs_for_post(post_id))
    assert statuses == ["pending"] * 4 + ["published"] * 2  # active jobs finished; queued ones never started
    assert len(probe.calls) == 2
