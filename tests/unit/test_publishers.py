"""Platform publisher adapter tests. All HTTP goes through httpx.MockTransport — no real provider calls."""

import json

import httpx
import pytest

from src.core.validation import MediaInfo
from src.platforms.base import PublishContext, PublishError, redact
from src.platforms.instagram.publisher import InstagramPublisher
from src.platforms.tiktok.publisher import TikTokPublisher, chunk_plan
from src.platforms.youtube.publisher import YouTubePublisher

TOKEN = "SECRET_ACCESS_TOKEN_123"
MB = 1024 * 1024


class Provider:
    """Scripted fake provider: routes (method, url-prefix) to a list of responses consumed in order."""

    def __init__(self):
        self.routes: dict[tuple[str, str], list] = {}
        self.requests: list[httpx.Request] = []

    def add(self, method: str, url: str, *responses):
        self.routes.setdefault((method, url), []).extend(responses)
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        for (method, prefix), queue in self.routes.items():
            if request.method == method and url.startswith(prefix) and queue:
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(item, Exception):
                    raise item
                return item(request) if callable(item) else item
        raise AssertionError(f"unexpected request {request.method} {url}")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def calls(self, method: str, url: str, prefix: bool = False) -> list[httpx.Request]:
        def match(r):
            base = str(r.url).split("?")[0]
            return base.startswith(url) if prefix else base == url
        return [r for r in self.requests if r.method == method and match(r)]


def resp(status: int, body=None, headers=None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers) if body is not None else httpx.Response(status, headers=headers)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x" * 1000)
    return path


def ctx(video, options=None, state=None, caption="Hello #test", size=None, account="17841400000"):
    return PublishContext(
        job_id=1,
        video_path=str(video),
        caption=caption,
        options=options or {},
        access_token=TOKEN,
        platform_account_id=account,
        media=MediaInfo(str(video), size or video.stat().st_size, "video/mp4"),
        state=state or {},
    )


def no_sleep(_):
    pass


class Progress:
    def __init__(self):
        self.events = []

    def __call__(self, status, state):
        self.events.append((status, dict(state)))


def assert_no_token_in_urls(provider: Provider):
    for r in provider.requests:
        assert TOKEN not in str(r.url)


# ---------------------------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------------------------

IG = "https://graph.instagram.com/v25.0"
IG_OPTS = {"video_url": "https://cdn.example.com/clip.mp4"}


def ig(provider: Provider, **kw) -> InstagramPublisher:
    return InstagramPublisher(client=provider.client(), sleep=no_sleep, poll_interval=0, **kw)


class TestInstagramPublisher:
    def test_full_flow_container_poll_publish(self, video):
        p = (Provider()
             .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "MEDIA_1"}))
             .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "CONT_1"}))
             .add("GET", f"{IG}/CONT_1", resp(200, {"status_code": "IN_PROGRESS"}), resp(200, {"status_code": "FINISHED"})))
        progress = Progress()
        outcome = ig(p).publish(ctx(video, IG_OPTS), progress)

        assert outcome.status == "published"
        assert outcome.platform_media_id == "MEDIA_1"
        create = p.calls("POST", f"{IG}/17841400000/media")[0]
        form = dict(x.split("=", 1) for x in create.content.decode().split("&"))
        assert form["media_type"] == "REELS"
        assert "video_url" in form and "caption" in form
        assert create.headers["Authorization"] == f"Bearer {TOKEN}"
        publish = p.calls("POST", f"{IG}/17841400000/media_publish")[0]
        assert publish.content.decode() == "creation_id=CONT_1"
        # container id persisted before polling
        assert progress.events[0] == ("processing", {"container_id": "CONT_1"})
        assert_no_token_in_urls(p)

    def test_processing_timeout_stays_processing_without_publishing(self, video):
        p = (Provider()
             .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "CONT_1"}))
             .add("GET", f"{IG}/CONT_1", resp(200, {"status_code": "IN_PROGRESS"})))
        outcome = ig(p, poll_attempts=3).publish(ctx(video, IG_OPTS), Progress())
        assert outcome.status == "processing"
        assert outcome.state == {"container_id": "CONT_1"}
        assert len(p.calls("GET", f"{IG}/CONT_1")) == 3
        assert p.calls("POST", f"{IG}/17841400000/media_publish") == []

    def test_processing_error_fails_non_retryable(self, video):
        p = (Provider()
             .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "CONT_1"}))
             .add("GET", f"{IG}/CONT_1", resp(200, {"status_code": "ERROR"})))
        with pytest.raises(PublishError) as e:
            ig(p).publish(ctx(video, IG_OPTS), Progress())
        assert e.value.code == "processing_failed"
        assert not e.value.retryable

    def test_resume_with_published_container_does_not_publish_again(self, video):
        p = Provider().add("GET", f"{IG}/CONT_1", resp(200, {"status_code": "PUBLISHED"}))
        outcome = ig(p).publish(ctx(video, IG_OPTS, state={"container_id": "CONT_1", "publish_requested": True}), Progress())
        assert outcome.status == "published"
        assert p.calls("POST", f"{IG}/17841400000/media") == []
        assert p.calls("POST", f"{IG}/17841400000/media_publish") == []

    def test_resume_finished_container_publishes_without_new_container(self, video):
        p = (Provider()
             .add("GET", f"{IG}/CONT_1", resp(200, {"status_code": "FINISHED"}))
             .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "MEDIA_9"})))
        outcome = ig(p).publish(ctx(video, IG_OPTS, state={"container_id": "CONT_1"}), Progress())
        assert outcome.platform_media_id == "MEDIA_9"
        assert p.calls("POST", f"{IG}/17841400000/media") == []

    def test_expired_container_is_replaced(self, video):
        p = (Provider()
             .add("GET", f"{IG}/OLD", resp(200, {"status_code": "EXPIRED"}))
             .add("POST", f"{IG}/17841400000/media_publish", resp(200, {"id": "MEDIA_2"}))
             .add("POST", f"{IG}/17841400000/media", resp(200, {"id": "NEW"}))
             .add("GET", f"{IG}/NEW", resp(200, {"status_code": "FINISHED"})))
        outcome = ig(p).publish(ctx(video, IG_OPTS, state={"container_id": "OLD"}), Progress())
        assert outcome.state["container_id"] == "NEW"

    @pytest.mark.parametrize("status,body,code,retryable", [
        (400, {"error": {"message": "Invalid OAuth access token", "code": 190}}, "unauthorized", False),
        (403, {"error": {"message": "no permission", "code": 10}}, "permission_denied", False),
        (500, {"error": {"message": "oops", "code": 2, "is_transient": True}}, "provider_error", True),
        (400, {"error": {"message": "limit", "code": 4}}, "rate_limited", True),
    ])
    def test_error_mapping(self, video, status, body, code, retryable):
        p = Provider().add("POST", f"{IG}/17841400000/media", resp(status, body))
        with pytest.raises(PublishError) as e:
            ig(p).publish(ctx(video, IG_OPTS), Progress())
        assert e.value.code == code
        assert e.value.retryable is retryable
        assert TOKEN not in str(e.value)

    def test_malformed_response(self, video):
        p = Provider().add("POST", f"{IG}/17841400000/media", httpx.Response(200, content=b"<html>"))
        with pytest.raises(PublishError) as e:
            ig(p).publish(ctx(video, IG_OPTS), Progress())
        assert e.value.code == "malformed_response"

    def test_timeout_is_retryable(self, video):
        p = Provider().add("POST", f"{IG}/17841400000/media", httpx.ReadTimeout("slow"))
        with pytest.raises(PublishError) as e:
            ig(p).publish(ctx(video, IG_OPTS), Progress())
        assert e.value.code == "timeout" and e.value.retryable

    def test_validation(self, video):
        pub = InstagramPublisher(client=Provider().client())
        media = MediaInfo(str(video), 1000, "video/mp4")
        assert pub.validate("ok", IG_OPTS, media) == []
        assert any("https" in e for e in pub.validate("ok", {}, media))
        assert any("https" in e for e in pub.validate("ok", {"video_url": "file:///C:/x.mp4"}, media))
        assert any("2200" in e for e in pub.validate("x" * 2201, IG_OPTS, media))
        assert any("hashtags" in e for e in pub.validate("#a " * 31, IG_OPTS, media))
        assert any("300 MB" in e for e in pub.validate("ok", IG_OPTS, MediaInfo("v", 301 * MB, "video/mp4")))
        assert any("MP4 or MOV" in e for e in pub.validate("ok", IG_OPTS, MediaInfo("v", 1, "video/webm")))
        long = MediaInfo("v", 1, "video/mp4", duration_seconds=16 * 60)
        assert any("15 minutes" in e for e in pub.validate("ok", IG_OPTS, long))
        unknown = MediaInfo("v", 1, "video/mp4")  # unknown duration is not rejected
        assert pub.validate("ok", IG_OPTS, unknown) == []

    def test_poll_defaults_follow_meta_guidance(self):
        assert InstagramPublisher.POLL_INTERVAL == 60
        assert InstagramPublisher.POLL_ATTEMPTS == 5


# ---------------------------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------------------------

TT = "https://open.tiktokapis.com/v2"
UPLOAD = "https://open-upload.tiktokapis.com/upload/?upload_id=1&upload_token=SECRETUPLOAD"
TT_OPTS = {"privacy_level": "SELF_ONLY"}


def tt_ok(data):
    return resp(200, {"data": data, "error": {"code": "ok", "message": "", "log_id": "L1"}})


def tt_err(status, code, message="bad"):
    return resp(status, {"data": {}, "error": {"code": code, "message": message, "log_id": "L2"}})


CREATOR = tt_ok({"creator_nickname": "me", "privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"],
                 "max_video_post_duration_sec": 600})


def tiktok(provider, **kw):
    return TikTokPublisher(client=provider.client(), sleep=no_sleep, poll_interval=0, **kw)


class TestTikTokPublisher:
    def full_provider(self, *status):
        return (Provider()
                .add("POST", f"{TT}/post/publish/creator_info/query/", CREATOR)
                .add("POST", f"{TT}/post/publish/video/init/", tt_ok({"publish_id": "v_pub_1", "upload_url": UPLOAD}))
                .add("PUT", "https://open-upload.tiktokapis.com/upload/", resp(201))
                .add("POST", f"{TT}/post/publish/status/fetch/", *status))

    def test_full_flow(self, video):
        p = self.full_provider(tt_ok({"status": "PROCESSING_UPLOAD"}),
                               tt_ok({"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7300000001]}))
        progress = Progress()
        outcome = tiktok(p).publish(ctx(video, TT_OPTS, caption="My video #fyp"), progress)

        assert outcome.status == "published"
        assert outcome.platform_media_id == "7300000001"
        init = json.loads(p.calls("POST", f"{TT}/post/publish/video/init/")[0].content)
        assert init["post_info"] == {"title": "My video #fyp", "privacy_level": "SELF_ONLY"}
        assert init["source_info"] == {"source": "FILE_UPLOAD", "video_size": 1000, "chunk_size": 1000,
                                       "total_chunk_count": 1}
        put = p.calls("PUT", "https://open-upload.tiktokapis.com/upload/")[0]
        assert put.headers["Content-Range"] == "bytes 0-999/1000"
        assert put.headers["Content-Type"] == "video/mp4"
        assert len(put.content) == 1000
        # publish_id persisted before upload; upload_url never persisted
        assert progress.events[0] == ("uploading", {"publish_id": "v_pub_1"})
        assert progress.events[1] == ("processing", {"publish_id": "v_pub_1", "upload_complete": True})
        assert all("SECRETUPLOAD" not in json.dumps(e) for e in progress.events)
        for r in p.calls("POST", TT, prefix=True):
            assert r.headers["Authorization"] == f"Bearer {TOKEN}"

    def test_private_post_without_public_id_uses_publish_id(self, video):
        p = self.full_provider(tt_ok({"status": "PUBLISH_COMPLETE"}))
        assert tiktok(p).publish(ctx(video, TT_OPTS), Progress()).platform_media_id == "v_pub_1"

    def test_chunked_upload_sequential_ranges(self, tmp_path):
        big = tmp_path / "big.mp4"
        size = 25 * MB + 123
        with open(big, "wb") as f:
            f.truncate(size)
        p = self.full_provider(tt_ok({"status": "PUBLISH_COMPLETE"}))
        p.routes[("PUT", "https://open-upload.tiktokapis.com/upload/")] = [resp(206), resp(201)]
        tiktok(p).publish(ctx(big, TT_OPTS, size=size), Progress())
        init = json.loads(p.calls("POST", f"{TT}/post/publish/video/init/")[0].content)
        assert init["source_info"]["chunk_size"] == 10 * MB
        assert init["source_info"]["total_chunk_count"] == 2
        ranges = [r.headers["Content-Range"] for r in p.calls("PUT", "https://open-upload", prefix=True)]
        assert ranges == [f"bytes 0-{10 * MB - 1}/{size}", f"bytes {10 * MB}-{size - 1}/{size}"]

    def test_chunk_plan_rules(self):
        assert chunk_plan(4 * MB) == (4 * MB, 1)
        assert chunk_plan(7 * MB) == (7 * MB, 1)
        assert chunk_plan(35 * MB) == (10 * MB, 3)  # floor; last chunk carries remainder (15 MB)
        size, count = 4 * 1024 * MB, chunk_plan(4 * 1024 * MB)[1]
        assert count <= 1000 and size - (count - 1) * 10 * MB <= 128 * MB

    def test_rejected_content(self, video):
        p = self.full_provider(tt_ok({"status": "FAILED", "fail_reason": "spam_risk"}))
        with pytest.raises(PublishError) as e:
            tiktok(p).publish(ctx(video, TT_OPTS), Progress())
        assert e.value.code == "publish_failed" and not e.value.retryable
        assert "spam_risk" in str(e.value)

    @pytest.mark.parametrize("status,code,expected,retryable", [
        (401, "access_token_invalid", "unauthorized", False),
        (401, "scope_not_authorized", "insufficient_scope", False),
        (403, "spam_risk_too_many_posts", "spam_risk_too_many_posts", False),
        (429, "rate_limit_exceeded", "rate_limit_exceeded", True),
        (500, "internal_error", "internal_error", True),
    ])
    def test_provider_errors(self, video, status, code, expected, retryable):
        p = Provider().add("POST", f"{TT}/post/publish/creator_info/query/", tt_err(status, code))
        with pytest.raises(PublishError) as e:
            tiktok(p).publish(ctx(video, TT_OPTS), Progress())
        assert e.value.code == expected
        assert e.value.retryable is retryable
        assert TOKEN not in str(e.value)

    def test_privacy_not_allowed_by_creator(self, video):
        p = Provider().add("POST", f"{TT}/post/publish/creator_info/query/", CREATOR)
        with pytest.raises(PublishError) as e:
            tiktok(p).publish(ctx(video, {"privacy_level": "FOLLOWER_OF_CREATOR"}), Progress())
        assert e.value.code == "privacy_level_option_mismatch"
        assert p.calls("POST", f"{TT}/post/publish/video/init/") == []

    def test_upload_failure(self, video):
        p = self.full_provider(tt_ok({"status": "PUBLISH_COMPLETE"}))
        p.routes[("PUT", "https://open-upload.tiktokapis.com/upload/")] = [resp(400)]
        with pytest.raises(PublishError) as e:
            tiktok(p).publish(ctx(video, TT_OPTS), Progress())
        assert e.value.code == "upload_failed" and not e.value.retryable
        assert "SECRETUPLOAD" not in str(e.value)

    def test_resume_after_upload_only_polls(self, video):
        p = Provider().add("POST", f"{TT}/post/publish/status/fetch/", tt_ok({"status": "PUBLISH_COMPLETE"}))
        outcome = tiktok(p).publish(ctx(video, TT_OPTS, state={"publish_id": "v1", "upload_complete": True}), Progress())
        assert outcome.status == "published"
        assert p.calls("POST", f"{TT}/post/publish/video/init/") == []

    def test_malformed_response(self, video):
        p = Provider().add("POST", f"{TT}/post/publish/creator_info/query/", httpx.Response(502, content=b"bad gateway"))
        with pytest.raises(PublishError) as e:
            tiktok(p).publish(ctx(video, TT_OPTS), Progress())
        assert e.value.code == "malformed_response" and e.value.retryable

    def test_validation(self, video):
        pub = TikTokPublisher(client=Provider().client())
        media = MediaInfo(str(video), 1000, "video/mp4")
        assert pub.validate("ok", TT_OPTS, media) == []
        assert any("privacy_level" in e for e in pub.validate("ok", {}, media))
        assert any("2200" in e for e in pub.validate("😀" * 1101, TT_OPTS, media))  # 2202 UTF-16 units
        assert any("10 minutes" in e for e in pub.validate("ok", TT_OPTS, MediaInfo("v", 1, "video/mp4", 601)))


# ---------------------------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------------------------

YT_UP = "https://www.googleapis.com/upload/youtube/v3/videos"
YT_V = "https://www.googleapis.com/youtube/v3/videos"
SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=SESS"
YT_OPTS = {"title": "My video", "privacy_status": "private"}


def youtube(provider, **kw):
    return YouTubePublisher(client=provider.client(), sleep=no_sleep, poll_interval=0, **kw)


def processed(status="processed", **extra):
    return resp(200, {"items": [{"id": "VID1", "status": {"uploadStatus": status, **extra}}]})


class TestYouTubePublisher:
    def test_full_flow(self, video):
        p = (Provider()
             .add("PUT", SESSION.split("?")[0], resp(201, {"id": "VID1"}))
             .add("POST", YT_UP, resp(200, headers={"Location": SESSION}))
             .add("GET", YT_V, processed("uploaded"), processed()))
        # both POST (init) and PUT go to the same base URL; route PUT first by method
        progress = Progress()
        outcome = youtube(p).publish(ctx(video, YT_OPTS, caption="desc"), progress)

        assert outcome.status == "published" and outcome.platform_media_id == "VID1"
        init = p.calls("POST", YT_UP)[0]
        assert init.url.params["uploadType"] == "resumable"
        assert init.url.params["part"] == "snippet,status"
        assert init.headers["X-Upload-Content-Length"] == "1000"
        assert init.headers["X-Upload-Content-Type"] == "video/mp4"
        body = json.loads(init.content)
        assert body == {"snippet": {"title": "My video", "description": "desc"}, "status": {"privacyStatus": "private"}}
        put = p.calls("PUT", YT_UP)[0]
        assert put.headers["Content-Range"] == "bytes 0-999/1000"
        assert progress.events[-1] == ("processing", {"video_id": "VID1"})
        assert_no_token_in_urls(p)

    def test_chunked_upload_follows_308_range(self, tmp_path):
        big = tmp_path / "big.mp4"
        size = 20 * MB
        with open(big, "wb") as f:
            f.truncate(size)
        chunk = YouTubePublisher.CHUNK_SIZE
        p = (Provider()
             .add("PUT", YT_UP, resp(308, headers={"Range": f"bytes=0-{chunk - 1}"}),
                  resp(308, headers={"Range": f"bytes=0-{2 * chunk - 1}"}), resp(200, {"id": "VID2"}))
             .add("POST", YT_UP, resp(200, headers={"Location": SESSION}))
             .add("GET", YT_V, processed()))
        youtube(p).publish(ctx(big, YT_OPTS, size=size), Progress())
        ranges = [r.headers["Content-Range"] for r in p.calls("PUT", YT_UP)]
        assert ranges == [f"bytes 0-{chunk - 1}/{size}", f"bytes {chunk}-{2 * chunk - 1}/{size}",
                          f"bytes {2 * chunk}-{size - 1}/{size}"]
        assert chunk % (256 * 1024) == 0
        assert max(len(r.content) for r in p.calls("PUT", YT_UP)) <= chunk  # never the whole file

    def test_network_error_resumes_from_server_offset(self, video):
        p = (Provider()
             .add("PUT", YT_UP, httpx.ConnectError("reset"), resp(308, headers={"Range": "bytes=0-499"}),
                  resp(201, {"id": "VID3"}))
             .add("POST", YT_UP, resp(200, headers={"Location": SESSION}))
             .add("GET", YT_V, processed()))
        outcome = youtube(p).publish(ctx(video, YT_OPTS), Progress())
        assert outcome.platform_media_id == "VID3"
        ranges = [r.headers["Content-Range"] for r in p.calls("PUT", YT_UP)]
        assert ranges == ["bytes 0-999/1000", "bytes */1000", "bytes 500-999/1000"]

    def test_final_chunk_lost_and_status_unknown_is_uncertain(self, video):
        p = (Provider()
             .add("PUT", YT_UP, httpx.ConnectError("reset"))
             .add("POST", YT_UP, resp(200, headers={"Location": SESSION})))
        with pytest.raises(PublishError) as e:
            youtube(p).publish(ctx(video, YT_OPTS), Progress())
        assert e.value.uncertain and not e.value.retryable

    def test_quota_error(self, video):
        body = {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
        p = Provider().add("POST", YT_UP, resp(403, body))
        with pytest.raises(PublishError) as e:
            youtube(p).publish(ctx(video, YT_OPTS), Progress())
        assert e.value.code == "quota_exceeded" and not e.value.retryable

    def test_unauthorized(self, video):
        body = {"error": {"code": 401, "message": "Invalid Credentials", "errors": [{"reason": "authError"}]}}
        p = Provider().add("POST", YT_UP, resp(401, body))
        with pytest.raises(PublishError) as e:
            youtube(p).publish(ctx(video, YT_OPTS), Progress())
        assert e.value.code == "unauthorized" and not e.value.retryable
        assert TOKEN not in str(e.value)

    def test_upload_rejected(self, video):
        p = (Provider()
             .add("PUT", YT_UP, resp(400, {"error": {"code": 400, "message": "bad", "errors": [{"reason": "invalidTitle"}]}}))
             .add("POST", YT_UP, resp(200, headers={"Location": SESSION})))
        with pytest.raises(PublishError) as e:
            youtube(p).publish(ctx(video, YT_OPTS), Progress())
        assert not e.value.retryable and e.value.http_status == 400

    def test_server_5xx_on_init_is_retryable(self, video):
        p = Provider().add("POST", YT_UP, resp(503, {"error": {"code": 503, "message": "backend"}}))
        with pytest.raises(PublishError) as e:
            youtube(p).publish(ctx(video, YT_OPTS), Progress())
        assert e.value.retryable

    def test_processing_failure(self, video):
        p = Provider().add("GET", YT_V, processed("rejected", rejectionReason="duplicate"))
        with pytest.raises(PublishError) as e:
            youtube(p).publish(ctx(video, YT_OPTS, state={"video_id": "VID1"}), Progress())
        assert e.value.code == "video_rejected" and "duplicate" in str(e.value)

    def test_resume_with_video_id_never_reuploads(self, video):
        p = Provider().add("GET", YT_V, processed("uploaded"))
        outcome = youtube(p, poll_attempts=2).publish(ctx(video, YT_OPTS, state={"video_id": "VID1"}), Progress())
        assert outcome.status == "processing"
        assert p.calls("POST", YT_UP) == [] and p.calls("PUT", YT_UP) == []

    def test_can_restart_only_with_video_id(self):
        pub = YouTubePublisher(client=Provider().client())
        assert pub.can_restart({}) is False
        assert pub.can_restart({"video_id": "V"}) is True

    def test_validation(self, video):
        pub = YouTubePublisher(client=Provider().client())
        media = MediaInfo(str(video), 1000, "video/mp4")
        assert pub.validate("desc", YT_OPTS, media) == []
        assert any("title" in e for e in pub.validate("d", {"privacy_status": "private"}, media))
        assert any("100" in e for e in pub.validate("d", {**YT_OPTS, "title": "t" * 101}, media))
        assert any("< or >" in e for e in pub.validate("a <b>", YT_OPTS, media))
        assert any("5000" in e for e in pub.validate("é" * 2501, YT_OPTS, media))
        assert any("privacy" in e for e in pub.validate("d", {"title": "t"}, media))


def test_redact_removes_secrets():
    text = (f"Authorization: Bearer {TOKEN} url?access_token={TOKEN}&x=1 "
            f'{{"refresh_token": "RT_SECRET"}} client_secret=CS code=AUTH_CODE error_code 190 OAuth abcdefghijkl')
    out = redact(text)
    for secret in (TOKEN, "RT_SECRET", "CS ", "AUTH_CODE", "abcdefghijkl"):
        assert secret not in out
    assert "error_code 190" in out


def test_youtube_rejects_foreign_session_uri(video):
    """Security: the bearer token must only be sent to Google's upload host."""
    p = Provider().add("POST", YT_UP, resp(200, headers={"Location": "https://evil.example.com/upload"}))
    with pytest.raises(PublishError) as e:
        youtube(p).publish(ctx(video, YT_OPTS), Progress())
    assert e.value.code == "malformed_response"
    assert p.calls("PUT", "https://evil.example.com/upload") == []


def test_tiktok_rejects_non_https_upload_url(video):
    p = (Provider()
         .add("POST", f"{TT}/post/publish/creator_info/query/", CREATOR)
         .add("POST", f"{TT}/post/publish/video/init/", tt_ok({"publish_id": "p", "upload_url": "http://x/up"})))
    with pytest.raises(PublishError):
        tiktok(p).publish(ctx(video, TT_OPTS), Progress())


def test_httpx_logging_cannot_leak_upload_urls():
    import logging

    import src.platforms.base  # noqa: F401 - import sets the level

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


# ---------------------------------------------------------------------------------------------
# YouTube custom thumbnail (thumbnails.set), Phase 5A
# ---------------------------------------------------------------------------------------------

YT_THUMB = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def yt_provider(*thumb_responses):
    p = (Provider()
         .add("PUT", YT_UP, resp(201, {"id": "VID1"}))
         .add("POST", YT_THUMB, *thumb_responses)
         .add("POST", YT_UP, resp(200, headers={"Location": SESSION}))
         .add("GET", YT_V, processed()))
    return p


class TestYouTubeThumbnail:
    def cover(self, tmp_path, name="cover.jpg", data=JPEG_BYTES):
        path = tmp_path / name
        path.write_bytes(data)
        return str(path)

    def test_thumbnail_uploaded_after_video(self, video, tmp_path):
        p = yt_provider(resp(200, {"kind": "youtube#thumbnailSetResponse", "items": [{}]}))
        opts = {**YT_OPTS, "cover_path": self.cover(tmp_path)}
        outcome = youtube(p).publish(ctx(video, opts), Progress())
        assert outcome.status == "published" and outcome.platform_media_id == "VID1"
        assert outcome.cover_status == "published" and outcome.cover_error is None
        thumb = p.calls("POST", YT_THUMB)[0]
        assert thumb.url.params["videoId"] == "VID1"
        assert thumb.headers["Content-Type"] == "image/jpeg"
        assert thumb.headers["Authorization"] == f"Bearer {TOKEN}"
        assert thumb.content == JPEG_BYTES
        order = [(r.method, str(r.url).split("?")[0]) for r in p.requests]
        assert order.index(("PUT", YT_UP)) < order.index(("POST", YT_THUMB))  # video first

    def test_thumbnail_failure_keeps_video_published(self, video, tmp_path):
        body = {"error": {"code": 403, "message": "The authenticated user doesn't have permissions",
                          "errors": [{"reason": "forbidden"}]}}
        p = yt_provider(resp(403, body))
        outcome = youtube(p).publish(ctx(video, {**YT_OPTS, "cover_path": self.cover(tmp_path)}), Progress())
        assert outcome.status == "published" and outcome.platform_media_id == "VID1"
        assert outcome.cover_status == "failed" and "403" in outcome.cover_error
        assert len(p.calls("PUT", YT_UP)) == 1  # no second video upload

    def test_thumbnail_network_error_is_reported_not_raised(self, video, tmp_path):
        p = yt_provider(httpx.ConnectError("reset"))
        outcome = youtube(p).publish(ctx(video, {**YT_OPTS, "cover_path": self.cover(tmp_path)}), Progress())
        assert outcome.status == "published" and outcome.cover_status == "failed"

    def test_resume_never_retries_thumbnail_or_video(self, video, tmp_path):
        p = Provider().add("GET", YT_V, processed())
        state = {"video_id": "VID1", "thumbnail": "failed", "thumbnail_error": "x"}
        outcome = youtube(p).publish(ctx(video, {**YT_OPTS, "cover_path": self.cover(tmp_path)}, state=state), Progress())
        assert outcome.cover_status == "failed"
        assert p.calls("POST", YT_THUMB) == [] and p.calls("PUT", YT_UP) == []

    def test_no_cover_publishes_normally(self, video):
        p = yt_provider()
        outcome = youtube(p).publish(ctx(video, YT_OPTS), Progress())
        assert outcome.status == "published" and outcome.cover_status is None
        assert p.calls("POST", YT_THUMB) == []

    def test_oversized_and_unsupported_covers_not_uploaded(self, video, tmp_path):
        big = tmp_path / "big.png"
        with open(big, "wb") as f:
            f.truncate(50 * 1024 * 1024 + 1)
        for cover, expected in ((str(big), "50 MB"), (self.cover(tmp_path, "c.webp", b"RIFF1234WEBP"), "JPEG or PNG")):
            p = yt_provider()
            outcome = youtube(p).publish(ctx(video, {**YT_OPTS, "cover_path": cover}), Progress())
            assert outcome.status == "published" and outcome.cover_status == "failed"
            assert expected in outcome.cover_error
            assert p.calls("POST", YT_THUMB) == []

    def test_thumbnail_state_persisted_via_progress(self, video, tmp_path):
        p = yt_provider(resp(200, {"items": [{}]}))
        progress = Progress()
        youtube(p).publish(ctx(video, {**YT_OPTS, "cover_path": self.cover(tmp_path)}), progress)
        assert progress.events[-1][1]["thumbnail"] == "published"


def test_tiktok_never_sends_a_cover_image(video):
    p = (Provider()
         .add("POST", f"{TT}/post/publish/creator_info/query/", CREATOR)
         .add("POST", f"{TT}/post/publish/video/init/", tt_ok({"publish_id": "p1", "upload_url": UPLOAD}))
         .add("PUT", "https://open-upload.tiktokapis.com/upload/", resp(201))
         .add("POST", f"{TT}/post/publish/status/fetch/", tt_ok({"status": "PUBLISH_COMPLETE"})))
    outcome = tiktok(p).publish(ctx(video, {**TT_OPTS, "cover_path": "C:/x/cover.jpg"}), Progress())
    assert outcome.status == "published" and outcome.cover_status is None
    init = json.loads(p.calls("POST", f"{TT}/post/publish/video/init/")[0].content)
    assert "video_cover_timestamp_ms" not in init["post_info"] and "cover" not in json.dumps(init)
