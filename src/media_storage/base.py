"""Object storage abstraction for temporary media delivery.

Platforms that must fetch media from a URL (Instagram) use this to put a local file behind a
short-lived, read-only HTTPS link. Implementations never expose credentials or signed URLs in
exception text.
"""

import ipaddress
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# Content types a temporary host may receive, with the generic upload filename extension.
UPLOAD_EXTENSIONS = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}


class MediaStorageError(Exception):
    """A storage operation failed. The message is safe to show (no credentials, no signed URLs).

    retryable: a later attempt may succeed (network error, 5xx, throttling). 4xx such as
    AccessDenied or NoSuchBucket are configuration problems and are not retried.
    """

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class StorageNotConfiguredError(MediaStorageError):
    """Required MEDIA_STORAGE_* settings are missing or invalid."""


@dataclass(frozen=True)
class StorageObject:
    key: str
    size: int
    content_type: str


class ObjectStorage(ABC):
    """Private object storage that can hand out temporary read URLs."""

    provider: str = ""

    @abstractmethod
    def upload_file(self, file_path: Path, object_key: str, content_type: str) -> StorageObject:
        """Stream a local file to ``object_key`` (never loaded fully into memory)."""

    @abstractmethod
    def create_presigned_url(self, object_key: str, expires_in: int) -> str:
        """HTTPS GET URL for one object, valid ``expires_in`` seconds. Treat the result as a secret."""

    @abstractmethod
    def delete_object(self, object_key: str) -> None:
        """Delete the object (idempotent)."""

    @abstractmethod
    def health_check(self) -> None:
        """Lightweight check that the bucket is reachable with these credentials. Raises MediaStorageError."""


def is_public_https_host(url: str) -> bool:
    """https URL whose host is a public name (not localhost, not a private/loopback IP literal)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host  # a DNS name
    return ip.is_global


def check_upload_file(path: Path, content_type: str, max_bytes: int, provider: str) -> int:
    """Local checks before sending a file to a host. Returns the size; raises MediaStorageError."""
    if not path.is_file():
        raise MediaStorageError(f"Video file not found: {path.name}")
    size = path.stat().st_size
    if size == 0:
        raise MediaStorageError(f"Video file is empty: {path.name}")
    if content_type not in UPLOAD_EXTENSIONS:
        raise MediaStorageError(f"Unsupported video type for upload: {content_type}")
    if size > max_bytes:
        raise MediaStorageError(f"{provider} accepts files up to {max_bytes // 1_000_000} MB "
                                f"(this video is {size // 1_000_000} MB)")
    return size

