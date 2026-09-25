"""Instagram Reels publishing via the Instagram API with Instagram Login.

Verified against Meta docs on 2026-09-25 (content-publishing, ig-user/media reference):

    POST graph.instagram.com/v25.0/{ig_user_id}/media         media_type=REELS, video_url, caption
        -> {"id": container_id}
    GET  graph.instagram.com/v25.0/{container_id}?fields=status_code
        -> EXPIRED | ERROR | FINISHED | IN_PROGRESS | PUBLISHED   (poll once/minute, max 5 minutes)
    POST graph.instagram.com/v25.0/{ig_user_id}/media_publish creation_id=container_id
        -> {"id": ig_media_id}

With Instagram Login, Meta documents video publishing only through ``video_url`` (a public URL
Meta downloads). The rupload.facebook.com resumable upload is documented for Facebook Login only.

Local files therefore go through a MediaSourceProvider (src/media_storage): the file is uploaded to
temporary private storage, Meta gets a short-lived presigned HTTPS URL, and the object is deleted
when this publish attempt ends. The user never enters a URL. (An explicit ``video_url`` option is
still honoured as an internal/debug override.)
"""

import logging
import math
import os
from collections.abc import Callable
from contextlib import ExitStack
from typing import Any
from urllib.parse import urlparse

import httpx

from src.core.validation import MediaInfo
from src.media_storage import (
    MediaSourceProvider,
    MediaStorageError,
    create_media_provider,
    delivery_provider,
    media_delivery_description,
    media_provider_problems,
)
from src.platforms.base import (
    PlatformPublisher,
    ProgressCallback,
    PublishContext,
    PublishError,
    PublishOutcome,
)

log = logging.getLogger("soc_bot.instagram")


class InstagramPublisher(PlatformPublisher):
    PLATFORM = "instagram"
    GRAPH_URL = "https://graph.instagram.com/v25.0"
    # Long-lived tokens last ~60 days and can only be re-exchanged once >= 24h old: renew early.
    TOKEN_REFRESH_MARGIN = 7 * 24 * 3600
    # Meta: "query a container's status once per minute, for no more than 5 minutes".
    POLL_INTERVAL = 60.0
    POLL_ATTEMPTS = 5
    # Large files: one extra poll per 25 MB above 50 MB (Meta downloads them first), capped by
    # INSTAGRAM_MAX_POLL_MINUTES. The media URL stays alive for the whole window.
    DEFAULT_MAX_POLL_MINUTES = 15

    MAX_CAPTION_CHARS = 2200
    MAX_HASHTAGS = 30
    MAX_MENTIONS = 20
    MAX_SIZE_BYTES = 300 * 1024 * 1024
    MIN_DURATION, MAX_DURATION = 3, 15 * 60
    MAX_WIDTH = 1920
    REQUIRED_SCOPES = (("instagram_business_content_publish",),)

    # Reels cover_url exists in the Facebook Login reference only and needs a *public* image URL;
    # the Instagram Login publishing docs don't document it. thumb_offset (a frame) is also
    # documented only in that reference. Neither is used.
    COVER_UNSUPPORTED_REASON = (
        "Instagram Login publishing documents no cover image upload (cover_url needs a public image URL "
        "and is only documented for Facebook Login); cover image skipped"
    )

    def __init__(self, *args, media_provider: Callable[[], MediaSourceProvider] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        # Factory, so an unconfigured provider only matters when a job actually needs media.
        self._media_provider_factory = media_provider or create_media_provider

    def validate(self, caption: str, options: dict[str, Any], media: MediaInfo) -> list[str]:
        errors = []
        if options.get("video_url"):  # internal/debug override
            parsed = urlparse(options["video_url"])
            if parsed.scheme != "https" or not parsed.netloc:
                errors.append("video_url override must be an https:// URL")
        else:
            problems = self.media_problems(media.size_bytes)
            if problems:
                errors.append("Instagram temporary media storage is not configured: " + "; ".join(problems)
                              + " (see docs/API_INTEGRATIONS.md > Instagram media delivery)")
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

    def media_problems(self, size: int | None = None) -> list[str]:
        """No-network check that this file can be delivered (with ``auto``, depends on its size)."""
        return media_provider_problems(size) if self._media_provider_factory is create_media_provider else []

    def delivery_notes(self, options: dict[str, Any], media: MediaInfo | None = None) -> list[str]:
        if options.get("video_url"):
            return ["media delivery: explicit video_url override"]
        size = media.size_bytes if media is not None else None
        when = ("tunnel started only after you confirm, stopped when Instagram is done"
                if delivery_provider(size) == "cloudflare_tunnel" else "uploaded only when publishing")
        return [f"media delivery: {media_delivery_description(size)} ({when})"]

    def poll_attempts(self, size: int | None) -> int:
        try:
            cap = int(os.environ.get("INSTAGRAM_MAX_POLL_MINUTES") or self.DEFAULT_MAX_POLL_MINUTES)
        except ValueError:
            cap = self.DEFAULT_MAX_POLL_MINUTES
        extra = math.ceil(max(0, (size or 0) - 50_000_000) / 25_000_000)
        return max(self.POLL_ATTEMPTS, min(self.POLL_ATTEMPTS + extra, cap))

    def _media_url(self, ctx: PublishContext, stack: ExitStack) -> str:
        """Direct HTTPS URL for this attempt. Temporary media is cleaned up when ``stack`` closes."""
        if ctx.options.get("video_url"):
            return ctx.options["video_url"]
        log.info("Preparing Instagram media.")
        try:
            provider = self._media_provider_factory()
            handle = provider.prepare(ctx.video_path, ctx.media.mime_type)
            stack.callback(provider.cleanup, handle)
            return provider.get_public_url(handle)
        except MediaStorageError as e:
            # Storage outages are transient; configuration/permission problems are not.
            raise PublishError(f"Instagram media preparation failed: {e}", code="media_storage",
                               retryable=e.retryable) from e

    def publish(self, ctx: PublishContext, on_progress: ProgressCallback) -> PublishOutcome:
        # The temporary media object lives until this attempt ends: through container creation,
        # Meta's download/processing and media_publish. It is deleted on success, failure or Ctrl+C.
        # A retry or resume that needs a new container uploads fresh media (URLs are never reused).
        with ExitStack() as stack:
            return self._publish(ctx, on_progress, stack)

    def _publish(self, ctx: PublishContext, on_progress: ProgressCallback, stack: ExitStack) -> PublishOutcome:
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
            container_id = self._create_container(ctx, self._media_url(ctx, stack))
            log.info("Instagram media container created.")
            state = {"container_id": container_id}  # never the media URL
            on_progress("processing", state)
            log.info("Waiting for Instagram media processing.")

        size = ctx.media.size_bytes if ctx.media is not None else None
        outcome = self._poll(lambda: self._processing_outcome(ctx, container_id, state), self.poll_attempts(size))
        if outcome.status == "processing":
            return outcome  # still IN_PROGRESS after the documented polling window

        if outcome.status == "published":  # container already published (e.g. earlier response lost)
            return outcome

        # FINISHED: publish exactly once.
        log.info("Publishing Instagram media.")
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
        permalink = self.fetch_permalink(ctx.access_token, str(media_id))
        if permalink:
            state["permalink"] = permalink  # permanent public URL (not a secret)
        return PublishOutcome("published", str(media_id), state)

    def fetch_permalink(self, access_token: str, media_id: str) -> str | None:
        """IG Media ``permalink`` ("Permanent URL to the media"). Best effort: never fails a publish."""
        try:
            response = self._request("GET", f"{self.GRAPH_URL}/{media_id}", params={"fields": "permalink"},
                                     headers={"Authorization": f"Bearer {access_token}"})
            permalink = self._json(response).get("permalink") if response.status_code == 200 else None
        except Exception:  # noqa: BLE001 - the Reel is already published; a missing link is only logged
            log.warning("Could not read the Instagram permalink; the Reel is published but its link isn't saved.")
            return None
        return permalink if isinstance(permalink, str) and permalink.startswith("https://www.instagram.com/") else None

    def published_url(self, platform_media_id: str | None, state: dict[str, Any]) -> str | None:
        # Only the permalink Instagram returned; the media id alone can't be turned into a URL.
        return state.get("permalink")

    def _create_container(self, ctx: PublishContext, video_url: str) -> str:
        payload = {"media_type": "REELS", "video_url": video_url, "caption": ctx.caption}
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
