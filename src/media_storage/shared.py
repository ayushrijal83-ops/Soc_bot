"""One media session per publishing batch: the video (+ cover) is prepared ONCE and shared by every job.

    batch starts ──► first job needing media: provider.prepare()  (ONE tunnel / ONE upload)
    later jobs   ──► same handle, same public video/cover URLs
    job ends     ──► cleanup() is a no-op (the session belongs to the batch)
    batch ends   ──► close(): ONE cleanup (tunnel + local server stopped / temporary copy deleted)

With per-job tunnels, 11+ Quick Tunnel creations in a short time made Cloudflare answer HTTP 429
(real case 2026-09-26). A batch now creates one tunnel, whatever the number of accounts.

If the prepared media dies (e.g. cloudflared exited), the next job that needs it starts ONE replacement.
A failed start is not retried by every waiting job at once: for FAILED_START_COOLDOWN seconds the same
error is returned, so a Cloudflare throttle isn't hammered.
"""

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from src.media_storage.base import MediaStorageError
from src.media_storage.provider import MediaHandle, MediaSourceProvider

log = logging.getLogger("soc_bot.media")

FAILED_START_COOLDOWN = 30.0


class SharedMediaSession(MediaSourceProvider):
    name = "shared media session (one per batch)"
    supports_cover = True

    def __init__(self, factory: Callable[[], MediaSourceProvider], clock: Callable[[], float] = time.monotonic):
        self.factory = factory
        self.clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple, tuple[MediaSourceProvider, MediaHandle]] = {}
        self._failed: dict[tuple, tuple[float, MediaStorageError]] = {}
        self._owners: dict[int, MediaSourceProvider] = {}  # id(handle) -> provider (atomic lookups)
        self.starts = 0  # underlying prepares (tunnel creations / uploads); observability and tests

    @staticmethod
    def _key(video_path, content_type: str, cover_path) -> tuple:
        cover = str(Path(cover_path).resolve()) if cover_path else None
        return str(Path(video_path).resolve()), content_type, cover

    def prepare(self, video_path: Path, content_type: str, cover_path: Path | None = None) -> MediaHandle:
        key = self._key(video_path, content_type, cover_path)
        with self._lock:  # other jobs wait for the one start instead of starting their own
            entry = self._entries.get(key)
            if entry is not None:
                provider, handle = entry
                if not handle.cleaned and provider.is_alive(handle):
                    return handle
                log.warning("Shared media session stopped unexpectedly; starting ONE replacement for this batch.")
                del self._entries[key]
                provider.cleanup(handle)
            failed = self._failed.get(key)
            if failed is not None and self.clock() - failed[0] < FAILED_START_COOLDOWN:
                raise MediaStorageError(str(failed[1]), retryable=failed[1].retryable)
            provider = self.factory()
            try:
                if cover_path is not None:
                    handle = provider.prepare(video_path, content_type, cover_path=cover_path)
                else:
                    handle = provider.prepare(video_path, content_type)
            except MediaStorageError as e:
                self._failed[key] = (self.clock(), e)
                raise
            self._failed.pop(key, None)
            self._entries[key] = (provider, handle)
            self._owners[id(handle)] = provider
            self.starts += 1
            log.info("Shared media session: ACTIVE (used by every job of this batch).")
            return handle

    def reset_failed_starts(self) -> None:
        """Forget cached start failures. Called before the automatic retry round: the round must make ONE
        real (shared) start attempt instead of inheriting an error cached < FAILED_START_COOLDOWN ago."""
        with self._lock:
            self._failed.clear()

    def get_public_url(self, handle: MediaHandle) -> str:
        return self._owner(handle).get_public_url(handle)

    def is_alive(self, handle: MediaHandle) -> bool:
        return self._owner(handle).is_alive(handle)

    def cleanup(self, handle: MediaHandle | None) -> None:
        """A job finished: nothing to do, the batch still needs the media (see close())."""

    def close(self) -> None:
        """End of the batch (after the retry round): release everything exactly once. Never raises."""
        with self._lock:
            entries, self._entries = list(self._entries.values()), {}
            self._owners.clear()
        for provider, handle in entries:
            try:
                provider.cleanup(handle)
            except Exception as e:  # noqa: BLE001 - a finished batch must stay finished
                log.warning("Shared media session cleanup warning: %s", type(e).__name__)
        if entries:
            log.info("Shared media session: CLOSED.")

    def _owner(self, handle: MediaHandle) -> MediaSourceProvider:
        provider = self._owners.get(id(handle))
        if provider is None:
            raise MediaStorageError("Media handle is not part of this session (already closed?)")
        return provider
