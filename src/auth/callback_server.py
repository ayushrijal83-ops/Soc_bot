"""OAuth callback server for receiving authorization callbacks."""

import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.auth.errors import OAuthStateError
from src.auth.state import OAuthStateStore


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callbacks."""

    def __init__(self, *args, state_store: OAuthStateStore, on_callback: Callable[[dict[str, Any]], None], **kwargs):
        self.state_store = state_store
        self.on_callback = on_callback
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        """Handle GET request for OAuth callback."""
        parsed = urlparse(self.path)
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

            # Validate state
            oauth_state = self.state_store.consume_state(state)

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
        except Exception as e:
            callback_data["error"] = "server_error"
            callback_data["error_description"] = str(e)
            self.on_callback(callback_data)
            self._send_error(f"Unexpected error: {e!s}", 500)

    def _send_success(self) -> None:
        """Send success HTML response."""
        html = """
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
        self.wfile.write(html.encode())

    def _send_error(self, message: str, status: int = 400) -> None:
        """Send error HTML response."""
        html = f"""
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
            <p>{message}</p>
            <p>You can close this window and return to Soc_bot.</p>
        </body>
        </html>
        """
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format: str, *args) -> None:
        """Suppress default log messages."""


class OAuthCallbackServer:
    """Local HTTP server for receiving OAuth callbacks."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
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
        self._error: Exception | None = None

    def start(self, on_callback: Callable[[dict[str, Any]], None]) -> None:
        """Start the callback server."""

        def handler(*args, **kwargs):
            return OAuthCallbackHandler(
                *args,
                state_store=self.state_store,
                on_callback=lambda data: self._handle_callback(data, on_callback),
                **kwargs,
            )

        self._server = HTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _handle_callback(self, callback_data: dict[str, Any], on_callback: Callable[[dict[str, Any]], None]) -> None:
        """Handle callback data and signal completion."""
        self._callback_data = callback_data
        self._callback_received.set()
        on_callback(callback_data)

    def wait_for_callback(self) -> dict[str, Any]:
        """Wait for callback and return data."""
        if not self._callback_received.wait(timeout=self.timeout):
            raise TimeoutError(f"OAuth callback timed out after {self.timeout} seconds")

        if self._error:
            raise self._error

        return self._callback_data

    def stop(self) -> None:
        """Stop the callback server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)


