"""OAuth authentication errors."""



class OAuthError(Exception):
    """Base exception for OAuth-related errors."""

    def __init__(self, message: str, platform: str | None = None):
        self.platform = platform
        super().__init__(message)

    def __str__(self) -> str:
        if self.platform:
            return f"[{self.platform}] {super().__str__()}"
        return super().__str__()


class OAuthConfigurationError(OAuthError):
    """Raised when OAuth configuration is invalid or missing."""

    def __init__(self, message: str, platform: str | None = None, missing_fields: list | None = None):
        self.missing_fields = missing_fields or []
        super().__init__(message, platform)


class OAuthStateError(OAuthError):
    """Raised when OAuth state validation fails."""

    def __init__(self, message: str, platform: str | None = None, state: str | None = None):
        self.state = state
        super().__init__(message, platform)


class OAuthCallbackError(OAuthError):
    """Raised when OAuth callback fails."""

    def __init__(self, message: str, platform: str | None = None, error: str | None = None, error_description: str | None = None):
        self.error = error
        self.error_description = error_description
        super().__init__(message, platform)


class OAuthTokenExchangeError(OAuthError):
    """Raised when token exchange fails."""

    def __init__(self, message: str, platform: str | None = None, status_code: int | None = None, response_body: str | None = None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message, platform)


class OAuthAccountIdentityError(OAuthError):
    """Raised when account identity cannot be retrieved."""

    def __init__(self, message: str, platform: str | None = None):
        super().__init__(message, platform)


class OAuthScopeError(OAuthError):
    """Raised when required scopes are not granted."""

    def __init__(self, message: str, platform: str | None = None, granted_scopes: list | None = None, required_scopes: list | None = None):
        self.granted_scopes = granted_scopes or []
        self.required_scopes = required_scopes or []
        super().__init__(message, platform)


class OAuthCallbackTimeoutError(OAuthError):
    """Raised when callback server times out waiting for authorization."""

    def __init__(self, message: str, platform: str | None = None, timeout_seconds: int | None = None):
        self.timeout_seconds = timeout_seconds
        super().__init__(message, platform)