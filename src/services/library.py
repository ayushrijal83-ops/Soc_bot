"""Links, content inbox and settings services for the UI (read-mostly, no publishing logic)."""

from __future__ import annotations

import os
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from src.core.published_links import (
    PLATFORMS,
    PublishedLinks,
    copy_to_clipboard,
    is_permanent_platform_url,
)


class LinkService:
    """Permanent published links (JSON is the source of truth). Temporary URLs can never pass through here."""

    def __init__(self, links: PublishedLinks, copier=copy_to_clipboard, opener=webbrowser.open):
        self.links = links
        self.copier = copier
        self.opener = opener

    def records(self, platform: str, search: str = "") -> list[dict]:
        needle = search.strip().lower()
        rows = list(reversed(self.links.records(platform)))  # newest first
        return [r for r in rows if not needle or needle in " ".join(str(v) for v in r.values()).lower()]

    def counts(self) -> dict[str, int]:
        return {p: len(self.links.records(p)) for p in PLATFORMS}

    def copy(self, platform: str, urls: list[str]) -> int:
        """Copy permanent URLs (one per line). Anything that isn't a permanent platform URL is refused."""
        safe = [u for u in urls if is_permanent_platform_url(platform, u)]
        if not safe:
            raise ValueError("No permanent links to copy")
        self.copier("\r\n".join(safe) if len(safe) > 1 else safe[0])
        return len(safe)

    def open(self, platform: str, url: str) -> None:
        if not is_permanent_platform_url(platform, url):
            raise ValueError("Only permanent platform links can be opened")
        self.opener(url)

    def text_file(self, platform: str) -> Path:
        return self.links.text_path(platform)


class ContentService:
    """Content inbox overview: stage counts and scanned packages (scan only; nothing is published here)."""

    STAGES = ("incoming", "publishing", "published", "failed", "archive")

    def __init__(self, intake):
        self.intake = intake

    def stage_counts(self) -> dict[str, int]:
        counts = {}
        for stage in self.STAGES:
            folder = self.intake.manager.stage_dir(stage)
            counts[stage] = sum(1 for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else 0
        return counts

    def entries(self) -> list[dict]:
        rows = []
        for entry in self.intake.scan():
            package = entry.package
            rows.append({"name": package.content_id, "status": entry.status,
                         "video": package.video_path.name if package.video_path else "-",
                         "cover": package.cover_path.name if package.cover_path else "-",
                         "destinations": len(entry.destinations),
                         "problems": "; ".join(entry.problems + package.validation_errors)[:120]})
        return rows

    @property
    def root(self) -> Path:
        return self.intake.root


@dataclass
class MediaStatus:
    name: str
    state: str      # available | not_configured | attention
    text: str


class SettingsService:
    """Current settings (never credentials) and the two Instagram batch settings the UI may change."""

    EDITABLE: ClassVar[dict[str, tuple]] = {
        "INSTAGRAM_MAX_CONCURRENT_PUBLISHES": ("Instagram concurrency", "Maximum number of Instagram jobs active at once.",
                                               1, 20, 5),
        "INSTAGRAM_FAILURE_RETRY_DELAY_SECONDS": ("Failure retry delay (s)",
                                                  "Pause before failed Instagram jobs get their one automatic retry.",
                                                  0, 300, 5),
    }

    def __init__(self, env_file: Path, auth_manager=None):
        self.env_file = env_file
        self.auth = auth_manager

    def value(self, key: str) -> int:
        _label, _, low, high, default = self.EDITABLE[key]
        try:
            value = int(float(os.environ.get(key) or default))
        except ValueError:
            return default
        return value if low <= value <= high else default

    def save(self, key: str, value: str) -> int:
        """Validate and persist one editable setting to .env (only this key) and apply it to this session."""
        from dotenv import set_key

        label, _, low, high, _ = self.EDITABLE[key]
        try:
            number = int(value.strip())
        except ValueError as e:
            raise ValueError(f"{label} must be a whole number") from e
        if not low <= number <= high:
            raise ValueError(f"{label} must be between {low} and {high}")
        if self.env_file.exists():
            set_key(str(self.env_file), key, str(number), quote_mode="never")
        os.environ[key] = str(number)
        return number

    def media_status(self) -> list[MediaStatus]:
        """Local checks only (no network): what each media provider needs."""
        from src.media_storage import StorageSettings
        from src.media_storage.cloudflare_tunnel import find_cloudflared

        settings = StorageSettings.from_env()
        rows = [MediaStatus("Routing", "available", f"MEDIA_STORAGE_PROVIDER = {settings.provider}")]
        tunnel = find_cloudflared(settings.cloudflared_path)
        rows.append(MediaStatus("Cloudflare Quick Tunnel", "available" if tunnel else "not_configured",
                                "cloudflared found (one shared tunnel per batch)" if tunnel
                                else "cloudflared not found: install it or set CLOUDFLARED_PATH"))
        rows.append(MediaStatus("TempFile.org", "available", "public temporary host, no credentials, max 100 MB"))
        from dataclasses import replace

        s3 = not replace(settings, provider="s3").problems()
        rows.append(MediaStatus("S3", "available" if s3 else "not_configured",
                                "bucket configured" if s3 else "not configured (optional)"))
        return rows

    def oauth_status(self) -> dict[str, bool]:
        result = {}
        for platform in PLATFORMS:
            try:
                result[platform] = bool(self.auth and self.auth.is_configured(platform))
            except Exception:  # noqa: BLE001
                result[platform] = False
        return result
