"""Finds content packages and the files in them. No validation of file contents here."""

from pathlib import Path

from src.content.models import (
    CAPTION_FILE,
    COVER_EXTENSIONS,
    PARTIAL_SUFFIXES,
    TITLE_FILE,
    VIDEO_EXTENSIONS,
    ContentPackage,
)


def is_safe_name(name: str) -> bool:
    """A package directory name: no path separators, no traversal, not hidden."""
    return bool(name) and name not in (".", "..") and not name.startswith(".") and "/" not in name and "\\" not in name


class ContentDetector:
    """Scans a stage directory (e.g. content/incoming) for package folders."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def scan(self, stage: str = "incoming") -> list[ContentPackage]:
        stage_dir = self.root / stage
        if not stage_dir.is_dir():
            return []
        packages = []
        for entry in sorted(stage_dir.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and not entry.is_symlink() and is_safe_name(entry.name):
                packages.append(self.detect(entry, stage))
        return packages

    def detect(self, package_dir: Path, stage: str) -> ContentPackage:
        package = ContentPackage(content_id=package_dir.name, package_path=package_dir, stage=stage)
        videos, captions, covers = [], [], []
        for f in sorted(package_dir.iterdir(), key=lambda p: p.name.lower()):
            name = f.name.lower()
            if f.is_symlink():
                # Never follow links: they could point outside the content root.
                package.ignored_files.append(f.name)
                continue
            if not f.is_file():
                continue
            if name.endswith(PARTIAL_SUFFIXES):
                package.partial_files.append(f.name)
            elif f.suffix.lower() in VIDEO_EXTENSIONS:
                videos.append(f)
            elif name == CAPTION_FILE or (f.stem.lower().startswith("caption") and f.suffix.lower() == ".txt"):
                captions.append(f)
            elif f.stem.lower() == "cover" and f.suffix.lower() in COVER_EXTENSIONS:
                covers.append(f)
            elif name == TITLE_FILE:
                pass  # read by the validator
            else:
                package.ignored_files.append(f.name)

        errors = package.validation_errors
        if not videos:
            errors.append(f"{package.content_id} has no video file (.mp4, .mov or .webm).")
        elif len(videos) > 1:
            errors.append(f"{package.content_id} contains multiple video files: {', '.join(v.name for v in videos)}.")
        else:
            package.video_path = videos[0]

        if not captions:
            errors.append(f"{package.content_id} has no {CAPTION_FILE}.")
        elif len(captions) > 1:
            errors.append(f"{package.content_id} contains multiple caption files: {', '.join(c.name for c in captions)}.")
        else:
            package.caption_path = captions[0]

        if len(covers) > 1:
            errors.append(f"{package.content_id} contains multiple cover images: {', '.join(c.name for c in covers)}.")
        elif covers:
            package.cover_path = covers[0]

        if package.ignored_files:
            package.validation_warnings.append("Ignored files: " + ", ".join(package.ignored_files))
        return package
