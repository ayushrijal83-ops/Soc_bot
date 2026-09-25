"""Tests for OAuth callback server."""

import threading
import time
import urllib.request
import urllib.error
from unittest.mock import patch, MagicMock
import pytest

from src.auth.state import OAuthStateStore
from src.auth.callback_server import OAuthCallbackServer
from src.auth.errors import OAuthCallbackError


class TestOAuthCallbackServer:
    """Tests for OAuthCallbackServer class."""

    def test_server_start_stop(self):
        """Test starting and stopping the server."""
        state_store = OAuthStateStore()
        server = OAuthCallbackServer(
            host="127.0.0.1",
            port=0,  # Use random port
            state_store=state_store,
            timeout=5,
        )

        callback_data = {}

        def handle_callback(data):
            callback_data.update(data)

        server.start(handle_callback)

        # Give server time to start
        time.sleep(0.1)

        # Stop server
        server.stop()

        assert callback_data == {}

    def test_wait_for_callback_timeout(self):
        """Test timeout when no callback received."""
        state_store = OAuthStateStore()
        server = OAuthCallbackServer(
            host="127.0.0.1",
            port=0,
            state_store=state_store,
            timeout=1,  # 1 second timeout
        )

        server.start(lambda data: None)

        with pytest.raises(TimeoutError):
            server.wait_for_callback()

        server.stop()

    def test_successful_callback_integration(self):
        """Test full callback flow with real HTTP request."""
        state_store = OAuthStateStore()
        oauth_state = state_store.create_state("instagram", "test_verifier")

        server = OAuthCallbackServer(
            host="127.0.0.1",
            port=0,
            state_store=state_store,
            timeout=5,
        )

        callback_data = {}

        def handle_callback(data):
            callback_data.update(data)

        server.start(handle_callback)

        # Give server time to start
        time.sleep(0.1)

        # Get the actual port
        port = server._server.server_address[1]

        # Make a real HTTP request to the callback endpoint
        url = f"http://127.0.0.1:{server._server.server_address[1]}/callback/instagram?code=test_code&state={oauth_state.state}"
        
        try:
            response = urllib.request.urlopen(url, timeout=5)
            html = response.read().decode()
            
            # Wait for callback to be processed
            time.sleep(0.5)
            
            # Verify callback was called
            assert callback_data.get("code") == "test_code"
            assert callback_data.get("state") == oauth_state.state
            assert callback_data.get("platform") == "instagram"
            assert callback_data.get("pkce_verifier") == oauth_state.pkce_verifier
            
            # Check response
            assert "Authorization Successful" in html
            
        finally:
            server.stop()

    def test_callback_with_error(self):
        """Test callback with error parameter from provider."""
        state_store = OAuthStateStore()

        server = OAuthCallbackServer(
            host="127.0.0.1",
            port=0,
            state_store=state_store,
            timeout=5,
        )

        callback_data = {}

        def handle_callback(data):
            callback_data.update(data)

        server.start(handle_callback)
        time.sleep(0.1)

        port = server._server.server_address[1]
        
        # Make request with error
        url = f"http://127.0.0.1:{port}/callback/instagram?error=access_denied&error_description=User%20denied"
        
        try:
            # urllib raises HTTPError for non-2xx responses, we need to catch it
            urllib.request.urlopen(url, timeout=5)
        except urllib.error.HTTPError as e:
            # Read the response body anyway
            html = e.read().decode()
            # Status code should be 400
            assert e.code == 400
        
        # Wait for callback to be processed
        time.sleep(0.5)
        
        # Verify callback was called with error data
        assert callback_data.get("error") == "access_denied"
        assert "User denied" in callback_data.get("error_description", "")
        assert callback_data.get("code") is None
        assert callback_data.get("state") is None
        assert callback_data.get("error") == "access_denied"
        
        # Check response shows error
        assert "Authorization Failed" in html
        assert "access_denied" in html
        
        server.stop()

    def test_callback_missing_code(self):
        """Test callback with missing code parameter."""
        state_store = OAuthStateStore()
        oauth_state = state_store.create_state("instagram")

        server = OAuthCallbackServer(
            host="127.0.0.1",
            port=0,
            state_store=state_store,
            timeout=5,
        )

        callback_data = {}

        def handle_callback(data):
            callback_data.update(data)

        server.start(handle_callback)
        time.sleep(0.1)

        port = server._server.server_address[1]
        
        # Request with state but no code
        url = f"http://127.0.0.1:{port}/callback/instagram?state={oauth_state.state}"
        
        try:
            urllib.request.urlopen(url, timeout=5)
        except urllib.error.HTTPError as e:
            html = e.read().decode()
            assert e.code == 400
        
        time.sleep(0.5)
        
        # Verify callback was called with error data
        assert callback_data.get("code") is None
        assert callback_data.get("state") == oauth_state.state
        assert callback_data.get("error") == "missing_code"
        assert "Missing authorization code" in callback_data.get("error_description", "")
        
        server.stop()

    def test_callback_missing_state(self):
        """Test callback with missing state parameter."""
        state_store = OAuthStateStore()

        server = OAuthCallbackServer(
            host="127.0.0.1",
            port=0,
            state_store=state_store,
            timeout=5,
        )

        callback_data = {}

        def handle_callback(data):
            callback_data.update(data)

        server.start(handle_callback)
        time.sleep(0.1)

        port = server._server.server_address[1]
        
        # Request with code but no state
        url = f"http://127.0.0.1:{port}/callback/instagram?code=test_code"
        
        try:
            urllib.request.urlopen(url, timeout=5)
        except urllib.error.HTTPError as e:
            html = e.read().decode()
            assert e.code == 400
        
        time.sleep(0.5)
        
        # Verify callback was called with error data
        assert callback_data.get("code") == "test_code"
        assert callback_data.get("state") is None
        assert callback_data.get("error") == "missing_state"
        assert "Missing state" in callback_data.get("error_description", "")
        
        server.stop()

    def test_callback_invalid_state(self):
        """Test callback with invalid state."""
        state_store = OAuthStateStore()

        server = OAuthCallbackServer(
            host="127.0.0.1",
            port=0,
            state_store=state_store,
            timeout=5,
        )

        callback_data = {}

        def handle_callback(data):
            callback_data.update(data)

        server.start(handle_callback)
        time.sleep(0.1)

        port = server._server.server_address[1]
        
        # Request with invalid state
        url = f"http://127.0.0.1:{port}/callback/instagram?code=test_code&state=invalid_state"
        
        try:
            urllib.request.urlopen(url, timeout=5)
        except urllib.error.HTTPError as e:
            html = e.read().decode()
            assert e.code == 400
        
        time.sleep(0.5)
        
        # Verify callback was called with error data
        assert callback_data.get("code") == "test_code"
        assert callback_data.get("state") == "invalid_state"
        assert callback_data.get("error") == "invalid_state"
        assert "Invalid or expired state" in callback_data.get("error_description", "")
        
        server.stop()