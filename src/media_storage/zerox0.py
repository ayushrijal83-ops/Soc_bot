"""0x0.st ("The Null Pointer") temporary PUBLIC file host as a MediaSourceProvider.

Verified from https://0x0.st (2026-09-25):
    upload   POST https://0x0.st  multipart/form-data: file=<data>, secret= (hard-to-guess URL),
             expires=<hours>  ->  body: file URL; header X-Token: management token (only for new files)
    delete   POST <file URL>  form: token=<X-Token>, delete=
    limits   max 512 MiB; kept 30 days..1 year by size unless ``expires`` is shorter;
             expired files are removed within about a minute
    rules    unique client user agent (no browser impersonation; blocked clients get HTTP 418);
             users must be told it is a public host with no privacy guarantees; the ToS forbids
             piracy, backups and "other automated mass uploads"; uploader IP + UA are stored

Unlike S3 there is nothing private here: anyone with the URL can download the video until it is
deleted or expires. The management token lives only in memory (MediaHandle, repr hidden).
"""

import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from src.media_storage.base import UPLOAD_EXTENSIONS as FILE_EXTENSIONS
from src.media_storage.base import (
    MediaStorageError,
    check_upload_file,
    is_public_https_host,
)
from src.media_storage.provider import MediaHandle, MediaSourceProvider

log = logging.getLogger("soc_bot.media")

USER_AGENT = "Soc_bot/1.0 (+https://github.com/ayushrijal83-ops/Soc_bot)"
MAX_BYTES = 512 * 1024 * 1024


class ZeroX0MediaStorage(MediaSourceProvider):
    name = "temporary PUBLIC upload to 0x0.st (third-party file host; the video leaves this computer)"
    max_file_size = MAX_BYTES

    def __init__(self, base_url: str = "https://0x0.st", expires_hours: int = 1, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.expires_hours = expires_hours
        self.client = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=20.0))
        self.headers = {"User-Agent": USER_AGENT}

    # --- MediaSourceProvider -------------------------------------------------------------

    def prepare(self, video_path: Path, content_type: str) -> MediaHandle:
        path = Path(video_path)
        if path.is_file() and path.stat().st_size > MAX_BYTES:
            raise MediaStorageError("0x0.st accepts files up to 512 MiB")
        check_upload_file(path, content_type, MAX_BYTES, "0x0.st")

        log.info("Uploading temporary media to 0x0.st (public host).")
        try:
            with open(path, "rb") as f:  # streamed by httpx's multipart encoder
                response = self.client.post(
                    self.base_url,
                    headers=self.headers,
                    # Generic name: the local filename never leaves the machine.
                    files={"file": ("video" + FILE_EXTENSIONS[content_type], f, content_type)},
                    data={"secret": "", "expires": str(self.expires_hours)},
                )
        except OSError as e:
            raise MediaStorageError(f"Temporary media upload failed: local file unreadable ({type(e).__name__})") from e
        except httpx.TimeoutException as e:
            raise MediaStorageError("Temporary media upload to 0x0.st timed out", retryable=True) from e
        except httpx.TransportError as e:
            raise MediaStorageError(f"Temporary media upload to 0x0.st failed: network error ({type(e).__name__})",
                                    retryable=True) from e

        if response.status_code != 200:
            raise self._http_error("Temporary media upload to 0x0.st", response)

        url = (response.text or "").strip().splitlines()[0].strip() if (response.text or "").strip() else ""
        if not self._is_own_url(url):
            raise MediaStorageError("0x0.st returned an unexpected response (no file URL)")
        handle = MediaHandle(path, content_type, object_key=urlparse(url).path.lstrip("/"),
                             public_url=url, token=response.headers.get("X-Token"))
        if not handle.token:
            log.warning("0x0.st returned no management token (file already existed); it cannot be deleted early "
                        "and expires on 0x0.st's own schedule.")

        # Confirm the URL really serves the file before handing it to Instagram.
        try:
            probe = self.client.head(url, headers=self.headers, follow_redirects=True)
            reachable = probe.status_code in (200, 206)
        except httpx.HTTPError:
            reachable = False
        if not reachable:
            self.cleanup(handle)
            raise MediaStorageError("Uploaded media is not reachable on 0x0.st yet", retryable=True)
        return handle

    def get_public_url(self, handle: MediaHandle) -> str:
        if handle.cleaned or not handle.public_url:
            raise MediaStorageError("Temporary media was already cleaned up")
        return handle.public_url

    def cleanup(self, handle: MediaHandle | None) -> None:
        if handle is None or handle.cleaned:
            return
        handle.cleaned = True
        if not handle.token:
            log.info("Temporary media has no management token; it expires automatically.")
            return
        try:
            response = self.client.post(handle.public_url, headers=self.headers,
                                        data={"token": handle.token, "delete": ""})
            if response.status_code == 200:
                log.info("Temporary media deleted from 0x0.st.")
            else:
                log.warning("Could not delete temporary media from 0x0.st (HTTP %s); it expires within %s h.",
                            response.status_code, self.expires_hours)
        except httpx.HTTPError as e:
            log.warning("Could not delete temporary media from 0x0.st (%s); it expires within %s h.",
                        type(e).__name__, self.expires_hours)
        finally:
            handle.token = None

    def health_check(self) -> str:
        """Reachability only (GET the documentation page). Nothing is uploaded."""
        try:
            response = self.client.get(self.base_url, headers=self.headers)
        except httpx.HTTPError as e:
            raise MediaStorageError(f"0x0.st is not reachable ({type(e).__name__})", retryable=True) from e
        if response.status_code == 418 or response.status_code == 403:
            raise MediaStorageError(f"0x0.st refuses this client (HTTP {response.status_code}): the user agent or IP "
                                    "may be blocked")
        if response.status_code != 200:
            raise MediaStorageError(f"0x0.st answered HTTP {response.status_code}")
        return ("0x0.st is reachable. It is an anonymous public file host, so this check cannot confirm that "
                "uploads will be accepted (the first real publish will).")

    # --- helpers -------------------------------------------------------------------------

    def _is_own_url(self, url: str) -> bool:
        return is_public_https_host(url) and urlparse(url).hostname == urlparse(self.base_url).hostname \
            and len(urlparse(url).path) > 1

    @staticmethod
    def _http_error(action: str, response: httpx.Response) -> MediaStorageError:
        status = response.status_code
        if status in (403, 418):
            detail = "refused (the user agent or IP may be blocked by 0x0.st)"
        elif status == 413:
            detail = "file too large for 0x0.st"
        else:
            detail = "rejected"
        # Response bodies are not echoed (could contain anything); status is enough.
        return MediaStorageError(f"{action} {detail} (HTTP {status})", retryable=status >= 500 or status == 429)
