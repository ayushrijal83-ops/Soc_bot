"""Storage settings, provider factory and the temporary-media lifecycle helper."""

import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlparse

from src.media_storage.base import ObjectStorage, StorageNotConfiguredError

# "s3": private bucket (AWS S3, R2 / MinIO via MEDIA_STORAGE_ENDPOINT). "0x0": PUBLIC temporary host 0x0.st.
SUPPORTED_PROVIDERS = ("s3", "0x0", "tempfile", "cloudflare_tunnel", "auto")
# Providers that put the video on a PUBLIC third-party host (display name shown to the user).
PUBLIC_HOSTS = {"0x0": "0x0.st", "tempfile": "TempFile.org"}
DEFAULT_TTL = 900
EXTENSIONS = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}


@dataclass
class StorageSettings:
    provider: str
    bucket: str
    region: str
    endpoint: str
    access_key: str
    secret_key: str
    ttl: int
    delete_after_publish: bool
    zerox0_url: str = "https://0x0.st"
    zerox0_expires_hours: int = 1
    tempfile_expiry_hours: int = 1
    auto_margin_bytes: int = 1_000_000
    cloudflared_path: str = "cloudflared"
    tunnel_startup_timeout: float = 90
    tunnel_token_bytes: int = 16
    tunnel_host: str = "127.0.0.1"

    @classmethod
    def from_env(cls) -> "StorageSettings":
        env = os.environ.get
        try:
            ttl = int(env("MEDIA_STORAGE_PRESIGNED_URL_TTL") or DEFAULT_TTL)
        except ValueError:
            ttl = -1
        try:
            expires_hours = int(env("MEDIA_STORAGE_0X0_EXPIRES_HOURS") or 1)
        except ValueError:
            expires_hours = -1
        try:
            tempfile_hours = int(env("MEDIA_STORAGE_TEMPFILE_EXPIRY_HOURS") or 1)
        except ValueError:
            tempfile_hours = -1
        try:
            margin = int(float(env("MEDIA_STORAGE_AUTO_MARGIN_MB") or 1) * 1_000_000)
        except ValueError:
            margin = -1
        try:
            tunnel_timeout = float(env("CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS") or 90)
        except ValueError:
            tunnel_timeout = -1
        try:
            token_bytes = int(env("CLOUDFLARE_MEDIA_TOKEN_BYTES") or 16)
        except ValueError:
            token_bytes = -1
        return cls(
            provider=(env("MEDIA_STORAGE_PROVIDER") or "s3").strip().lower(),
            bucket=(env("MEDIA_STORAGE_BUCKET") or "").strip(),
            region=(env("MEDIA_STORAGE_REGION") or "").strip(),
            endpoint=(env("MEDIA_STORAGE_ENDPOINT") or "").strip(),
            access_key=(env("MEDIA_STORAGE_ACCESS_KEY") or "").strip(),
            secret_key=(env("MEDIA_STORAGE_SECRET_KEY") or "").strip(),
            ttl=ttl,
            delete_after_publish=(env("MEDIA_STORAGE_DELETE_AFTER_PUBLISH") or "true").strip().lower()
            not in ("0", "false", "no", "off"),
            zerox0_url=(env("MEDIA_STORAGE_0X0_URL") or "https://0x0.st").strip(),
            zerox0_expires_hours=expires_hours,
            tempfile_expiry_hours=tempfile_hours,
            auto_margin_bytes=margin,
            cloudflared_path=(env("CLOUDFLARED_PATH") or "cloudflared").strip(),
            tunnel_startup_timeout=tunnel_timeout,
            tunnel_token_bytes=token_bytes,
            tunnel_host=(env("CLOUDFLARE_MEDIA_HOST") or "127.0.0.1").strip(),
        )

    def problems(self) -> list[str]:
        """Configuration problems (no network). Never includes secret values."""
        issues = []
        if self.provider not in SUPPORTED_PROVIDERS:
            return [(f"MEDIA_STORAGE_PROVIDER={self.provider!r} is not supported "
                     "(use: auto, tempfile, cloudflare_tunnel, s3 or 0x0)")]
        if self.provider == "auto":
            # Size-dependent checks happen per video (router); here only the always-used parts.
            issues = []
            if self.tempfile_expiry_hours not in (1, 6, 24, 48):
                issues.append("MEDIA_STORAGE_TEMPFILE_EXPIRY_HOURS must be 1, 6, 24 or 48 (TempFile.org's allowed values)")
            if not 0 <= self.auto_margin_bytes <= 50_000_000:
                issues.append("MEDIA_STORAGE_AUTO_MARGIN_MB must be 0..50")
            return issues
        if self.provider == "cloudflare_tunnel":
            from src.media_storage.cloudflare_tunnel import (
                INSTALL_HINT,
                LOOPBACK_HOSTS,
                find_cloudflared,
            )

            if self.tunnel_host not in LOOPBACK_HOSTS:
                issues.append("CLOUDFLARE_MEDIA_HOST must be 127.0.0.1 (the video is never served on other interfaces)")
            if not 5 <= self.tunnel_startup_timeout <= 300:
                issues.append("CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT_SECONDS must be 5..300")
            if not 16 <= self.tunnel_token_bytes <= 64:
                issues.append("CLOUDFLARE_MEDIA_TOKEN_BYTES must be 16..64")
            if find_cloudflared(self.cloudflared_path) is None:
                issues.append(f"cloudflared not found (CLOUDFLARED_PATH={self.cloudflared_path!r}). {INSTALL_HINT}")
            return issues
        if self.provider == "tempfile":
            if self.tempfile_expiry_hours not in (1, 6, 24, 48):
                return ["MEDIA_STORAGE_TEMPFILE_EXPIRY_HOURS must be 1, 6, 24 or 48 (TempFile.org's allowed values)"]
            return []
        if self.provider == "0x0":
            from src.media_storage.zerox0 import is_public_https_host

            parsed = urlparse(self.zerox0_url)
            if not is_public_https_host(self.zerox0_url) or parsed.query:
                issues.append("MEDIA_STORAGE_0X0_URL must be a public https:// URL (e.g. https://0x0.st)")
            if not 1 <= self.zerox0_expires_hours <= 24:
                issues.append("MEDIA_STORAGE_0X0_EXPIRES_HOURS must be 1..24 (uploads are temporary)")
            return issues
        missing = [name for name, value in (("MEDIA_STORAGE_BUCKET", self.bucket),
                                            ("MEDIA_STORAGE_ACCESS_KEY", self.access_key),
                                            ("MEDIA_STORAGE_SECRET_KEY", self.secret_key)) if not value]
        if missing:
            issues.append("Missing " + ", ".join(missing))
        if self.endpoint:
            parsed = urlparse(self.endpoint)
            if parsed.scheme != "https" or not parsed.hostname:
                issues.append("MEDIA_STORAGE_ENDPOINT must be an https:// URL (Instagram only fetches HTTPS media)")
            elif parsed.username or parsed.password or parsed.query:
                issues.append("MEDIA_STORAGE_ENDPOINT must not contain credentials or a query string")
        elif not self.region:
            issues.append("MEDIA_STORAGE_REGION is required for AWS S3 (or set MEDIA_STORAGE_ENDPOINT for R2/MinIO)")
        if not 60 <= self.ttl <= 7 * 24 * 3600:
            issues.append("MEDIA_STORAGE_PRESIGNED_URL_TTL must be 60..604800 seconds")
        return issues

    @property
    def configured(self) -> bool:
        return not self.problems()


def create_media_storage(settings: StorageSettings | None = None) -> ObjectStorage:
    """Provider factory. Raises StorageNotConfiguredError with an actionable message."""
    settings = settings or StorageSettings.from_env()
    problems = settings.problems()
    if problems:
        raise StorageNotConfiguredError(
            "Instagram temporary media storage is not configured: " + "; ".join(problems)
            + ". Required: MEDIA_STORAGE_BUCKET, MEDIA_STORAGE_ACCESS_KEY, MEDIA_STORAGE_SECRET_KEY "
            "(+ MEDIA_STORAGE_REGION for AWS or MEDIA_STORAGE_ENDPOINT for R2/MinIO). See docs/API_INTEGRATIONS.md."
        )
    from src.media_storage.s3 import S3ObjectStorage

    return S3ObjectStorage(settings.bucket, settings.access_key, settings.secret_key,
                           region=settings.region, endpoint=settings.endpoint)


def new_object_key(content_type: str, prefix: str = "instagram/temp") -> str:
    """Unguessable key with no filename, path or account data: instagram/temp/<random>/video.mp4."""
    return f"{prefix}/{secrets.token_urlsafe(24)}/video{EXTENSIONS.get(content_type, '.bin')}"
