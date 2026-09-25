"""Instagram Reels publishing via the Instagram API with Instagram Login.

Verified against Meta docs on 2026-09-25 (content-publishing, ig-user/media reference):

    POST graph.instagram.com/v25.0/{ig_user_id}/media         media_type=REELS, video_url, caption
        -> {"id": container_id}
    GET  graph.instagram.com/v25.0/{container_id}?fields=status_code
        -> EXPIRED | ERROR | FINISHED | IN_PROGRESS | PUBLISHED   (poll once/minute, max 5 minutes)
    POST graph.instagram.com/v25.0/{ig_user_id}/media_publish creation_id=container_id
        -> {"id": ig_media_id}

With Instagram Login, Meta documents video publishing only through ``video_url`` (a public URL
Meta downloads). The rupload.facebook.com resumable upload is documented for Facebook Login
only, so local-file upload is NOT implemented for Instagram.
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
)


class InstagramPublisher(PlatformPublisher):
    PLATFORM = "instagram"
    GRAPH_URL = "https://graph.instagram.com/v25.0"
    # Long-lived tokens last ~60 days and can only be re-exchanged once >= 24h old: renew early.
    TOKEN_REFRESH_MARGIN = 7 * 24 * 3600
    # Meta: "query a container's status once per minute, for no more than 5 minutes".
    POLL_INTERVAL = 60.0
    POLL_ATTEMPTS = 5

    MAX_CAPTION_CHARS = 2200
    MAX_HASHTAGS = 30
    MAX_MENTIONS = 20
    MAX_SIZE_BYTES = 300 * 1024 * 1024
    MIN_DURATION, MAX_DURATION = 3, 15 * 60
    MAX_WIDTH = 1920

    def validate(self, caption: str, options: dict[str, Any], media: MediaInfo) -> list[str]:
        errors = []
        url = options.get("video_url") or ""
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append("Instagram needs a public https:// video_url that Meta can download (local upload is not supported with Instagram Login)")
        if media.mime_type not in ("video/mp4", "video/quicktime"):
            errors.append("Instagram Reels accept MP4 or MOV only")
        if len(caption) > self.MAX_CAPTION_CHARS:
            errors.append(f"Instagram caption exceeds {self.MAX_CAPTION_CHARS} characters")
        if caption.count("#") > self.MAX_HASHTAGS:
            errors.append(f"Instagram caption has more than {self.MAX_HASHTAGS} hashtags")
        if caption.count("@") > self.MAX_MENTIONS:
            errors.append(f"Instagram caption has more than {self.MAX_MENTIONS} @ tags")
        if media.size_bytes > self.MAX_SIZE_BYTES:
            errors.append("Instagram Reels are limited to 300 MB")
        if media.duration_seconds is not None and not self.MIN_DURATION <= media.duration_seconds <= self.MAX_DURATION:
            errors.append("Instagram Reels must be 3 seconds to 15 minutes long")
        if media.width is not None and media.width > self.MAX_WIDTH:
            errors.append("Instagram Reels are limited to 1920 px width")
        return errors

    def publish(self, ctx: PublishContext, on_progress: ProgressCallback) -> PublishOutcome:
        state = dict(ctx.state)
        container_id = state.get("container_id")

        # Resume: never create a second container or publish twice.
        if container_id:
            status = self._container_status(ctx, container_id)
            if status == "PUBLISHED":
                return PublishOutcome("published", state.get("media_id") or container_id, state)
            if status in ("ERROR", "EXPIRED"):
                container_id = None
                state = {}

        if not container_id:
            container_id = self._create_container(ctx)
            state = {"container_id": container_id}
            on_progress("processing", state)

        outcome = self._poll(lambda: self._processing_outcome(ctx, container_id, state))
        if outcome.status == "processing":
            return outcome  # still IN_PROGRESS after the documented polling window

        if outcome.status == "published":  # container already published (e.g. earlier response lost)
            return outcome

        # FINISHED: publish exactly once.
        state["publish_requested"] = True
        on_progress("processing", state)
        response = self._request(
            "POST",
            f"{self.GRAPH_URL}/{ctx.platform_account_id}/media_publish",
            data={"creation_id": container_id},
            headers=self._auth(ctx),
        )
        data = self._check(response, "media_publish failed")
        media_id = data.get("id")
        if not media_id:
            raise PublishError("Instagram media_publish returned no media id", code="malformed_response",
                               http_status=response.status_code)
        state["media_id"] = str(media_id)
        return PublishOutcome("published", str(media_id), state)

    def _create_container(self, ctx: PublishContext) -> str:
        payload = {"media_type": "REELS", "video_url": ctx.options["video_url"], "caption": ctx.caption}
        if "share_to_feed" in ctx.options:
            payload["share_to_feed"] = "true" if ctx.options["share_to_feed"] else "false"
        response = self._request(
            "POST", f"{self.GRAPH_URL}/{ctx.platform_account_id}/media", data=payload, headers=self._auth(ctx)
        )
        data = self._check(response, "media container creation failed")
        if not data.get("id"):
            raise PublishError("Instagram returned no container id", code="malformed_response",
                               http_status=response.status_code)
        return str(data["id"])

    def _container_status(self, ctx: PublishContext, container_id: str) -> str:
        response = self._request(
            "GET", f"{self.GRAPH_URL}/{container_id}", params={"fields": "status_code"}, headers=self._auth(ctx)
        )
        data = self._check(response, "container status check failed")
        status = data.get("status_code")
        if not status:
            raise PublishError("Instagram container status missing", code="malformed_response",
                               http_status=response.status_code, retryable=True)
        return status

    def _processing_outcome(self, ctx: PublishContext, container_id: str, state: dict) -> PublishOutcome:
        status = self._container_status(ctx, container_id)
        if status == "IN_PROGRESS":
            return PublishOutcome("processing", None, state)
        if status == "FINISHED":
            return PublishOutcome("ready", None, state)
        if status == "PUBLISHED":
            return PublishOutcome("published", state.get("media_id") or container_id, state)
        if status == "EXPIRED":
            raise PublishError("Instagram container expired before publishing", code="container_expired", retryable=True)
        raise PublishError(f"Instagram media processing failed (status {status})", code="processing_failed")

    @staticmethod
    def _auth(ctx: PublishContext) -> dict[str, str]:
        # Header, never the URL: URLs end up in exception text and HTTP logs.
        return {"Authorization": f"Bearer {ctx.access_token}"}

    def _check(self, response: httpx.Response, what: str) -> dict[str, Any]:
        if response.status_code == 200:
            return self._json(response)
        err: dict[str, Any] = {}
        try:
            err = (response.json() or {}).get("error") or {}
        except ValueError:
            pass
        http_status = response.status_code
        error_code = err.get("code")
        retryable = bool(err.get("is_transient")) or http_status == 429 or http_status >= 500
        if http_status == 401 or error_code == 190:
            code = "unauthorized"
        elif http_status == 403 or error_code in (10, 200):
            code = "permission_denied"
        elif error_code in (4, 9, 17, 32, 613):
            code, retryable = "rate_limited", True
        else:
            code = "provider_error"
        detail = err.get("message") or "no error message"
        ref = f", fbtrace_id {err['fbtrace_id']}" if err.get("fbtrace_id") else ""
        raise PublishError(
            f"Instagram {what}: {detail} (HTTP {http_status}, error_code {error_code}{ref})",
            code=code, retryable=retryable, http_status=http_status,
        )
