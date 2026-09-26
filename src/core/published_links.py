"""Published-link library: one JSON file per platform with the permanent URLs of published videos.

    content/published_links/youtube.json | instagram.json | tiktok.json   (records with metadata)
    content/published_links/youtube.txt  | instagram.txt  | tiktok.txt    (one URL per line, for pasting)

Written only after a platform confirmed a publication (the engine calls ``record`` from the
"published" transition). Only permanent URLs on the platform's own website are accepted, so
temporary media URLs (TempFile, 0x0, S3 presigned, upload sessions) can never end up here.
Not stored in the database by design.
"""

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from src.content.models import content_root

log = logging.getLogger("soc_bot.links")

PLATFORMS = ("youtube", "instagram", "tiktok")
# Permanent public pages only. Anything else (tempfile.org, 0x0.st, *.amazonaws.com,
# *.r2.cloudflarestorage.com, googleapis upload URLs, ...) is rejected.
ALLOWED_HOSTS = {
    "youtube": ("www.youtube.com", "youtube.com", "youtu.be"),
    "instagram": ("www.instagram.com", "instagram.com"),
    "tiktok": ("www.tiktok.com", "tiktok.com"),
}


class LinkError(ValueError):
    pass


# Read-modify-write of the JSON files happens under this lock (concurrent publish jobs, one process)
# plus an OS file lock on <root>/.lock (two Soc_bot processes, e.g. the menu and --scan).
_WRITE_LOCK = threading.RLock()


@contextmanager
def _process_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with open(root / ".lock", "a+b") as f:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def is_permanent_platform_url(platform: str, url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if (parsed.hostname or "").lower() not in ALLOWED_HOSTS.get(platform, ()):
        return False
    # Signed/temporary URL markers never belong in a permanent link.
    return not any(marker in (parsed.query or "") for marker in ("X-Amz-", "Signature=", "Expires=", "token="))


class PublishedLinks:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else content_root() / "published_links"

    def path(self, platform: str) -> Path:
        if platform not in PLATFORMS:
            raise LinkError(f"Unknown platform: {platform}")
        return self.root / f"{platform}.json"

    def text_path(self, platform: str) -> Path:
        return self.path(platform).with_suffix(".txt")

    def ensure_files(self) -> None:
        """Create missing JSON files and (re)build the .txt exports from the JSON records."""
        with _WRITE_LOCK, _process_lock(self.root):
            for platform in PLATFORMS:
                records = self._records(platform)
                if not self.path(platform).exists():
                    self._write(platform, records)
                else:
                    self._write_text(platform, records)

    def records(self, platform: str) -> list[dict]:
        with _WRITE_LOCK:
            return self._records(platform)

    def _records(self, platform: str) -> list[dict]:
        path = self.path(platform)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return self._quarantine(platform)
        if not isinstance(data, list):
            return self._quarantine(platform)
        return [r for r in data if isinstance(r, dict) and r.get("url")]

    def add(self, platform: str, video: str, account: str, url: str, provider_id: str,
            published_at: datetime | None = None) -> bool:
        """Append one record. Returns False if (account, provider_id) is already saved."""
        if not is_permanent_platform_url(platform, url):
            raise LinkError(f"Refusing to save a non-permanent {platform} URL")
        if not provider_id:
            raise LinkError("A provider id is required for duplicate protection")
        with _WRITE_LOCK, _process_lock(self.root):
            return self._add(platform, video, account, url, provider_id, published_at)

    def _add(self, platform: str, video: str, account: str, url: str, provider_id: str,
             published_at: datetime | None) -> bool:
        records = self._records(platform)  # re-read under the lock: another writer may have added some
        if any(r.get("account") == account and r.get("provider_id") == provider_id for r in records):
            return False
        when = published_at or datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)  # stored timestamps are UTC
        records.append({"video": video, "account": account, "url": url, "provider_id": provider_id,
                        "published_at": when.isoformat(timespec="seconds")})
        self._write(platform, records)
        return True

    # --- file safety ----------------------------------------------------------------------

    def _write(self, platform: str, records: list[dict]) -> None:
        """JSON first (the source of truth), then the derived .txt export. Both atomic."""
        self._atomic_write(self.path(platform), json.dumps(records, ensure_ascii=False, indent=2) + "\n")
        self._write_text(platform, records)

    def _write_text(self, platform: str, records: list[dict]) -> None:
        urls = [r["url"] for r in records if is_permanent_platform_url(platform, r.get("url", ""))]
        self._atomic_write(self.text_path(platform), "".join(f"{url}\n" for url in urls))

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """Write a temp file in the same folder, fsync, then os.replace (a crash never leaves half a file)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _quarantine(self, platform: str) -> list[dict]:
        """Malformed file: keep it aside (never deleted) and continue with an empty list."""
        path = self.path(platform)
        backup = path.with_name(f"{path.stem}.corrupt-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json")
        try:
            os.replace(path, backup)
            log.warning("%s was not valid JSON; kept it as %s and started a new list.", path.name, backup.name)
        except OSError:
            pass
        return []


def video_label(video_path: str) -> str:
    """Human name for a video: the file name, or the content package folder for 'video.mp4'."""
    p = Path(video_path)
    return p.parent.name if p.stem.lower() == "video" and p.parent.name else p.stem


def copy_to_clipboard(text: str, runner: Callable = subprocess.run) -> None:
    """Copy to the Windows clipboard with the built-in clip.exe (no extra dependency)."""
    if os.name != "nt":
        raise OSError("Clipboard copy is only supported on Windows")
    # Platform URLs are ASCII (non-ASCII is percent-encoded), which clip.exe copies verbatim.
    if not text.isascii():
        raise ValueError("Only ASCII links can be copied")
    runner(["clip"], input=text.encode("ascii"), check=True)
