"""Platform publisher interface shared by the Instagram, TikTok and YouTube adapters.

The core engine (src/core/publisher.py) only talks to this interface; all platform HTTP
lives in src/platforms/<platform>/publisher.py.
"""

import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.core.validation import MediaInfo

# httpx logs full request URLs at INFO; TikTok upload URLs and YouTube session URIs are
# bearer-like secrets, so keep its logging at WARNING regardless of the app's log level.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Stage callback: (job status "uploading"/"processing", provider state to persist now).
ProgressCallback = Callable[[str, dict[str, Any]], None]

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer|oauth)\s+[^\s,;\"']+"),
    re.compile(r"(?i)\b(bearer|oauth)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(\b(?:access_token|refresh_token|client_secret|code|token)[\"']?\s*[=:]\s*[\"']?)[^&\s,;\"']+"),
]


def redact(text: str | None, limit: int = 500) -> str:
    """Remove anything token-like from provider/error text before it is stored or shown."""
    if not text:
        return ""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", text)
    return text[:limit]


class PublishError(Exception):
    """A publish step failed.

    retryable: the same request may succeed later (network error, 5xx, 429, transient).
    uncertain: the provider may have acted even though we saw a failure — never auto-retry.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "error",
        retryable: bool = False,
        http_status: int | None = None,
        uncertain: bool = False,
    ):
        self.code = code
        self.retryable = retryable and not uncertain
        self.http_status = http_status
        self.uncertain = uncertain
        super().__init__(redact(message))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "http_status": self.http_status,
            "retryable": self.retryable,
            "uncertain": self.uncertain,
        }


@dataclass
class PublishContext:
    """Everything an adapter needs for one job. ``access_token`` never leaves the adapter."""

    job_id: int
    video_path: str
    caption: str
    options: dict[str, Any]
    access_token: str
    platform_account_id: str
    media: MediaInfo
    # Provider references from earlier attempts (container id, publish_id, video id, ...).
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishOutcome:
    """status: "published" or "processing" (provider still working; check again later)."""

    status: str
    platform_media_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    # Cover/thumbnail result, separate from the video: "published" | "failed" | None (not attempted).
    cover_status: str | None = None
    cover_error: str | None = None


COVER_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


class PlatformPublisher(ABC):
    """Base class for platform publishing adapters."""

    PLATFORM: str = ""
    # Refresh the access token when it expires within this many seconds.
    TOKEN_REFRESH_MARGIN = 300
    # False when a crash mid-upload may have created a post we can't detect (YouTube).
    RESTART_SAFE = True
    POLL_INTERVAL: float = 10.0
    POLL_ATTEMPTS: int = 30

    def __init__(
        self,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float | None = None,
        poll_attempts: int | None = None,
    ):
        self.client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))
        self.sleep = sleep
        if poll_interval is not None:
            self.POLL_INTERVAL = poll_interval
        if poll_attempts is not None:
            self.POLL_ATTEMPTS = poll_attempts

    @abstractmethod
    def validate(self, caption: str, options: dict[str, Any], media: MediaInfo) -> list[str]:
        """Platform-specific checks that need no network. Returns human-readable errors."""

    @abstractmethod
    def publish(self, ctx: PublishContext, on_progress: ProgressCallback) -> PublishOutcome:
        """Run the publish flow, resuming from ``ctx.state`` when earlier attempts saved provider IDs.

        Must call ``on_progress`` right after the provider assigns an ID (so it is persisted before
        the next step) and never repeat a step the saved state shows already happened.
        Raises PublishError.
        """

    def can_restart(self, state: dict[str, Any]) -> bool:
        """Whether a job interrupted mid-upload (process crash) can safely be started again."""
        return self.RESTART_SAFE

    # --- cover / thumbnail capability ----------------------------------------------
    # Each platform documents covers differently; adapters override these.
    COVER_UNSUPPORTED_REASON = "custom cover images are not supported for this platform"

    def supports_cover_upload(self) -> bool:
        """Can a local cover image file be uploaded for the published video?"""
        return False

    def supports_cover_timestamp(self) -> bool:
        """Can a frame of the video be chosen as the cover (no image upload)?"""
        return False

    def supports_custom_cover(self) -> bool:
        return self.supports_cover_upload()

    def validate_cover(self, cover_path: str) -> list[str]:
        """Platform-specific cover checks (type/size). Only called when uploads are supported."""
        return []

    def cover_plan(self, cover_path: str | None) -> tuple[str, str]:
        """How this platform would handle a cover: (status, reason).

        status: "none" (no cover), "upload" (will be uploaded after the video),
        "not_supported", or "skipped" (supported but this file is unusable).
        """
        if not cover_path:
            return "none", "no cover"
        if not self.supports_cover_upload():
            return "not_supported", self.COVER_UNSUPPORTED_REASON
        errors = self.validate_cover(cover_path)
        return ("skipped", "; ".join(errors)) if errors else ("upload", "")

    # --- shared HTTP helpers -------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Send a request; network failures become retryable PublishErrors (no URL/headers in text)."""
        try:
            return self.client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise PublishError(f"{self.PLATFORM} request timed out", code="timeout", retryable=True) from e
        except httpx.TransportError as e:
            raise PublishError(
                f"{self.PLATFORM} network error ({type(e).__name__})", code="network", retryable=True
            ) from e

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as e:
            raise PublishError(
                f"Malformed provider response (HTTP {response.status_code})",
                code="malformed_response",
                http_status=response.status_code,
                retryable=response.status_code >= 500,
            ) from e
        if not isinstance(data, dict):
            raise PublishError("Malformed provider response", code="malformed_response", http_status=response.status_code)
        return data

    def _poll(self, check: Callable[[], PublishOutcome]) -> PublishOutcome:
        """Poll ``check`` until it reports published, raises, or attempts run out ("processing")."""
        outcome = check()
        for _ in range(self.POLL_ATTEMPTS - 1):
            if outcome.status != "processing":
                return outcome
            self.sleep(self.POLL_INTERVAL)
            outcome = check()
        return outcome


def http_error(platform: str, response: httpx.Response, code: str, message: str) -> PublishError:
    """Map an HTTP failure to a PublishError. 429 and 5xx are retryable; 4xx are not."""
    status = response.status_code
    retryable = status == 429 or status >= 500
    if status == 401:
        code = "unauthorized"
    elif status == 403 and code == "error":
        code = "forbidden"
    return PublishError(f"{platform}: {message} (HTTP {status})", code=code, retryable=retryable, http_status=status)
