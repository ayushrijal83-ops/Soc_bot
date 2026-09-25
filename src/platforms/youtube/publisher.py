"""YouTube upload via the Data API v3 resumable upload protocol.

Verified against Google docs on 2026-09-25 (videos.insert, resumable upload guide, video resource):

    POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status
         X-Upload-Content-Length / X-Upload-Content-Type, JSON video resource  -> 200, Location: session URI
    PUT  session URI, Content-Range: bytes a-b/total (chunks, multiple of 256 KiB) -> 308 ... 200/201 + video
    PUT  session URI, Content-Range: bytes */total   (status query)              -> 308 Range: bytes=0-N, or 200/201
    GET  https://www.googleapis.com/youtube/v3/videos?part=status,processingDetails&id=...
         status.uploadStatus: uploaded | processed | failed | rejected | deleted

The video file is streamed chunk by chunk; it is never loaded into memory whole.
"""

from typing import Any
from urllib.parse import urlparse

import httpx

from src.core.validation import MediaInfo
from src.platforms.base import (
    PlatformPublisher,
    ProgressCallback,
    PublishContext,
    PublishError,
    PublishOutcome,
    http_error,
)

PRIVACY_STATUSES = ("private", "unlisted", "public")
QUOTA_REASONS = ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded", "rateLimitExceeded")


class YouTubePublisher(PlatformPublisher):
    PLATFORM = "youtube"
    UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
    VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
    # A crash after the last chunk may already have created the video, and a new upload would
    # duplicate it. Only restart when no upload could have completed.
    RESTART_SAFE = False
    POLL_INTERVAL = 15.0
    POLL_ATTEMPTS = 20  # videos.list costs 1 quota unit per call

    CHUNK_SIZE = 32 * 256 * 1024  # 8 MiB, a multiple of 256 KiB as required
    MAX_RESUMES = 3
    MAX_SIZE_BYTES = 256 * 1024 ** 3
    MAX_TITLE_CHARS = 100
    MAX_DESCRIPTION_BYTES = 5000

    def validate(self, caption: str, options: dict[str, Any], media: MediaInfo) -> list[str]:
        errors = []
        title = options.get("title") or ""
        if not title.strip():
            errors.append("YouTube needs a title")
        elif len(title) > self.MAX_TITLE_CHARS:
            errors.append(f"YouTube title exceeds {self.MAX_TITLE_CHARS} characters")
        if "<" in title or ">" in title:
            errors.append("YouTube title may not contain < or >")
        if len(caption.encode("utf-8")) > self.MAX_DESCRIPTION_BYTES:
            errors.append(f"YouTube description exceeds {self.MAX_DESCRIPTION_BYTES} bytes")
        if "<" in caption or ">" in caption:
            errors.append("YouTube description may not contain < or >")
        if options.get("privacy_status") not in PRIVACY_STATUSES:
            errors.append("YouTube privacy_status must be one of: " + ", ".join(PRIVACY_STATUSES))
        if media.size_bytes > self.MAX_SIZE_BYTES:
            errors.append("YouTube uploads are limited to 256 GB")
        return errors

    def can_restart(self, state: dict[str, Any]) -> bool:
        return bool(state.get("video_id"))

    def publish(self, ctx: PublishContext, on_progress: ProgressCallback) -> PublishOutcome:
        state = dict(ctx.state)
        if not state.get("video_id"):
            session_uri = self._start_session(ctx)
            on_progress("uploading", state)
            video_id = self._upload(ctx, session_uri)
            state["video_id"] = video_id
            on_progress("processing", state)
        return self._poll(lambda: self._processing(ctx, state))

    def _start_session(self, ctx: PublishContext) -> str:
        snippet = {"title": ctx.options["title"], "description": ctx.caption}
        if ctx.options.get("category_id"):
            snippet["categoryId"] = str(ctx.options["category_id"])
        status: dict[str, Any] = {"privacyStatus": ctx.options["privacy_status"]}
        if "made_for_kids" in ctx.options:
            status["selfDeclaredMadeForKids"] = bool(ctx.options["made_for_kids"])
        response = self._request(
            "POST",
            self.UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            json={"snippet": snippet, "status": status},
            headers={
                **self._auth(ctx),
                "X-Upload-Content-Length": str(ctx.media.size_bytes),
                "X-Upload-Content-Type": ctx.media.mime_type,
            },
        )
        if response.status_code != 200:
            raise self._error(response, "upload session initialisation failed")
        location = response.headers.get("Location")
        parsed = urlparse(location or "")
        # The access token is sent to this URI: only accept Google's upload host over TLS.
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".googleapis.com"):
            raise PublishError("YouTube did not return a valid upload session URI", code="malformed_response",
                               http_status=response.status_code)
        return location

    def _upload(self, ctx: PublishContext, session_uri: str) -> str:
        """Upload in chunks; resume from the server's offset after transient failures."""
        total = ctx.media.size_bytes
        offset = 0
        resumes = 0
        with open(ctx.video_path, "rb") as f:
            while True:
                end = min(offset + self.CHUNK_SIZE, total) - 1
                f.seek(offset)
                body = f.read(end - offset + 1)
                final_chunk = end == total - 1
                try:
                    response = self._request("PUT", session_uri, content=body, headers={
                        **self._auth(ctx),
                        "Content-Type": ctx.media.mime_type,
                        "Content-Range": f"bytes {offset}-{end}/{total}",
                    })
                except PublishError:
                    response = None

                if response is not None and response.status_code in (200, 201):
                    return self._video_id(response)
                if response is not None and response.status_code == 308:
                    offset = self._next_offset(response)
                    continue
                if response is not None and response.status_code < 500:
                    raise self._error(response, "upload failed")

                # Network error or 5xx: ask the server how much it has, then resume (bounded).
                resumes += 1
                if resumes > self.MAX_RESUMES:
                    raise PublishError(
                        "YouTube upload failed repeatedly" + (" after the final chunk; the video may exist" if final_chunk else ""),
                        code="upload_failed", retryable=not final_chunk, uncertain=final_chunk,
                    )
                self.sleep(2 ** resumes)
                done, offset = self._query_offset(ctx, session_uri, total, final_chunk)
                if done:
                    return done

    def _query_offset(self, ctx: PublishContext, session_uri: str, total: int, final_chunk: bool) -> tuple[str | None, int]:
        """Ask the session how many bytes it holds: (video_id if already complete, next offset)."""
        unknown = PublishError(
            "YouTube upload interrupted and status unknown" + ("; the video may exist" if final_chunk else ""),
            code="upload_interrupted", retryable=not final_chunk, uncertain=final_chunk,
        )
        try:
            response = self._request("PUT", session_uri, content=b"", headers={
                **self._auth(ctx), "Content-Range": f"bytes */{total}",
            })
        except PublishError as e:
            raise unknown from e
        if response.status_code in (200, 201):
            return self._video_id(response), total
        if response.status_code == 308:
            return None, self._next_offset(response)
        if response.status_code >= 500:
            raise unknown
        if response.status_code == 404:
            raise PublishError("YouTube upload session expired", code="session_expired", http_status=404,
                               retryable=not final_chunk, uncertain=final_chunk)
        raise self._error(response, "upload status query failed")

    @staticmethod
    def _next_offset(response: httpx.Response) -> int:
        # "Range: bytes=0-999999" -> next byte 1000000; no header -> nothing stored yet.
        rng = response.headers.get("Range")
        if not rng or "-" not in rng:
            return 0
        return int(rng.rsplit("-", 1)[1]) + 1

    def _video_id(self, response: httpx.Response) -> str:
        video_id = self._json(response).get("id")
        if not video_id:
            # Upload completed but no id: never re-upload blindly.
            raise PublishError("YouTube upload completed without a video id", code="malformed_response",
                               uncertain=True, http_status=response.status_code)
        return str(video_id)

    def _processing(self, ctx: PublishContext, state: dict[str, Any]) -> PublishOutcome:
        response = self._request(
            "GET", self.VIDEOS_URL,
            params={"part": "status,processingDetails", "id": state["video_id"]},
            headers=self._auth(ctx),
        )
        if response.status_code != 200:
            raise self._error(response, "processing status check failed")
        items = self._json(response).get("items") or []
        if not items:
            return PublishOutcome("processing", state["video_id"], state)  # not listed yet
        status = items[0].get("status") or {}
        upload_status = status.get("uploadStatus")
        if upload_status == "processed":
            return PublishOutcome("published", state["video_id"], state)
        if upload_status in ("failed", "rejected", "deleted"):
            reason = status.get("failureReason") or status.get("rejectionReason") or upload_status
            raise PublishError(f"YouTube video {upload_status}: {reason}", code=f"video_{upload_status}")
        return PublishOutcome("processing", state["video_id"], state)

    @staticmethod
    def _auth(ctx: PublishContext) -> dict[str, str]:
        return {"Authorization": f"Bearer {ctx.access_token}"}

    def _error(self, response: httpx.Response, what: str) -> PublishError:
        reason, message = None, None
        try:
            err = (response.json() or {}).get("error") or {}
            message = err.get("message")
            reason = ((err.get("errors") or [{}])[0]).get("reason")
        except (ValueError, AttributeError, IndexError):
            pass
        if reason in QUOTA_REASONS:
            return PublishError(f"YouTube {what}: quota exceeded ({reason})", code="quota_exceeded",
                                http_status=response.status_code)
        error = http_error("YouTube", response, reason or "error", f"{what}: {message or 'no message'}")
        return error
