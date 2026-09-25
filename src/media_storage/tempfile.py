"""TempFile.org temporary PUBLIC file host as a MediaSourceProvider.

Verified 2026-09-25 from https://tempfile.org/openapi.json (API v2.2.0) and a real probe:
    upload    POST https://tempfile.org/api/upload/local   multipart: files=<data>, expiryHours in {1,6,24,48}
              -> 200 {"success": true, "files": [{"id", "name", "size", "url", "expiryTime"}], "message"}
    file      GET  https://tempfile.org/<id>/download   raw bytes (the "url" field is an HTML landing page)
    delete    DELETE https://tempfile.org/api/file/<id>   -> 200 {"success": true}; afterwards 404
    limits    100 MB per file; 200 upload requests/hour; files expire 1-48 h after upload
    auth      none: anyone who knows a file ID can download AND delete it, so the ID is kept secret
    rules     no malware, copyrighted content without permission, adult material, automated bulk uploads

The video is publicly downloadable by anyone with its link until deleted or expired.
"""

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from src.media_storage.base import (
    UPLOAD_EXTENSIONS,
    MediaStorageError,
    check_upload_file,
    is_public_https_host,
)
from src.media_storage.provider import MediaHandle, MediaSourceProvider
from src.platforms.base import redact

log = logging.getLogger("soc_bot.media")

USER_AGENT = "Soc_bot/1.0 (+https://github.com/ayushrijal83-ops/Soc_bot)"
MAX_BYTES = 100 * 1000 * 1000  # documented "100MB"; the decimal reading is the safe (smaller) one
EXPIRY_HOURS = (1, 6, 24, 48)  # the only values the API accepts
# File IDs per the OpenAPI spec: 11 Base58 characters (v2) or legacy file_<ts>_<rand>.
_FILE_ID = re.compile(r"^(?:[1-9A-HJ-NP-Za-km-z]{11}|file_[0-9]+_[a-zA-Z0-9]+)$")


class TempFileMediaStorage(MediaSourceProvider):
    name = "temporary PUBLIC upload to TempFile.org (third-party host; the video leaves this computer)"
    max_file_size = MAX_BYTES

    def __init__(self, base_url: str = "https://tempfile.org", expiry_hours: int = 1, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.expiry_hours = expiry_hours
        self.client = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=20.0))
        self.headers = {"User-Agent": USER_AGENT}

    def prepare(self, video_path: Path, content_type: str) -> MediaHandle:
        path = Path(video_path)
        size = check_upload_file(path, content_type, MAX_BYTES, "TempFile.org")

        log.info("Uploading temporary media to TempFile.org (public host).")
        try:
            with open(path, "rb") as f:  # streamed by httpx's multipart encoder
                response = self.client.post(
                    f"{self.base_url}/api/upload/local",
                    headers=self.headers,
                    # Generic name: the local filename never leaves the machine.
                    files={"files": ("video" + UPLOAD_EXTENSIONS[content_type], f, content_type)},
                    data={"expiryHours": str(self.expiry_hours)},
                )
        except OSError as e:
            raise MediaStorageError(f"Temporary media upload failed: local file unreadable ({type(e).__name__})") from e
        except httpx.TimeoutException as e:
            raise MediaStorageError("Temporary media upload to TempFile.org timed out", retryable=True) from e
        except httpx.TransportError as e:
            raise MediaStorageError(f"Temporary media upload to TempFile.org failed: network error ({type(e).__name__})",
                                    retryable=True) from e

        body = self._json(response)
        if response.status_code != 200 or body.get("success") is not True:
            raise self._http_error("Temporary media upload to TempFile.org", response, body)

        files = body.get("files")
        entry = files[0] if isinstance(files, list) and files and isinstance(files[0], dict) else {}
        file_id = entry.get("id")
        landing = entry.get("url") or ""
        if not isinstance(file_id, str) or not _FILE_ID.match(file_id):
            raise MediaStorageError("TempFile.org returned an unexpected response (no valid file id)")
        # The response URL must point at this same service; the media URL is then built from the id
        # (never taken verbatim from the response), so a bad response can't steer Instagram elsewhere.
        if landing and not self._same_service(landing):
            raise MediaStorageError("TempFile.org returned a URL for a different host")
        media_url = f"{self.base_url}/{file_id}/download"

        # file_id doubles as the (unauthenticated) delete capability: keep it only in the hidden token field.
        handle = MediaHandle(path, content_type, public_url=media_url, token=file_id)

        try:
            probe = self.client.head(media_url, headers=self.headers, follow_redirects=False)
            reachable = probe.status_code == 200 and probe.headers.get("content-length") in (None, str(size))
            served_type = probe.headers.get("content-type", "")
        except httpx.HTTPError:
            reachable, served_type = False, ""
        if not reachable:
            self.cleanup(handle)
            raise MediaStorageError("Uploaded media is not reachable on TempFile.org yet", retryable=True)
        if served_type and not served_type.startswith("video/"):
            log.warning("TempFile.org serves the video as %s; Instagram may reject it.", served_type.split(";")[0])
        return handle

    def get_public_url(self, handle: MediaHandle) -> str:
        if handle.cleaned or not handle.public_url:
            raise MediaStorageError("Temporary media was already cleaned up")
        return handle.public_url

    def cleanup(self, handle: MediaHandle | None) -> None:
        if handle is None or handle.cleaned:
            return
        handle.cleaned = True
        file_id, handle.token = handle.token, None
        if not file_id:
            return
        try:
            response = self.client.delete(f"{self.base_url}/api/file/{file_id}", headers=self.headers)
            if response.status_code in (200, 404):  # 404: already expired/removed
                log.info("Temporary media deleted from TempFile.org.")
            else:
                log.warning("Could not delete temporary media from TempFile.org (HTTP %s); it expires within %s h.",
                            response.status_code, self.expiry_hours)
        except httpx.HTTPError as e:
            log.warning("Could not delete temporary media from TempFile.org (%s); it expires within %s h.",
                        type(e).__name__, self.expiry_hours)

    def health_check(self) -> str:
        """Reachability of the API description only. Nothing is uploaded."""
        try:
            response = self.client.get(f"{self.base_url}/openapi.json", headers=self.headers)
        except httpx.HTTPError as e:
            raise MediaStorageError(f"TempFile.org is not reachable ({type(e).__name__})", retryable=True) from e
        if response.status_code == 403:
            raise MediaStorageError("TempFile.org denies access from this IP address (HTTP 403)")
        if response.status_code != 200 or "/upload/local" not in response.text:
            raise MediaStorageError(f"TempFile.org API description unavailable (HTTP {response.status_code})")
        return ("TempFile.org is reachable (service reachable; no credentials required). This check uploads "
                "nothing, so a real upload is only confirmed by the first publish.")

    def _same_service(self, url: str) -> bool:
        return is_public_https_host(url) and urlparse(url).hostname == urlparse(self.base_url).hostname

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _http_error(action: str, response: httpx.Response, body: dict) -> MediaStorageError:
        status = response.status_code
        detail = {403: "denied from this IP address", 413: "file too large (TempFile.org max 100 MB)",
                  429: "rate limited (200 uploads/hour)"}.get(status)
        if detail is None:
            provider_message = redact(str(body.get("error") or ""), limit=120)
            detail = f"rejected: {provider_message}" if provider_message else "rejected"
        if status == 429 and response.headers.get("X-RateLimit-Reset"):
            detail += f", resets at unix time {response.headers['X-RateLimit-Reset']}"
        return MediaStorageError(f"{action} {detail} (HTTP {status})", retryable=status >= 500 or status == 429)
