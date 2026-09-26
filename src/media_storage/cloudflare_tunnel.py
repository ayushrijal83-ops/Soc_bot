"""Cloudflare Quick Tunnel as a MediaSourceProvider: the video is SERVED from this computer, not uploaded.

    local MP4 -> private HTTP server on 127.0.0.1:<random port>, one route /media/<random token>.mp4
              -> cloudflared tunnel --url http://127.0.0.1:<port>
              -> https://<random>.trycloudflare.com/media/<token>.mp4   (Instagram fetches this)
    cleanup   -> stop cloudflared + stop the server (nothing to delete anywhere: no cloud copy exists)

Quick Tunnels need no Cloudflare account, API key or domain. Cloudflare documents them as intended
for testing and development, with no uptime guarantee (https://developers.cloudflare.com/cloudflare-one/
connections/connect-networks/do-more-with-tunnels/trycloudflare/). Each prepared media object gets its
own server, token and tunnel, so concurrent jobs (two Instagram accounts) never share or break a URL.
"""

import atexit
import http.server
import ipaddress
import logging
import re
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from src.media_storage.base import (
    UPLOAD_EXTENSIONS,
    MediaStorageError,
    StorageNotConfiguredError,
    is_public_https_host,
)
from src.media_storage.provider import MediaHandle, MediaSourceProvider

log = logging.getLogger("soc_bot.media")
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines contain request URLs (the token)

LOOPBACK_HOSTS = ("127.0.0.1",)
INSTALL_HINT = ("Install it with `winget install --id Cloudflare.cloudflared` (or download it from "
                "https://github.com/cloudflare/cloudflared/releases) and/or set CLOUDFLARED_PATH.")
# Public DoH resolvers for the readiness check, alternated: independent caches, so one cached "no such host"
# (trycloudflare.com negative TTL: 60 s) doesn't stall the check.
DOH_URLS = ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve")
FIRST_LOOKUP_DELAY = 5  # asking before the record exists is what creates a cached "no such host"
# Quick Tunnel names are hyphenated random words (e.g. quiet-river-demo-1234). Requiring a hyphen skips
# service hosts cloudflared also prints, such as https://api.trycloudflare.com (the quick-tunnel API).
_TUNNEL_URL = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com(?![a-z0-9.-])")
CHUNK = 1024 * 1024

# Children still running if Python exits without cleanup (e.g. an unhandled exception at exit).
_LIVE: set[subprocess.Popen] = set()


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    _LIVE.discard(process)


# ponytail: atexit covers normal exits and Ctrl+C (the console also sends Ctrl+C to cloudflared);
# a hard kill of Python can still orphan cloudflared. Add a Windows Job Object if that happens in practice.
atexit.register(lambda: [_terminate(p) for p in list(_LIVE)])


def find_cloudflared(executable: str) -> str | None:
    """Full path of the cloudflared executable, or None. Local lookup only (no download, no network)."""
    if Path(executable).is_file():
        return str(Path(executable))
    return shutil.which(executable)


def parse_tunnel_url(line: str) -> str | None:
    match = _TUNNEL_URL.search(line)
    return match.group(0) if match and is_public_https_host(match.group(0)) else None


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Single ``bytes=a-b`` / ``bytes=a-`` / ``bytes=-n`` range -> inclusive (start, end); None = invalid."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not match or match.groups() == ("", ""):
        return None
    first, last = match.groups()
    if first == "":
        start, end = max(0, size - int(last)), size - 1
    else:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    return (start, end) if start <= end < size else None


def make_handler(routes: dict[str, tuple[Path, str]]):
    """Request handler that serves only the prepared files, each at exactly one random path.

    ``routes`` maps "/media/<token>.<ext>" to (local file, content type). Everything else is 404.
    """

    class PreparedFilesHandler(http.server.BaseHTTPRequestHandler):
        server_version = "Soc_bot"
        sys_version = ""

        def do_HEAD(self):
            self._serve(body=False)

        def do_GET(self):
            self._serve(body=True)

        def _serve(self, body: bool) -> None:
            entry = routes.get(self.path.split("?", 1)[0])  # exact match: no traversal, no listing
            if entry is None:
                self.send_error(404)
                return
            path, content_type = entry
            size = path.stat().st_size
            start, end, status = 0, size - 1, 200
            if self.headers.get("Range"):
                parsed = _parse_range(self.headers["Range"], size)
                if parsed is None:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                (start, end), status = parsed, 206
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not body:
                return
            try:
                with open(path, "rb") as f:  # streamed in 1 MiB chunks, never read whole
                    f.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = f.read(min(CHUNK, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # client went away (Meta may probe and disconnect)

        def log_message(self, format, *args):  # never log request paths (they hold the token)
            pass

    return PreparedFilesHandler


@dataclass
class TunnelSession:
    """Everything one prepared media object keeps running. In memory only."""

    server: http.server.ThreadingHTTPServer
    thread: threading.Thread
    process: subprocess.Popen | None = None

    def close(self) -> None:
        errors = []
        if self.process is not None:
            try:
                _terminate(self.process)
            except Exception as e:  # noqa: BLE001 - keep going: the server must stop too
                errors.append(f"cloudflared: {type(e).__name__}")
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception as e:  # noqa: BLE001
            errors.append(f"server: {type(e).__name__}")
        if errors:
            raise MediaStorageError("Tunnel cleanup incomplete (" + ", ".join(errors) + ")")


class CloudflareTunnelMediaProvider(MediaSourceProvider):
    name = ("Cloudflare Quick Tunnel to this computer (temporary public URL, nothing is uploaded; "
            "runs only while Instagram fetches the video)")
    max_file_size = None  # streamed from disk; Instagram's own 300 MB limit is checked by the adapter
    supports_cover = True  # the cover is served next to the video, on the same server and tunnel

    def __init__(self, executable: str = "cloudflared", startup_timeout: float = 90, token_bytes: int = 16,
                 host: str = "127.0.0.1", popen=subprocess.Popen, client: httpx.Client | None = None,
                 verify_public: bool = True):
        if host not in LOOPBACK_HOSTS:
            raise StorageNotConfiguredError("CLOUDFLARE_MEDIA_HOST must be 127.0.0.1 (loopback only)")
        self.executable = executable
        self.startup_timeout = startup_timeout
        self.token_bytes = token_bytes
        self.host = host
        self.popen = popen
        self.client = client or httpx.Client(timeout=httpx.Timeout(15.0))
        self.verify_public = verify_public

    # --- lifecycle --------------------------------------------------------------------------

    def prepare(self, video_path: Path, content_type: str, cover_path: Path | None = None) -> MediaHandle:
        path = Path(video_path)
        if not path.is_file():
            raise MediaStorageError(f"Video file not found: {path.name}")
        if path.stat().st_size == 0:
            raise MediaStorageError(f"Video file is empty: {path.name}")
        if content_type not in UPLOAD_EXTENSIONS:
            raise MediaStorageError(f"Unsupported video type: {content_type}")
        exe = find_cloudflared(self.executable)
        if exe is None:
            raise StorageNotConfiguredError(f"cloudflared not found ({self.executable!r}). {INSTALL_HINT}")

        route = f"/media/{secrets.token_hex(self.token_bytes)}{UPLOAD_EXTENSIONS[content_type]}"
        routes = {route: (path, content_type)}
        cover_route = None
        if cover_path is not None:
            cover = Path(cover_path)
            if not cover.is_file() or cover.stat().st_size == 0:
                raise MediaStorageError(f"Cover image not found or empty: {cover.name}")
            # Same local file for every job; only this job's random path points at it.
            cover_route = f"/media/{secrets.token_hex(self.token_bytes)}.jpg"
            routes[cover_route] = (cover, "image/jpeg")
        session = self._start_server(routes)
        handle = MediaHandle(path, content_type, session=session)
        try:
            port = session.server.server_address[1]
            base = self._start_tunnel(exe, port, session)
            handle.public_url = base + route
            handle.cover_url = base + cover_route if cover_route else None
            if self.verify_public:
                self._wait_until_public(handle.public_url, path.stat().st_size)
        except BaseException:
            self.cleanup(handle)
            raise
        log.info("Cloudflare Quick Tunnel ready (serving the local video; nothing uploaded).")
        return handle

    def get_public_url(self, handle: MediaHandle) -> str:
        if handle.cleaned or not handle.public_url:
            raise MediaStorageError("Temporary media was already cleaned up")
        return handle.public_url

    def cleanup(self, handle: MediaHandle | None) -> None:
        """Stop cloudflared and the local server. Never raises: a finished publish must stay finished."""
        if handle is None or handle.cleaned:
            return
        handle.cleaned = True
        handle.public_url = handle.cover_url = None
        session, handle.session = handle.session, None
        if session is None:
            return
        try:
            session.close()
            log.info("Cloudflare Quick Tunnel stopped.")
        except Exception as e:  # noqa: BLE001
            log.warning("Tunnel cleanup warning: %s", e)

    def health_check(self) -> str:
        """Local check only: cloudflared exists and runs. No tunnel is started."""
        exe = find_cloudflared(self.executable)
        if exe is None:
            raise StorageNotConfiguredError(f"cloudflared not found ({self.executable!r}). {INSTALL_HINT}")
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise MediaStorageError(f"cloudflared could not be run ({type(e).__name__})") from e
        version = (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else "?"
        return (f"available ({version}). Quick Tunnels are for testing/development (no uptime guarantee); "
                "no tunnel is started by this check.")

    # --- internals --------------------------------------------------------------------------

    def _start_server(self, routes: dict[str, tuple[Path, str]]) -> TunnelSession:
        server = http.server.ThreadingHTTPServer((self.host, 0), make_handler(routes))  # 0 = free port
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, name="soc_bot-media-server", daemon=True)
        thread.start()
        return TunnelSession(server, thread)

    def _start_tunnel(self, exe: str, port: int, session: TunnelSession) -> str:
        origin = f"http://{self.host}:{port}"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = self.popen([exe, "tunnel", "--no-autoupdate", "--url", origin],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", creationflags=flags)
        except OSError as e:
            raise StorageNotConfiguredError(f"cloudflared could not be started ({type(e).__name__}). {INSTALL_HINT}") from e
        session.process = process
        _LIVE.add(process)

        found: list[str] = []
        errors: list[str] = []
        ready = threading.Event()

        def reader():  # drains output so cloudflared never blocks on a full pipe
            for line in process.stdout:
                if " ERR " in line:  # kept for the error message, without any URL
                    errors[:] = [re.sub(r"https?://\S+", "<url>", line.split(" ERR ", 1)[1]).strip()[:160]]
                if not found:
                    url = parse_tunnel_url(line)
                    if url:
                        found.append(url)
                        ready.set()
            ready.set()  # output closed: process ended

        threading.Thread(target=reader, name="soc_bot-cloudflared-output", daemon=True).start()
        if not ready.wait(self.startup_timeout) or not found:
            if process.poll() is not None:
                raise MediaStorageError(f"cloudflared exited before the tunnel was ready (exit code {process.returncode})"
                                        + (f": {errors[0]}" if errors else ""),
                                        retryable=True)
            raise MediaStorageError(f"Cloudflare Quick Tunnel did not start within {self.startup_timeout:g}s"
                                    + (f" (last cloudflared error: {errors[0]})" if errors else ""),
                                    retryable=True)
        return found[0]

    def _wait_until_public(self, url: str, size: int) -> None:
        """Wait until the file is reachable the way Meta will reach it: via PUBLIC DNS.

        A new trycloudflare hostname usually appears in public DNS a few seconds after cloudflared prints
        it. Asking earlier makes resolvers cache "no such host" for the zone's negative TTL (60 s), and the
        local resolver can't be told to forget. So the first lookup waits a moment, the hostname is resolved
        with public DNS-over-HTTPS (two resolvers, alternated), and the HEAD goes to that IP with the real
        hostname as TLS SNI + Host (certificate still verified).
        """
        parsed = urlparse(url)
        host = parsed.hostname or ""
        deadline = time.monotonic() + self.startup_timeout
        last = "not in public DNS yet"
        time.sleep(FIRST_LOOKUP_DELAY)
        attempt = 0
        while time.monotonic() < deadline:
            resolver, attempt = DOH_URLS[attempt % len(DOH_URLS)], attempt + 1
            try:
                ip = self._resolve_public(host, resolver)
                if ip:
                    response = self.client.head(f"https://{ip}{parsed.path}", headers={"Host": host},
                                                extensions={"sni_hostname": host}, follow_redirects=False)
                    if response.status_code == 200 and response.headers.get("content-length") in (None, str(size)):
                        return
                    last = f"HTTP {response.status_code}"
            except httpx.HTTPError as e:
                last = type(e).__name__
            time.sleep(2)
        raise MediaStorageError(f"Tunnel URL not reachable within {self.startup_timeout:g}s ({last})", retryable=True)

    def _resolve_public(self, host: str, resolver: str = DOH_URLS[0]) -> str | None:
        """A public IPv4 address for ``host`` from a DNS-over-HTTPS JSON API, or None."""
        response = self.client.get(resolver, params={"name": host, "type": "A"},
                                   headers={"Accept": "application/dns-json"})
        if response.status_code != 200:
            return None
        try:
            answers = response.json().get("Answer") or []
        except (ValueError, AttributeError):
            return None
        for answer in answers:
            try:
                ip = ipaddress.ip_address(str(answer.get("data", "")))
            except ValueError:
                continue
            if answer.get("type") == 1 and ip.is_global:
                return str(ip)
        return None
