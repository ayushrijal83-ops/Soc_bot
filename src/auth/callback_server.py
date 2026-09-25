"""OAuth callback server for receiving authorization callbacks."""

import html
import socket
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.auth.errors import OAuthCallbackError, OAuthStateError
from src.auth.state import OAuthStateStore

CALLBACK_PATH_PREFIX = "/callback/"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callbacks."""

    def __init__(self, *args, state_store: OAuthStateStore, on_callback: Callable[[dict[str, Any]], None], **kwargs):
        self.state_store = state_store
        self.on_callback = on_callback
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        """Handle GET request for OAuth callback."""
        parsed = urlparse(self.path)
        if not parsed.path.startswith(CALLBACK_PATH_PREFIX):
            # e.g. /favicon.ico — must not be mistaken for an OAuth callback
            self._send_error("Not found", 404)
            return
        callback_platform = parsed.path[len(CALLBACK_PATH_PREFIX):].strip("/")
        query = parse_qs(parsed.query)

        # Extract parameters
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0]
        error = query.get("error", [None])[0]
        error_description = query.get("error_description", [None])[0]

        # Prepare callback data
        callback_data = {
            "code": code,
            "state": state,
            "error": error,
            "error_description": error_description,
            "path": parsed.path,
        }

        try:
            if error:
                # OAuth provider returned an error (e.g., user denied)
                callback_data["platform"] = None
                callback_data["pkce_verifier"] = None
                self.on_callback(callback_data)
                self._send_error(
                    f"OAuth authorization failed: {error} - {error_description or ''}",
                    400
                )
                return

            if not code:
                callback_data["error"] = "missing_code"
                callback_data["error_description"] = "Missing authorization code in callback"
                self.on_callback(callback_data)
                self._send_error("Missing authorization code in callback", 400)
                return

            if not state:
                callback_data["error"] = "missing_state"
                callback_data["error_description"] = "Missing state parameter in callback"
                self.on_callback(callback_data)
                self._send_error("Missing state parameter in callback", 400)
                return

            # Validate state (single-use) and that it was issued for this callback's platform
            oauth_state = self.state_store.consume_state(state, platform=callback_platform)

            # Add platform info to callback data
            callback_data["platform"] = oauth_state.platform
            callback_data["pkce_verifier"] = oauth_state.pkce_verifier

            # Call the callback handler
            self.on_callback(callback_data)

            # Send success response
            self._send_success()

        except OAuthStateError as e:
            callback_data["error"] = "invalid_state"
            callback_data["error_description"] = str(e)
            self.on_callback(callback_data)
            self._send_error("Invalid or expired state. Please try again.", 400)
        except Exception as e:  # noqa: BLE001 - must always answer the browser
            callback_data["error"] = "server_error"
            callback_data["error_description"] = type(e).__name__
            self.on_callback(callback_data)
            self._send_error("Unexpected error while handling the callback.", 500)

    def _send_success(self) -> None:
        """Send success HTML response."""
        page = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Authorization Successful</title>
            <style>
                body { font-family: sans-serif; text-align: center; padding: 50px; }
                .success { color: #28a745; }
            </style>
        </head>
        <body>
            <h1 class="success">Authorization Successful</h1>
            <p>You can now return to Soc_bot.</p>
        </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(page.encode())

    def _send_error(self, message: str, status: int = 400) -> None:
        """Send error HTML response."""
        page = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Authorization Failed</title>
            <style>
                body {{ font-family: sans-serif; text-align: center; padding: 50px; }}
                .error {{ color: #dc3545; }}
            </style>
        </head>
        <body>
            <h1 class="error">Authorization Failed</h1>
            <p>{html.escape(message)}</p>
            <p>You can close this window and return to Soc_bot.</p>
        </body>
        </html>
        """
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(page.encode())

    def log_message(self, format: str, *args) -> None:
        """Suppress default log messages."""


class _LoopbackHTTPServer(HTTPServer):
    # HTTPServer enables SO_REUSEADDR, which on Windows lets another process bind the
    # same port and intercept the authorization code. Require exclusive binding.
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows only
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class OAuthCallbackServer:
    """Local loopback HTTP server for receiving OAuth callbacks.

    ``port=0`` (the default) lets the OS pick a free port; read the bound port
    from ``port`` after ``start()`` and build the redirect URI with ``redirect_uri()``.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        state_store: OAuthStateStore | None = None,
        timeout: int = 300,
    ):
        self.host = host
        self.port = port
        self.state_store = state_store or OAuthStateStore()
        self.timeout = timeout
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._callback_data: dict[str, Any] = {}
        self._callback_received = threading.Event()

    def start(self, on_callback: Callable[[dict[str, Any]], None] | None = None) -> None:
        """Bind the socket and start serving in a background thread."""

        def handler(*args, **kwargs):
            return OAuthCallbackHandler(
                *args,
                state_store=self.state_store,
                on_callback=lambda data: self._handle_callback(data, on_callback),
                **kwargs,
            )

        try:
            self._server = _LoopbackHTTPServer((self.host, self.port), handler)
        except OSError as e:
            raise OAuthCallbackError(
                f"Could not start OAuth callback server on {self.host}:{self.port}: {e.strerror or e}"
            ) from e
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def redirect_uri(self, platform: str) -> str:
        """Redirect URI for ``platform`` using the actually bound port."""
        if self._server is None:
            raise OAuthCallbackError("Callback server is not started")
        return f"http://{self.host}:{self.port}/callback/{platform}"

    def _handle_callback(
        self, callback_data: dict[str, Any], on_callback: Callable[[dict[str, Any]], None] | None
    ) -> None:
        """Handle callback data and signal completion."""
        self._callback_data = callback_data
        self._callback_received.set()
        if on_callback:
            on_callback(callback_data)

    def wait_for_callback(self) -> dict[str, Any]:
        """Block until a callback arrives and return its data."""
        if not self._callback_received.wait(timeout=self.timeout):
            raise TimeoutError(f"OAuth callback timed out after {self.timeout} seconds")
        return self._callback_data

    def stop(self) -> None:
        """Stop the callback server. Safe to call more than once."""
        server, self._server = self._server, None
        if server:
            try:
                server.shutdown()
            finally:
                server.server_close()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
