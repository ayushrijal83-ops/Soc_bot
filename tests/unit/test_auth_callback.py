"""Tests for OAuth callback server."""

import time
import urllib.error
import urllib.request

import pytest

from src.auth.callback_server import OAuthCallbackServer
from src.auth.errors import OAuthCallbackError
from src.auth.state import OAuthStateStore


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
            assert e.code == 400
        
        time.sleep(0.5)
        
        # Verify callback was called with error data
        assert callback_data.get("code") == "test_code"
        assert callback_data.get("state") == "invalid_state"
        assert callback_data.get("error") == "invalid_state"
        assert "Invalid or expired state" in callback_data.get("error_description", "")
        
        server.stop()

class TestDynamicPortAndPlatformBinding:
    """Regression tests for Phase 3B audit fixes."""

    def _get(self, url):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_default_port_is_dynamic_and_exposed(self):
        server = OAuthCallbackServer(state_store=OAuthStateStore(), timeout=5)
        assert server.host == "127.0.0.1"
        assert server.port == 0
        server.start()
        try:
            assert server.port not in (0, 8080)
            assert server.port == server._server.server_address[1]
            assert server.redirect_uri("youtube") == f"http://127.0.0.1:{server.port}/callback/youtube"
        finally:
            server.stop()
        server.stop()  # idempotent

    def test_redirect_uri_requires_started_server(self):
        with pytest.raises(OAuthCallbackError):
            OAuthCallbackServer().redirect_uri("youtube")

    def test_bind_failure_raises_clean_error(self):
        first = OAuthCallbackServer()
        first.start()
        try:
            second = OAuthCallbackServer(port=first.port)
            with pytest.raises(OAuthCallbackError, match="Could not start"):
                second.start()
        finally:
            first.stop()

    def test_same_platform_callback_accepted_on_dynamic_port(self):
        store = OAuthStateStore()
        st = store.create_state("youtube", "verifier")
        server = OAuthCallbackServer(state_store=store, timeout=5)
        server.start()
        try:
            status, _ = self._get(f"{server.redirect_uri('youtube')}?code=c&state={st.state}")
            data = server.wait_for_callback()
        finally:
            server.stop()
        assert status == 200
        assert data["platform"] == "youtube"
        assert data["pkce_verifier"] == "verifier"
        assert data["error"] is None

    @pytest.mark.parametrize("issued,callback", [("instagram", "tiktok"), ("tiktok", "instagram")])
    def test_platform_mismatch_rejected_and_state_consumed(self, issued, callback):
        store = OAuthStateStore()
        st = store.create_state(issued, "verifier")
        server = OAuthCallbackServer(state_store=store, timeout=5)
        server.start()
        try:
            status, _ = self._get(f"{server.redirect_uri(callback)}?code=c&state={st.state}")
            data = server.wait_for_callback()
        finally:
            server.stop()
        assert status == 400
        assert data["error"] == "invalid_state"
        assert data.get("platform") is None
        assert "pkce_verifier" not in data or data["pkce_verifier"] is None
        assert store.get_state(st.state) is None  # single-use even on mismatch

    def test_state_is_single_use(self):
        store = OAuthStateStore()
        st = store.create_state("tiktok")
        server = OAuthCallbackServer(state_store=store, timeout=5)
        server.start()
        try:
            first, _ = self._get(f"{server.redirect_uri('tiktok')}?code=c&state={st.state}")
            second, _ = self._get(f"{server.redirect_uri('tiktok')}?code=c&state={st.state}")
        finally:
            server.stop()
        assert (first, second) == (200, 400)

    def test_non_callback_paths_are_ignored(self):
        server = OAuthCallbackServer(timeout=1)
        server.start()
        try:
            status, _ = self._get(f"http://127.0.0.1:{server.port}/favicon.ico")
            assert status == 404
            with pytest.raises(TimeoutError):
                server.wait_for_callback()
        finally:
            server.stop()

    def test_error_page_escapes_provider_input(self):
        server = OAuthCallbackServer(timeout=5)
        server.start()
        try:
            _, page = self._get(
                f"{server.redirect_uri('tiktok')}?error=%3Cscript%3Ex%3C%2Fscript%3E&error_description=d"
            )
        finally:
            server.stop()
        assert "<script>" not in page
        assert "&lt;script&gt;" in page

    def test_code_not_echoed_in_response_page(self):
        store = OAuthStateStore()
        st = store.create_state("youtube")
        server = OAuthCallbackServer(state_store=store, timeout=5)
        server.start()
        try:
            _, ok_page = self._get(f"{server.redirect_uri('youtube')}?code=SECRET_CODE&state={st.state}")
            _, bad_page = self._get(f"{server.redirect_uri('youtube')}?code=SECRET_CODE&state=bogus")
        finally:
            server.stop()
        assert "SECRET_CODE" not in ok_page
        assert "SECRET_CODE" not in bad_page
