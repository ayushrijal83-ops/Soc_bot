"""Content intake data model and settings."""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

STAGES = ("incoming", "publishing", "published", "failed", "archive")

VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
CAPTION_FILE = "caption.txt"
# Optional package files (single line each).
TITLE_FILE = "title.txt"          # YouTube title; defaults to the caption's first line
# Files that mean "still being copied/downloaded".
PARTIAL_SUFFIXES = (".part", ".partial", ".crdownload", ".download", ".tmp", ".!ut")


def content_root() -> Path:
    """CONTENT_ROOT env var (absolute, or relative to the project), default <project>/content."""
    configured = os.environ.get("CONTENT_ROOT")
    root = Path(configured) if configured else PROJECT_ROOT / "content"
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root.resolve()


def stability_seconds() -> float:
    """CONTENT_STABILITY_SECONDS: how long files must stay unchanged before a package is READY."""
    try:
        return max(0.0, float(os.environ.get("CONTENT_STABILITY_SECONDS", "3")))
    except ValueError:
        return 3.0


@dataclass
class ContentPackage:
    """One folder in the content tree. The filesystem holds the media; nothing is copied."""

    content_id: str
    package_path: Path
    stage: str
    video_path: Path | None = None
    caption_path: Path | None = None
    caption_text: str | None = None
    cover_path: Path | None = None
    title: str | None = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    validation_status: str = "unchecked"   # unchecked | valid | invalid | copying
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    ignored_files: list[str] = field(default_factory=list)
    partial_files: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.validation_status == "valid"
