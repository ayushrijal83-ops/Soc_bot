"""TikTok Direct Post via the Content Posting API (FILE_UPLOAD).

Verified against TikTok docs on 2026-09-25:

    POST open.tiktokapis.com/v2/post/publish/creator_info/query/   -> privacy_level_options, max duration, ...
    POST open.tiktokapis.com/v2/post/publish/video/init/            -> publish_id, upload_url (valid 1 hour)
    PUT  upload_url  (sequential chunks, Content-Range)             -> 206 per chunk, 201 when complete
    POST open.tiktokapis.com/v2/post/publish/status/fetch/          -> PROCESSING_UPLOAD | PUBLISH_COMPLETE | FAILED ...

Scope: video.publish. Unaudited clients can only post with privacy SELF_ONLY (private).
"""

from typing import Any

import httpx

from src.core.validation import MediaInfo
from src.platforms.base import (
    PlatformPublisher,
    ProgressCallback,
    PublishContext,
    PublishError,
    PublishOutcome,
)

PRIVACY_LEVELS = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")

MB = 1024 * 1024


def chunk_plan(video_size: int, chunk_size: int = 10 * MB) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per the media transfer guide.

    Files under 5 MB (and up to one chunk) go as a single chunk; otherwise count =
    floor(size / chunk_size) and the last chunk carries the remainder (< 2 chunks <= 128 MB).
    """
    if video_size <= chunk_size:
        return video_size, 1
    return chunk_size, video_size // chunk_size


class TikTokPublisher(PlatformPublisher):
    PLATFORM = "tiktok"
    API_URL = "https://open.tiktokapis.com/v2"
    # Status fetch is limited to 30 requests/minute per token.
    POLL_INTERVAL = 5.0
    POLL_ATTEMPTS = 60

    MAX_TITLE_UTF16 = 2200
    MAX_SIZE_BYTES = 4 * 1024 * MB
    MAX_DURATION = 10 * 60
    CHUNK_SIZE = 10 * MB

    # Direct Post only offers post_info.video_cover_timestamp_ms (a frame of the video).
    COVER_UNSUPPORTED_REASON = (
        "TikTok Direct Post has no cover image upload (only video_cover_timestamp_ms, a frame of the video); "
        "cover image skipped"
    )

    REQUIRED_SCOPES = (("video.publish",),)

    def supports_cover_timestamp(self) -> bool:
        return True

    def preflight(self, ctx: PublishContext) -> tuple[list[str], list[str]]:
        """Query creator_info (required before posting) and check the chosen options against it."""
        creator = self._post(ctx, "/post/publish/creator_info/query/", {}, "creator info query failed")
        return self._creator_checks(creator, ctx)

    @staticmethod
    def _creator_checks(creator: dict[str, Any], ctx: PublishContext) -> tuple[list[str], list[str]]:
        notes, errors = [], []
        options = creator.get("privacy_level_options")
        if not isinstance(options, list):
            return notes, ["TikTok creator info has no privacy_level_options (malformed response)"]
        name = creator.get("creator_nickname") or creator.get("creator_username") or "unknown"
        notes.append(f"creator: {name}" + (f" (@{creator['creator_username']})" if creator.get("creator_username") else ""))
        notes.append("allowed privacy: " + (", ".join(options) or "none"))
        privacy = ctx.options.get("privacy_level")
        if privacy not in options:
            errors.append(f"TikTok privacy_level {privacy} is not allowed for this creator (allowed: {', '.join(options) or 'none'})")
        else:
            notes.append(f"privacy: {privacy}")
        max_duration = creator.get("max_video_post_duration_sec")
        if max_duration:
            notes.append(f"max duration: {max_duration}s")
            if ctx.media.duration_seconds and ctx.media.duration_seconds > max_duration:
                errors.append(f"Video is longer than this creator's limit of {max_duration}s")
        for flag, label in (("comment_disabled", "comments"), ("duet_disabled", "duets"), ("stitch_disabled", "stitches")):
            if creator.get(flag):
                notes.append(f"{label} are disabled in this creator's settings")
        return notes, errors

    def validate(self, caption: str, options: dict[str, Any], media: MediaInfo) -> list[str]:
        errors = []
        privacy = options.get("privacy_level")
        if privacy not in PRIVACY_LEVELS:
            errors.append("TikTok needs a privacy_level chosen by the user: " + ", ".join(PRIVACY_LEVELS))
        if len(caption.encode("utf-16-le")) // 2 > self.MAX_TITLE_UTF16:
            errors.append(f"TikTok caption exceeds {self.MAX_TITLE_UTF16} UTF-16 characters")
        if media.size_bytes > self.MAX_SIZE_BYTES:
            errors.append("TikTok videos are limited to 4 GB")
        if media.duration_seconds is not None and media.duration_seconds > self.MAX_DURATION:
            errors.append("TikTok API uploads are limited to 10 minutes")
        return errors

    def publish(self, ctx: PublishContext, on_progress: ProgressCallback) -> PublishOutcome:
        state = dict(ctx.state)

        # Upload finished earlier: only the status is left to check (no second init).
        if state.get("publish_id") and state.get("upload_complete"):
            return self._poll(lambda: self._status(ctx, state))

        # No upload, or an interrupted one: an unfinished upload never publishes, so start fresh.
        creator = self._post(ctx, "/post/publish/creator_info/query/", {}, "creator info query failed")
        _, errors = self._creator_checks(creator, ctx)
        if errors:
            code = "privacy_level_option_mismatch" if "privacy_level" in errors[0] else "creator_constraint"
            raise PublishError("; ".join(errors), code=code)
        privacy = ctx.options.get("privacy_level")

        size = ctx.media.size_bytes
        chunk_size, chunk_count = chunk_plan(size, self.CHUNK_SIZE)
        post_info = {"title": ctx.caption, "privacy_level": privacy}
        for flag in ("disable_comment", "disable_duet", "disable_stitch", "is_aigc"):
            if flag in ctx.options:
                post_info[flag] = bool(ctx.options[flag])
        init = self._post(ctx, "/post/publish/video/init/", {
            "post_info": post_info,
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunk_count,
            },
        }, "post init failed")
        publish_id, upload_url = init.get("publish_id"), init.get("upload_url")
        if not publish_id or not str(upload_url).startswith("https://"):
            raise PublishError("TikTok init returned no publish_id or no https upload_url", code="malformed_response")

        # upload_url is a bearer-like secret: kept in memory only, never persisted.
        state = {"publish_id": publish_id}
        on_progress("uploading", state)
        self._upload(ctx, upload_url, size, chunk_size, chunk_count)
        state["upload_complete"] = True
        on_progress("processing", state)
        return self._poll(lambda: self._status(ctx, state))

    def _upload(self, ctx: PublishContext, upload_url: str, size: int, chunk_size: int, count: int) -> None:
        with open(ctx.video_path, "rb") as f:
            for index in range(count):
                start = index * chunk_size
                end = size - 1 if index == count - 1 else start + chunk_size - 1
                f.seek(start)
                body = f.read(end - start + 1)
                response = self._request("PUT", upload_url, content=body, headers={
                    "Content-Type": ctx.media.mime_type,
                    "Content-Range": f"bytes {start}-{end}/{size}",
                })
                expected = 201 if index == count - 1 else 206
                if response.status_code not in (expected, 201):
                    raise PublishError(
                        f"TikTok upload of chunk {index + 1}/{count} failed (HTTP {response.status_code})",
                        code="upload_failed",
                        http_status=response.status_code,
                        retryable=response.status_code >= 500 or response.status_code == 403,  # 403 = URL expired
                    )

    def _status(self, ctx: PublishContext, state: dict[str, Any]) -> PublishOutcome:
        data = self._post(ctx, "/post/publish/status/fetch/", {"publish_id": state["publish_id"]},
                          "status fetch failed")
        status = data.get("status")
        if status == "PUBLISH_COMPLETE":
            post_ids = data.get("publicaly_available_post_id") or []  # (sic) field name per TikTok docs
            media_id = str(post_ids[0]) if post_ids else state["publish_id"]
            return PublishOutcome("published", media_id, state)
        if status == "FAILED":
            raise PublishError(f"TikTok rejected the post: {data.get('fail_reason') or 'unknown reason'}",
                               code="publish_failed")
        if status in ("PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD", "SEND_TO_USER_INBOX"):
            return PublishOutcome("processing", None, state)
        raise PublishError(f"Unexpected TikTok post status {status!r}", code="malformed_response", retryable=True)

    def _post(self, ctx: PublishContext, path: str, body: dict, what: str) -> dict[str, Any]:
        response = self._request("POST", f"{self.API_URL}{path}", json=body, headers={
            "Authorization": f"Bearer {ctx.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        })
        return self._unwrap(response, what)

    def _unwrap(self, response: httpx.Response, what: str) -> dict[str, Any]:
        payload = self._json(response)
        error = payload.get("error") or {}
        err_code = error.get("code", "ok" if response.status_code == 200 else "unknown")
        if response.status_code == 200 and err_code == "ok":
            data = payload.get("data")
            if not isinstance(data, dict):
                raise PublishError(f"TikTok {what}: response has no data", code="malformed_response")
            return data
        retryable = err_code in ("rate_limit_exceeded", "internal_error") or response.status_code >= 500
        code = {
            "access_token_invalid": "unauthorized",
            "scope_not_authorized": "insufficient_scope",
        }.get(err_code, err_code)
        log_id = f", log_id {error['log_id']}" if error.get("log_id") else ""
        raise PublishError(
            f"TikTok {what}: {err_code} - {error.get('message') or ''} (HTTP {response.status_code}{log_id})",
            code=code, retryable=retryable, http_status=response.status_code,
        )
