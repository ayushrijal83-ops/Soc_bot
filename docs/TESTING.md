# Testing Strategy

## Test Framework
- **pytest** — Primary test runner
- **pytest-asyncio** — Async test support
- **pytest-mock** — Mocking utilities
- **respx** — HTTP mocking for API tests
- **faker** — Test data generation

## Test Organization

```
tests/
├── unit/                    # Fast, isolated unit tests (214 tests ✅)
│   ├── test_tokens.py       # Token encryption (13 tests)
│   ├── test_database.py     # Database layer (34 tests)
│   ├── test_cli_display.py  # Display utilities (10 tests)
│   ├── test_cli_prompts.py  # Input validation (22 tests)
│   ├── test_cli_menu.py     # Menu navigation (13 tests)
│   ├── test_main.py         # Entry point (8 tests)
│   ├── test_account_manager.py  # Account management (38 tests)
│   ├── test_auth_state.py       # OAuth state/PKCE (23 tests)
│   ├── test_auth_callback.py    # OAuth callback server (17 tests)
│   ├── test_auth_manager.py     # AuthManager flow, dynamic port, persistence (11 tests)
│   ├── test_platform_auth.py    # Platform auth adapters, mocked HTTP (25 tests)
│   ├── test_validation.py       # 📋 PLANNED
│   ├── test_models.py           # 📋 PLANNED
│   ├── test_job_state_machine.py  # 📋 PLANNED
│   └── test_cli_parsing.py      # 📋 PLANNED
├── integration/             # Slower, cross-component tests
│   ├── test_database.py     # 📋 PLANNED (uses temp DB)
│   ├── test_account_manager.py  # 📋 PLANNED
│   ├── test_publisher_engine.py  # 📋 PLANNED
│   └── test_oauth_flows.py   # 📋 PLANNED
├── platform/                # Platform-specific tests (mocked APIs)
│   ├── test_instagram_adapter.py  # 📋 PLANNED
│   ├── test_tiktok_adapter.py    # 📋 PLANNED
│   └── test_youtube_adapter.py   # 📋 PLANNED
├── e2e/                     # End-to-end (manual/ci only)
│   └── test_publish_flow.py  # 📋 PLANNED
└── fixtures/                # Shared test data
    ├── sample_videos/
    ├── mock_responses/
    └── test_accounts.json
```

## Unit Tests (Implemented: 214)

| Test Module | Tests | Coverage |
|-------------|-------|----------|
| `test_tokens.py` | 13 | Encrypt/decrypt roundtrip, special chars, empty strings, wrong key, corrupted data, optional handling, env key |
| `test_database.py` | 34 | Init, schema version, idempotency, migrations, health check, all model CRUD, constraints, relationships, indexes, encryption integration |
| `test_cli_display.py` | 10 | Headers, menus, tables, status messages, empty tables |
| `test_cli_prompts.py` | 22 | Text, int, choice, yes/no, menu selection, EOF, Ctrl+C |
| `test_cli_menu.py` | 13 | Init, routing, handlers, run loop, edge cases |
| `test_main.py` | 8 | Args parsing, dry-run, KeyboardInterrupt, exceptions |
| `test_account_manager.py` | 38 | Account CRUD, listing, filtering, updates, disconnect, enable, dev accounts, security, edge cases |
| `test_auth_state.py` | 23 | OAuth state, PKCE (base64url + hex), platform binding, single use, expiration, cleanup |
| `test_auth_callback.py` | 17 | Callback server: dynamic port, bind failure, platform mismatch, single use, non-callback paths, escaping, no code echo |
| `test_auth_manager.py` | 11 | End-to-end connect flow on a real loopback server with mocked providers; dynamic port; state/platform mismatch; TikTok rotation persisted; Instagram re-exchange; reconnect update; no secrets in logs |
| `test_platform_auth.py` | 25 | Instagram Login endpoints/scopes/exchange/identity/revocation, TikTok hex PKCE/expiry/rotation, YouTube base64url PKCE, expiry model |

### Token Encryption Tests (`test_tokens.py`)
- Key generation produces valid URL-safe base64
- Encrypt/decrypt roundtrip
- Special characters in tokens
- Empty string/bytes raise errors
- Wrong key fails decryption
- Corrupted data fails decryption
- Optional handling (None/empty)
- Environment variable key loading

### Database Layer Tests (`test_database.py`)
- Database initialization creates all 6 tables
- Schema version tracking
- Idempotent initialization
- Migration runs pending only
- Health check
- Account: create, uniqueness, platform/status CHECK constraints, encryption roundtrip
- Video: create, checksum uniqueness, cascade delete posts
- Post: create, FK to video
- PublishJob: create, status CHECK, FK to post/account, default status
- PublishAttempt: create, uniqueness, status CHECK, cascade delete
- Relationships: account→jobs, post→jobs, job→attempts (ordered)
- Token encryption integration: persist/decrypt, wrong key fails
- Indexes: all expected indexes exist

### CLI Display Tests (`test_cli_display.py`)
- Header rendering with borders
- Menu rendering with numbered options
- Info/Success/Warning/Error messages
- Not implemented message
- Table rendering with headers and rows
- Empty table handling
- Clear screen function

### CLI Prompts Tests (`test_cli_prompts.py`)
- Text input: required, optional, default, EOF handling
- Integer input: valid, min/max bounds, invalid-then-valid, default, EOF
- Choice selection: valid, out of range, default
- Yes/No: yes, no, default yes, default no
- Menu selection: valid, exit option, no exit, invalid-then-valid, EOF, Ctrl+C

### CLI Menu Tests (`test_cli_menu.py`)
- Handler initialization with 6 options
- Main menu display
- All 6 choice handlers (Create Post, Accounts, Queue, History, Settings, Exit)
- Invalid choice handling
- Run loop: exits on option 6, continues on 1-5
- None choice handling (Ctrl+C/EOF)

### Main Entry Point Tests (`test_main.py`)
- Argument parsing: default, --dry-run, --help, --version
- Main function: normal run, dry-run mode, KeyboardInterrupt, exceptions

### Account Manager Tests (`test_account_manager.py`)
- **Account Creation**: valid account, no refresh token, no display name, default status, invalid platform, invalid status, duplicate rejection, different platform allowed
- **Account Retrieval**: get by ID, find by platform+ID, not found handling
- **Account Listing**: all accounts, filter by platform, filter by status, filter by both, invalid platform/status handling, ordering by created_at DESC
- **Account Updates**: display info, status, access token (encrypted), refresh token (encrypted), nonexistent account, invalid status
- **Account Disconnect**: sets status to disconnected, preserves historical data
- **Account Enable**: sets status to active
- **Active Accounts**: get all active, filter by platform
- **Safe Display**: `get_account_display_info()` excludes tokens
- **Internal Token Access**: `get_account_with_tokens()` for publishing, not found handling
- **Development Accounts**: explicitly labeled dev accounts with placeholder tokens
- **Error Hierarchy**: AccountError, AccountNotFoundError, DuplicateAccountError, InvalidPlatformError, InvalidStatusError
- **Edge Cases**: expires_at updates, meta_json updates, case-sensitive platform/status validation, ordering verification

### OAuth State Tests (`test_auth_state.py`)
- **OAuthState**: creation, expiration checks, validity
- **OAuthStateStore**: create, get, consume, cleanup, expiration, TTL
- **Platform binding**: matching platform accepted; mismatch rejected and state consumed
- **PKCE**: verifier length (43–128), base64url default, TikTok hex, unknown encoding rejected

### OAuth Callback Tests (`test_auth_callback.py`)
- Server start/stop
- Timeout handling
- Successful callback integration (real HTTP request)
- Error from provider (access_denied)
- Missing code parameter
- Missing state parameter
- Invalid/expired state
- Dynamic port (port 0): actual port exposed, redirect URI uses it, 8080 not assumed
- Bind failure raises a clean `OAuthCallbackError`
- Instagram state on a TikTok callback (and the reverse) rejected
- State single use, `/favicon.ico` ignored, provider error HTML-escaped, code never echoed

### AuthManager Tests (`test_auth_manager.py`)
- Full YouTube connect over a real dynamic loopback port (provider HTTP mocked, browser simulated)
- Fresh port per flow; server closed afterwards
- Fixed redirect URI honoured and validated
- Cross-platform state rejected before code exchange
- TikTok refresh persists the rotated refresh token (encrypted)
- Instagram renewal re-exchanges the access token
- Reconnect updates an existing account instead of failing as a duplicate
- Tokens, codes and client secrets absent from logs and results

### Platform Auth Tests (`test_platform_auth.py`)
- **InstagramAuth**: instagram.com authorize URL, Instagram Login scopes (legacy scopes absent), no PKCE, api.instagram.com code exchange, `ig_exchange_token`, failure does not fall back to the short-lived token, `ig_refresh_token` returns a new token, `/me` `user_id` identity with header auth, revocation makes no request
- **TikTokAuth**: hex PKCE challenge, comma scopes, `expires_in` → `expires_at`, refresh-token rotation
- **YouTubeAuth**: base64url PKCE, offline/consent params, refresh keeps the refresh token when Google omits it
- **Expiry model**: no invented expiry; `is_token_expiring` with a margin and naive-UTC input

Real provider OAuth is **not** exercised by any automated test. Real OAuth test: NOT RUN (credentials not configured).

## Integration Tests (Implemented: 0)

| Test Module | Coverage Target |
|-------------|-----------------|
| `test_database.py` | CRUD, migrations, constraints (temp DB) |
| `test_account_manager.py` | OAuth flow, token refresh, disconnect |
| `test_publisher_engine.py` | Job creation, execution, dry-run |
| `test_oauth_flows.py` | 📋 PLANNED |

## Platform Adapter Tests (Implemented: 0)

Each platform adapter tested against **mocked official API responses**:
- Media validation
- Upload initiation
- Status polling
- Publishing
- Token refresh
- Error handling (rate limit, auth expired, invalid media)

## OAuth Tests (Implemented: 7)

- OAuth state creation and expiration
- PKCE verifier/challenge generation and validation
- Callback server start/stop, timeout
- Successful callback with code + state
- Provider error (access_denied)
- Missing code parameter
- Missing state parameter
- Invalid/expired state

## Publishing Tests (Implemented: 0)

- Single destination publish
- Multi-destination publish (independent jobs)
- Partial failure handling
- Retry logic
- Dry-run accuracy

## Failure Tests (Implemented: 0)

- Network timeout
- Platform API errors (4xx, 5xx)
- Invalid media format/size
- Expired tokens
- Revoked permissions
- Rate limiting (429)
- Quota exceeded

## Retry Tests (Implemented: 0)

- Retry on transient failure
- No retry on permanent failure
- Exponential backoff timing
- Max retry limit
- Retry state persistence

## Dry-run Tests (Implemented: 0)

- Plan matches actual execution
- No API calls made
- All destinations listed
- Validation still runs

## Account Selection Tests (Implemented: 0)

- Select subset of connected accounts
- Deselect all → no jobs created
- Platform filter
- Account status filter (only active)

## Running Tests

```bash
# All tests
pytest

# Unit only (fast) — 214 tests passing
pytest tests/unit -v

# Integration (requires test DB)
pytest tests/integration -v

# Platform mocks
pytest tests/platform -v

# With coverage
pytest --cov=src --cov-report=html

# Parallel (if independent)
pytest -n auto
```

## Test Data

- **No real credentials** in tests
- Mock responses from official API documentation
- Fixtures in `tests/fixtures/`
- Video fixtures: small synthetic MP4 files (< 1MB)
- Temporary databases used for isolation (`tempfile.NamedTemporaryFile`)

## CI/CD Integration (Future)

```yaml
# .github/workflows/test.yml
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements-dev.txt
      - run: pytest --cov=src --cov-fail-under=80
```

## Current Status
✅ **Phase 1, 2, 3A & 3B (incl. 2026-09-25 audit fixes)**: 214 unit tests passing; `ruff check .`: 0 errors. OAuth is tested with mocks only. Real provider OAuth: NOT RUN.

Next: Integration tests for account manager and publisher engine (Phase 4).