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
├── unit/                    # Fast, isolated unit tests (170 tests ✅)
│   ├── test_tokens.py       # Token encryption (14 tests)
│   ├── test_database.py     # Database layer (33 tests)
│   ├── test_cli_display.py  # Display utilities (11 tests)
│   ├── test_cli_prompts.py  # Input validation (24 tests)
│   ├── test_cli_menu.py     # Menu navigation (14 tests)
│   ├── test_main.py         # Entry point (4 tests)
│   ├── test_account_manager.py  # Account management (38 tests)
│   ├── test_auth_state.py       # OAuth state/PKCE (10 tests)
│   ├── test_auth_callback.py    # OAuth callback server (7 tests)
│   ├── test_platform_auth.py    # Platform auth adapters (9 tests)
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

## Unit Tests (Implemented: 170)

| Test Module | Tests | Coverage |
|-------------|-------|----------|
| `test_tokens.py` | 14 | Encrypt/decrypt roundtrip, special chars, empty strings, wrong key, corrupted data, optional handling, env key |
| `test_database.py` | 33 | Init, schema version, idempotency, migrations, health check, all model CRUD, constraints, relationships, indexes, encryption integration |
| `test_cli_display.py` | 11 | Headers, menus, tables, status messages, empty tables |
| `test_cli_prompts.py` | 24 | Text, int, choice, yes/no, menu selection, EOF, Ctrl+C |
| `test_cli_menu.py` | 14 | Init, routing, handlers, run loop, edge cases |
| `test_main.py` | 4 | Args parsing, dry-run, KeyboardInterrupt, exceptions |
| `test_account_manager.py` | 38 | Account CRUD, listing, filtering, updates, disconnect, enable, dev accounts, security, edge cases |
| `test_auth_state.py` | 10 | OAuth state, PKCE, expiration, cleanup |
| `test_auth_callback.py` | 7 | OAuth callback server, success, error, missing code/state, invalid state |
| `test_platform_auth.py` | 9 | Platform auth adapters (config, URLs, PKCE for all 3) |

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
- **OAuthStateStore**: create, get, consume, cleanup, expiration
- **PKCE**: verifier generation, challenge generation (S256), uniqueness

### OAuth Callback Tests (`test_auth_callback.py`)
- Server start/stop
- Timeout handling
- Successful callback integration (real HTTP request)
- Error from provider (access_denied)
- Missing code parameter
- Missing state parameter
- Invalid/expired state

### Platform Auth Tests (`test_platform_auth.py`)
- **InstagramAuth**: config creation, validation, PKCE, authorization URL
- **TikTokAuth**: config creation, authorization URL
- **YouTubeAuth**: config creation, authorization URL
- PKCE pair generation for all platforms

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

# Unit only (fast) — 170 tests passing
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
✅ **Phase 1, 2, 3A & 3B UNIT TESTS IMPLEMENTED** — 170 tests passing covering token encryption, database layer, CLI framework, Account Management Core, and OAuth Infrastructure.

Next: Integration tests for account manager and publisher engine (Phase 4).