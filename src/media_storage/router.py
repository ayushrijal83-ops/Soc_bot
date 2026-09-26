"""Size-based routing across media providers (MEDIA_STORAGE_PROVIDER=auto).

The router is itself a MediaSourceProvider, so consumers (Instagram) don't change. At prepare()
time it picks the FIRST configured provider in AUTO_ORDER whose declared ``max_file_size`` fits
the file (minus a safety margin), and remembers that choice in the handle. get_public_url() and
cleanup() always go to the provider that created the object.

Routing is deterministic and capability-based. A provider that FAILS is reported as failed; the
router never retries the upload on another provider, so media never moves somewhere unexpected.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.media_storage.base import MediaStorageError, StorageNotConfiguredError
from src.media_storage.provider import MediaHandle, MediaSourceProvider

log = logging.getLogger("soc_bot.media")

# TempFile needs no setup; the Cloudflare Quick Tunnel (no size limit, nothing uploaded) takes what
# TempFile can't; S3 is only reached when cloudflared is unavailable. (0x0: uploads disabled, never AUTO.)
AUTO_ORDER = ("tempfile", "cloudflare_tunnel", "s3")


@dataclass
class Tier:
    """One routable provider: a lazy factory (not built until chosen) and its declared limit."""

    name: str
    label: str
    max_file_size: int | None       # None = no application limit
    problems: Callable[[], list[str]]
    factory: Callable[[], MediaSourceProvider]
    supports_cover: bool = False  # can serve a cover image next to the video


def fmt_mb(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


class MediaStorageRouter(MediaSourceProvider):
    name = "automatic by video size"

    def __init__(self, tiers: list[Tier], margin_bytes: int = 1_000_000):
        self.tiers = tiers
        self.margin_bytes = margin_bytes

    # --- selection (no network) ------------------------------------------------------------

    def fits(self, tier: Tier, size: int) -> bool:
        # Margin: multipart framing adds bytes on top of the file, so stay clearly below the limit.
        return tier.max_file_size is None or size <= tier.max_file_size - self.margin_bytes

    def select(self, size: int, needs_cover: bool = False) -> Tier:
        """The provider that WOULD handle a file of ``size`` bytes (and its cover image, if any).

        Raises StorageNotConfiguredError. A provider that can't deliver the cover is never chosen for a
        job that has one: the cover is never silently dropped."""
        reasons = []
        for tier in self.tiers:
            if not self.fits(tier, size):
                reasons.append(f"{tier.label} takes up to {fmt_mb(tier.max_file_size - self.margin_bytes)}")
                continue
            if needs_cover and not tier.supports_cover:
                reasons.append(f"{tier.label} can't deliver a cover image")
                continue
            problems = tier.problems()
            if problems:
                reasons.append(f"{tier.label} is not configured ({'; '.join(problems)})")
                continue
            return tier
        raise StorageNotConfiguredError(
            f"No media provider can take this {fmt_mb(size)} video: " + "; ".join(reasons)
            + ". For larger videos install cloudflared (Cloudflare Quick Tunnel) or configure S3 "
            "(MEDIA_STORAGE_BUCKET/ACCESS_KEY/SECRET_KEY + REGION or ENDPOINT)."
        )

    def problems_for(self, size: int, needs_cover: bool = False) -> list[str]:
        try:
            self.select(size, needs_cover)
        except StorageNotConfiguredError as e:
            return [str(e)]
        return []

    # --- MediaSourceProvider -----------------------------------------------------------------

    supports_cover = True  # routes cover jobs to a tier that supports covers

    def prepare(self, video_path: Path, content_type: str, cover_path: Path | None = None) -> MediaHandle:
        path = Path(video_path)
        if not path.is_file():
            raise MediaStorageError(f"Video file not found: {path.name}")
        tier = self.select(path.stat().st_size, needs_cover=cover_path is not None)
        log.info("Media routing: %s video%s -> %s.", fmt_mb(path.stat().st_size),
                 " + cover" if cover_path is not None else "", tier.label)
        provider = tier.factory()
        if cover_path is not None:
            handle = provider.prepare(path, content_type, cover_path=cover_path)
        else:
            handle = provider.prepare(path, content_type)
        handle.provider = provider  # every later call goes to the provider that owns the object
        return handle

    def get_public_url(self, handle: MediaHandle) -> str:
        return self._owner(handle).get_public_url(handle)

    def is_alive(self, handle: MediaHandle) -> bool:
        return self._owner(handle).is_alive(handle)

    def cleanup(self, handle: MediaHandle | None) -> None:
        if handle is None:
            return
        self._owner(handle).cleanup(handle)

    def health_check(self) -> str:
        """Each configured tier's own upload-free check."""
        lines = []
        for tier in self.tiers:
            problems = tier.problems()
            limit = f"up to {fmt_mb(tier.max_file_size - self.margin_bytes)}" if tier.max_file_size else "no size limit"
            if problems:
                lines.append(f"{tier.label} ({limit}): NOT configured: {'; '.join(problems)}")
                continue
            lines.append(f"{tier.label} ({limit}): {tier.factory().health_check()}")
        if not any("NOT configured" not in line for line in lines):
            raise StorageNotConfiguredError("No media provider is configured: " + " | ".join(lines))
        return "\n".join(lines)

    @staticmethod
    def _owner(handle: MediaHandle) -> MediaSourceProvider:
        if handle.provider is None:
            raise MediaStorageError("Media handle has no owning provider")
        return handle.provider
