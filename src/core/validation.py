"""Generic, provider-independent media validation.

Only checks that hold for every platform live here. Platform limits (duration, size,
caption length, ...) live in each platform's publisher ``validate()``.
"""

import functools
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Containers accepted by at least one supported platform (MP4/MOV everywhere; WebM on TikTok/YouTube).
SUPPORTED_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


@dataclass
class MediaInfo:
    """Facts about a local video. Probe fields are None when ffprobe is unavailable."""

    path: str
    size_bytes: int
    mime_type: str
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None


@dataclass
class ValidationResult:
    media: MediaInfo | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_video_file(path: str | os.PathLike, probe: bool = True) -> ValidationResult:
    """Check the file exists, is readable, non-empty and a supported container; probe metadata if possible."""
    p = Path(path)
    if not p.exists():
        return ValidationResult(None, [f"Video file not found: {p.name}"])
    if not p.is_file():
        return ValidationResult(None, [f"Not a regular file: {p.name}"])
    if not os.access(p, os.R_OK):
        return ValidationResult(None, [f"Video file is not readable: {p.name}"])

    size = p.stat().st_size
    if size == 0:
        return ValidationResult(None, [f"Video file is empty: {p.name}"])

    ext = p.suffix.lower()
    mime = SUPPORTED_VIDEO_TYPES.get(ext)
    if mime is None:
        guessed = mimetypes.guess_type(p.name)[0] or "unknown"
        return ValidationResult(None, [f"Unsupported video format '{ext or guessed}' (supported: MP4, MOV, WebM)"])

    media = MediaInfo(path=str(p.resolve()), size_bytes=size, mime_type=mime)
    result = ValidationResult(media)
    if probe and not _probe_into(media):
        result.warnings.append("ffprobe not available: duration/resolution not checked locally")
    return result


def _probe_into(media: MediaInfo) -> bool:
    """Fill duration/dimensions via ffprobe (no shell, fixed argv). Returns False if unavailable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    try:
        out = subprocess.run(
            [
                ffprobe, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate:format=duration",
                "-of", "json", media.path,
            ],
            capture_output=True, text=True, timeout=30, check=False,
        )
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    stream = (data.get("streams") or [{}])[0]
    media.width = stream.get("width")
    media.height = stream.get("height")
    rate = stream.get("r_frame_rate")
    if rate and "/" in rate:
        num, den = rate.split("/")
        media.frame_rate = float(num) / float(den) if float(den) else None
    duration = (data.get("format") or {}).get("duration")
    media.duration_seconds = float(duration) if duration else None
    return True


def file_checksum(path: str | os.PathLike) -> str:
    """SHA-256 of the file, streamed (large videos are never loaded whole).

    Cached per (path, size, modification time): planning checks every account against the same video,
    and hashing a 132 MB file once per account made the review step slow. A changed file is re-hashed.
    """
    stat = os.stat(path)
    return _checksum(os.path.abspath(path), stat.st_size, stat.st_mtime_ns)


@functools.lru_cache(maxsize=32)
def _checksum(path: str, size: int, mtime_ns: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# JPEG start-of-frame markers (baseline, progressive, lossless, ...): they carry the image size.
_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def jpeg_info(path: str | os.PathLike, max_bytes: int) -> tuple[list[str], tuple[int, int] | None]:
    """Structural JPEG check without third-party libraries: (problems, (width, height)).

    Checks: readable, non-empty, <= max_bytes, SOI marker, well-formed segments up to the scan,
    a frame header with a non-zero size, and the EOI marker at the end. Never modifies the file.
    """
    p = Path(path)
    name = p.name
    try:
        size = p.stat().st_size
        if size == 0:
            return [f"{name} is empty"], None
        if size > max_bytes:
            return [f"{name} is {size / 1_000_000:.1f} MB (max {max_bytes / 1_000_000:.0f} MB)"], None
        data = p.read_bytes()
    except OSError:
        return [f"{name} could not be read"], None
    if not data.startswith(b"\xff\xd8\xff"):
        return [f"{name} is not a JPEG image"], None
    if not data.rstrip(b"\x00").endswith(b"\xff\xd9"):
        return [f"{name} is incomplete or corrupt (no JPEG end marker)"], None
    i, dims = 2, None
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return [f"{name} is corrupt (bad JPEG segment)"], None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # standalone markers
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 2 or i + 2 + length > len(data):
            return [f"{name} is corrupt (truncated JPEG segment)"], None
        if marker in _SOF_MARKERS and length >= 7:
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            dims = (width, height)
        if marker == 0xDA:  # start of scan: compressed data follows
            break
        i += 2 + length
    if dims is None or 0 in dims:
        return [f"{name} is corrupt (no JPEG frame header)"], None
    return [], dims
