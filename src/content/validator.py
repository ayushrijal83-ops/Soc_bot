"""Validates a detected content package. Generic checks only; platform rules stay in adapters."""

import time
from collections.abc import Callable
from pathlib import Path

from src.content.models import TITLE_FILE, VIDEO_URL_FILE, ContentPackage
from src.core.validation import validate_video_file

MAX_COVER_BYTES = 50 * 1024 * 1024  # generous generic ceiling; platforms apply their own limits

# Magic bytes, so a renamed text file isn't accepted as an image (no Pillow needed).
_IMAGE_SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
}


def image_problem(path: Path) -> str | None:
    """None if the file looks like the image its extension claims, else a reason."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return f"{path.name} could not be read."
    if size == 0:
        return f"{path.name} is empty."
    if size > MAX_COVER_BYTES:
        return f"{path.name} is larger than 50 MB."
    suffix = path.suffix.lower()
    if suffix == ".webp":
        ok = head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    else:
        ok = any(head.startswith(sig) for sig in _IMAGE_SIGNATURES.get(suffix, ()))
    return None if ok else f"{path.name} could not be read as a valid image."


def _read_text(path: Path) -> str:
    # utf-8-sig drops a BOM only; everything else (emoji, hashtags, line breaks) is kept as-is.
    return path.read_text(encoding="utf-8-sig")


class ContentValidator:
    def __init__(self, probe_media: bool = True):
        self.probe_media = probe_media

    def validate(self, package: ContentPackage) -> ContentPackage:
        """Fill caption/title/url and validation status. Detection errors are kept."""
        errors, warnings = package.validation_errors, package.validation_warnings

        if package.video_path is not None:
            result = validate_video_file(package.video_path, probe=self.probe_media)
            errors.extend(f"{package.content_id}: {e}" for e in result.errors)
            warnings.extend(result.warnings)

        if package.caption_path is not None:
            try:
                text = _read_text(package.caption_path)
            except UnicodeDecodeError:
                errors.append(f"{package.content_id}: caption.txt is not valid UTF-8.")
            except OSError:
                errors.append(f"{package.content_id}: caption.txt could not be read.")
            else:
                if not text.strip():
                    errors.append(f"{package.content_id}: caption.txt is empty.")
                package.caption_text = text  # never modified or truncated

        if package.cover_path is not None:
            problem = image_problem(package.cover_path)
            if problem:
                warnings.append(f"{problem} The cover will not be used.")
                package.cover_path = None

        for filename, attr in ((TITLE_FILE, "title"), (VIDEO_URL_FILE, "video_url")):
            path = package.package_path / filename
            if path.is_file() and not path.is_symlink():
                try:
                    value = _read_text(path).strip()
                except (OSError, UnicodeDecodeError):
                    errors.append(f"{package.content_id}: {filename} could not be read as UTF-8.")
                    continue
                setattr(package, attr, value or None)

        if package.partial_files:
            package.validation_status = "copying"
        else:
            package.validation_status = "invalid" if errors else "valid"
        return package


def snapshot(package_dir: Path) -> dict[str, tuple[int, int]]:
    """(size, mtime_ns) of every regular file in the package."""
    state = {}
    for f in package_dir.iterdir():
        if f.is_file() and not f.is_symlink():
            st = f.stat()
            state[f.name] = (st.st_size, st.st_mtime_ns)
    return state


def is_stable(package_dir: Path, seconds: float, sleep: Callable[[float], None] = time.sleep,
              now: Callable[[], float] = time.time) -> bool:
    """True when no file changed during the stability window (guards against half-copied videos).

    If every file was last modified more than ``seconds`` ago, no waiting is needed.
    """
    before = snapshot(package_dir)
    if not before:
        return False
    newest = max(mtime for _, mtime in before.values()) / 1e9
    if now() - newest >= seconds:
        return True
    sleep(seconds)
    return snapshot(package_dir) == before
