"""Provider-neutral media source: turns a local video into a temporary direct HTTPS media URL.

    local video --prepare()--> MediaHandle --get_public_url()--> https URL --cleanup()--> gone

Consumers (the Instagram publisher) only see this interface, never S3/R2/MinIO details.
YouTube is deliberately NOT a provider: the YouTube Data API returns watch/page URLs, not direct
media files, and downloading or scraping YouTube media violates its Terms of Service.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from src.media_storage.base import (
    MediaStorageError,
    ObjectStorage,
    StorageNotConfiguredError,
)
from src.media_storage.service import (
    StorageSettings,
    create_media_storage,
    new_object_key,
)

log = logging.getLogger("soc_bot.media")


@dataclass
class MediaHandle:
    """A prepared temporary media object. In-memory only: never logged or persisted.

    S3 mints URLs on demand (public_url stays None). Hosts that return a fixed URL and a deletion
    token (0x0.st) keep them here; both are hidden from repr so the handle can't leak them.
    """

    source_path: Path
    content_type: str
    object_key: str = ""
    cleaned: bool = field(default=False)
    public_url: str | None = field(default=None, repr=False)
    # Temporary URL of the cover image served with this video (providers with supports_cover only).
    cover_url: str | None = field(default=None, repr=False)
    token: str | None = field(default=None, repr=False)
    # Provider that created the object (set by the router so cleanup goes to the right place).
    provider: object | None = field(default=None, repr=False, compare=False)
    # Live resources a provider keeps for this object (Cloudflare Tunnel: local server + cloudflared).
    session: object | None = field(default=None, repr=False, compare=False)
    created_at: float = field(default_factory=time.monotonic, repr=False, compare=False)


class MediaSourceProvider(ABC):
    name: str = ""
    # Capability: largest file this provider accepts, in bytes (None = no application limit).
    max_file_size: int | None = None
    # Capability: can also deliver a cover image (prepare(..., cover_path=...) sets handle.cover_url).
    supports_cover: bool = False

    @abstractmethod
    def prepare(self, video_path: Path, content_type: str) -> MediaHandle:
        """Make the local file available as a temporary object. Never modifies the source."""

    @abstractmethod
    def get_public_url(self, handle: MediaHandle) -> str:
        """A fresh direct HTTPS URL for the object (short-lived). Do not log or persist it."""

    @abstractmethod
    def cleanup(self, handle: MediaHandle) -> None:
        """Remove the temporary object. Idempotent; never touches the local source file."""

    def describe(self) -> str:
        return self.name

    def is_alive(self, handle: MediaHandle) -> bool:
        """Can this prepared object still be fetched? (Shared batch sessions replace dead ones.)"""
        return not handle.cleaned

    def health_check(self) -> str:
        """Lightweight, upload-free check. Returns a message; raises MediaStorageError."""
        return "No check available for this provider."


class ObjectStorageMediaProvider(MediaSourceProvider):
    """Private bucket + presigned GET URL (S3-compatible)."""

    name = "temporary object storage + presigned HTTPS URL"
    max_file_size = None  # multipart upload; Instagram's own 300 MB limit is checked by the adapter

    def __init__(self, storage: ObjectStorage, ttl: int, delete_after: bool = True):
        self.storage = storage
        self.ttl = ttl
        self.delete_after = delete_after

    def prepare(self, video_path: Path, content_type: str) -> MediaHandle:
        path = Path(video_path)
        if not path.is_file():
            raise MediaStorageError(f"Video file not found: {path.name}")
        handle = MediaHandle(path, content_type, new_object_key(content_type))
        log.info("Uploading temporary media.")
        self.storage.upload_file(path, handle.object_key, content_type)
        return handle

    def get_public_url(self, handle: MediaHandle) -> str:
        if handle.cleaned:
            raise MediaStorageError("Temporary media was already cleaned up")
        url = self.storage.create_presigned_url(handle.object_key, self.ttl)
        log.info("Temporary media URL generated (expires in %ss).", self.ttl)
        return url

    def health_check(self) -> str:
        self.storage.health_check()
        return "Media storage bucket reachable. (Keep it private; add a lifecycle rule that expires instagram/temp/.)"

    def cleanup(self, handle: MediaHandle | None) -> None:
        if handle is None or handle.cleaned:
            return
        handle.cleaned = True
        if not self.delete_after:
            log.info("Temporary media kept (MEDIA_STORAGE_DELETE_AFTER_PUBLISH=false).")
            return
        try:
            self.storage.delete_object(handle.object_key)
            log.info("Temporary media deleted.")
        except MediaStorageError as e:
            # Private object with an expiring URL; a bucket lifecycle rule on instagram/temp/ removes it.
            log.warning("Could not delete temporary media (%s).", e)


def media_provider_problems(size: int | None = None, needs_cover: bool = False) -> list[str]:
    """Why no provider can take the media (no network). Empty = OK. With ``auto`` the answer depends on
    size and on whether a cover image must be delivered too."""
    settings = StorageSettings.from_env()
    problems = settings.problems()
    if problems:
        return problems
    if settings.provider != "auto":
        if needs_cover and settings.provider not in COVER_PROVIDERS:
            return [(f"MEDIA_STORAGE_PROVIDER={settings.provider} can't deliver a cover image "
                     "(use auto or cloudflare_tunnel)")]
        return []
    if size is None:
        return []
    return build_router(settings).problems_for(size, needs_cover)


def create_media_provider() -> MediaSourceProvider:
    """Factory from MEDIA_STORAGE_* settings (s3 | tempfile | cloudflare_tunnel | 0x0 | auto).

    Raises StorageNotConfiguredError."""
    settings = StorageSettings.from_env()
    if settings.provider == "cloudflare_tunnel":
        problems = settings.problems()
        if problems:
            raise StorageNotConfiguredError("Instagram media delivery is not configured: " + "; ".join(problems))
        return _tunnel(settings)
    if settings.provider == "auto":
        problems = settings.problems()
        if problems:
            raise StorageNotConfiguredError("Instagram temporary media storage is not configured: " + "; ".join(problems))
        return build_router(settings)
    if settings.provider in ("0x0", "tempfile"):
        problems = settings.problems()
        if problems:
            raise StorageNotConfiguredError("Instagram temporary media storage is not configured: " + "; ".join(problems))
        if settings.provider == "tempfile":
            from src.media_storage.tempfile import TempFileMediaStorage

            return TempFileMediaStorage(expiry_hours=settings.tempfile_expiry_hours)
        from src.media_storage.zerox0 import ZeroX0MediaStorage

        return ZeroX0MediaStorage(settings.zerox0_url, settings.zerox0_expires_hours)
    return ObjectStorageMediaProvider(create_media_storage(settings), settings.ttl, settings.delete_after_publish)


# Providers that can serve a cover image next to the video (declared by the provider class).
COVER_PROVIDERS = ("cloudflare_tunnel",)


def _tunnel(settings: StorageSettings) -> MediaSourceProvider:
    from src.media_storage.cloudflare_tunnel import CloudflareTunnelMediaProvider

    return CloudflareTunnelMediaProvider(settings.cloudflared_path, settings.tunnel_startup_timeout,
                                         settings.tunnel_token_bytes, settings.tunnel_host)


def build_router(settings: StorageSettings):
    """The AUTO tiers: TempFile.org first, then the Cloudflare Quick Tunnel for what TempFile can't take,
    then S3 (only reached when cloudflared is unavailable). Limits come from the providers."""
    from dataclasses import replace

    from src.media_storage.cloudflare_tunnel import CloudflareTunnelMediaProvider
    from src.media_storage.router import AUTO_ORDER, MediaStorageRouter, Tier
    from src.media_storage.tempfile import TempFileMediaStorage

    as_s3 = replace(settings, provider="s3")
    as_tempfile = replace(settings, provider="tempfile")
    as_tunnel = replace(settings, provider="cloudflare_tunnel")
    available = {
        "tempfile": Tier(
            "tempfile", "TempFile.org (temporary PUBLIC upload)", TempFileMediaStorage.max_file_size,
            as_tempfile.problems, lambda: TempFileMediaStorage(expiry_hours=settings.tempfile_expiry_hours),
            TempFileMediaStorage.supports_cover),
        "cloudflare_tunnel": Tier(
            "cloudflare_tunnel", "Cloudflare Quick Tunnel (served from this computer, nothing uploaded)",
            CloudflareTunnelMediaProvider.max_file_size, as_tunnel.problems, lambda: _tunnel(settings),
            CloudflareTunnelMediaProvider.supports_cover),
        "s3": Tier(
            "s3", "S3 (PRIVATE temporary object + presigned HTTPS URL)", ObjectStorageMediaProvider.max_file_size,
            as_s3.problems,
            lambda: ObjectStorageMediaProvider(create_media_storage(as_s3), settings.ttl, settings.delete_after_publish),
            ObjectStorageMediaProvider.supports_cover),
    }
    return MediaStorageRouter([available[name] for name in AUTO_ORDER], margin_bytes=settings.auto_margin_bytes)


def media_delivery_description(size: int | None = None, needs_cover: bool = False) -> str:
    """How Instagram media will be delivered with the current settings (no network)."""
    settings = StorageSettings.from_env()
    if settings.provider == "auto" and size is not None:
        from src.media_storage.router import fmt_mb

        router = build_router(settings)
        try:
            tier = router.select(size, needs_cover)
        except StorageNotConfiguredError:
            return f"{fmt_mb(size)} video: no media provider can take it (see error)"
        reason = ""
        if tier.name != "tempfile":
            too_big = not router.fits(router.tiers[0], size)
            reason = "; too large for TempFile.org" if too_big else "; video + cover need a provider that serves both"
        if needs_cover:
            reason += "; cover served with the video"
        return f"{fmt_mb(size)} video -> {_describe(tier.name)} [auto{reason}]"
    if settings.provider == "auto":
        return ("automatic by size: TempFile.org (public) for small videos, a Cloudflare Quick Tunnel for large "
                "ones (S3 if cloudflared is unavailable)")
    return _describe(settings.provider)


def _describe(provider: str) -> str:
    from src.media_storage.service import PUBLIC_HOSTS

    if provider == "cloudflare_tunnel":
        from src.media_storage.cloudflare_tunnel import CloudflareTunnelMediaProvider

        return CloudflareTunnelMediaProvider.name
    host = PUBLIC_HOSTS.get(provider)
    if host:
        return f"temporary PUBLIC upload to {host} (third-party host; the video leaves this computer)"
    return "temporary PRIVATE object storage (S3) + presigned HTTPS URL"


def delivery_provider(size: int | None = None, needs_cover: bool = False) -> str | None:
    """Name of the provider that will deliver a video of ``size`` bytes (no network). None = none can."""
    settings = StorageSettings.from_env()
    if settings.provider != "auto":
        return settings.provider
    if size is None:
        return None
    try:
        return build_router(settings).select(size, needs_cover).name
    except StorageNotConfiguredError:
        return None


def public_host_name(size: int | None = None) -> str | None:
    """Display name of the PUBLIC host that will (or, for auto without a size, may) receive the video."""
    from src.media_storage.service import PUBLIC_HOSTS

    settings = StorageSettings.from_env()
    if settings.provider == "auto":
        if size is None:
            return PUBLIC_HOSTS["tempfile"]
        try:
            return PUBLIC_HOSTS.get(build_router(settings).select(size).name)
        except StorageNotConfiguredError:
            return None
    return PUBLIC_HOSTS.get(settings.provider)
