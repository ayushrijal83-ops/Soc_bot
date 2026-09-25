# CLAUDE_HANDOFF.md

> **CRITICAL:** This file is the source of truth when switching from OpenCode to Claude Code. It must contain everything needed to understand the project without asking the user.

---

## Current Objective

**Phase 4 (Publishing Engine) complete at code/test level, 2026-09-25.** Publishing to Instagram (Reels via `video_url`), TikTok (Direct Post) and YouTube (resumable upload) is implemented behind a platform-independent engine, checked against current official docs, and tested with mocks (305 tests, Ruff clean). **Mocked tests verified. Real provider OAuth and publishing NOT verified (never run: no credentials).** Next: Phase 5, real-provider verification.

---

## Current Project State

- **Phase:** 4 (Publishing Engine): **COMPLETE** (mocked tests only; real publishing NOT RUN)
- **Repository:** https://github.com/ayushrijal83-ops/Soc_bot
- **Branch:** main
- **Last Commit:** 90312a5 (Initial commit with docs)
- **Working Tree:** Clean (new files only)
- **Git Status:** Synchronized with remote

---

## What Has Been Implemented ✅

### Repository Structure
```
Soc_bot/
├── main.py                 # IMPLEMENTED - Entry point with arg parsing
├── requirements.txt        # Created
├── requirements-dev.txt    # Created
├── .env.example            # Created
├── .env                    # Created (from .env.example, with ENCRYPTION_KEY)
├── .gitignore              # Created
├── README.md               # Comprehensive
├── LICENSE                 # MIT
├── data/
│   └── publisher.db        # SQLite database (CREATED)
├── logs/                   # Empty
├── videos/                 # Empty
├── src/
│   ├── __init__.py
│   ├── cli/                # IMPLEMENTED
│   │   ├── __init__.py
│   │   ├── display.py      # Formatting, tables, status messages
│   │   ├── menu.py         # Menu navigation & routing
│   │   ├── prompts.py      # Interactive prompts & validation
│   │   └── account_menu.py # Account management submenu
│   ├── auth/               # IMPLEMENTED
│   │   ├── __init__.py
│   │   ├── base.py         # OAuth base classes, config, token result
│   │   ├── state.py        # OAuth state management, PKCE
│   │   ├── callback_server.py # OAuth callback HTTP server
│   │   ├── manager.py      # AuthManager coordinating all platforms
│   │   └── errors.py       # OAuth-specific exceptions
│   ├── core/               # Empty
│   │   └── __init__.py
│   ├── accounts/           # IMPLEMENTED
│   │   ├── __init__.py
│   │   └── manager.py      # Account CRUD, listing, status management
│   ├── storage/            # IMPLEMENTED
│   │   ├── __init__.py
│   │   ├── tokens.py       # Fernet encryption
│   │   ├── database.py     # SQLAlchemy ORM, migrations, models
│   │   └── migrations/
│   │       └── 001_initial_schema.sql
│   └── platforms/
│       ├── __init__.py
│       ├── instagram/      # IMPLEMENTED
│       │   ├── __init__.py
│       │   └── auth.py     # Instagram/Meta OAuth flow
│       ├── tiktok/         # IMPLEMENTED
│       │   ├── __init__.py
│       │   └── auth.py     # TikTok OAuth flow
│       └── youtube/        # IMPLEMENTED
│           ├── __init__.py
│           └── auth.py     # YouTube/Google OAuth flow
├── tests/
│   ├── __init__.py
│   ├── unit/               # 305 TESTS PASSING
│   │   ├── __init__.py
│   │   ├── test_tokens.py
│   │   ├── test_database.py
│   │   ├── test_cli_display.py
│   │   ├── test_cli_prompts.py
│   │   ├── test_cli_menu.py
│   │   ├── test_main.py
│   │   ├── test_account_manager.py
│   │   ├── test_auth_state.py
│   │   ├── test_auth_callback.py
│   │   └── test_platform_auth.py
│   ├── integration/        # Empty
│   │   └── __init__.py
│   ├── platform/           # Empty
│   │   └── __init__.py
│   ├── e2e/                # Empty
│   │   └── __init__.py
│   └── fixtures/           # Empty
│       └── __init__.py
└── docs/                   # ALL DOCS UPDATED
    ├── ARCHITECTURE.md
    ├── PROJECT_STATUS.md
    ├── API_INTEGRATIONS.md
    ├── AUTHENTICATION.md
    ├── DATABASE.md         # UPDATED - IMPLEMENTED
    ├── DEVELOPMENT.md
    ├── TESTING.md
    ├── SECURITY.md
    ├── DECISIONS.md
    ├── TROUBLESHOOTING.md
    └── CLAUDE_HANDOFF.md   # THIS FILE
```

### Documentation (Updated)
All 11 required documentation files updated to reflect Phase 3B completion:
- README.md — Main entry point with accurate status
- ARCHITECTURE.md — System design with ASCII diagrams
- PROJECT_STATUS.md — Progress tracker (Phase 3B ✅)
- API_INTEGRATIONS.md — Platform APIs (all VERIFIED against official docs)
- AUTHENTICATION.md — OAuth design, token handling
- DATABASE.md — Schema design (**IMPLEMENTED**)
- DEVELOPMENT.md — Dev setup, commands
- TESTING.md — Test strategy
- SECURITY.md — Security practices
- DECISIONS.md — 10 ADRs documented
- TROUBLESHOOTING.md — Common issues structure
- CLAUDE_HANDOFF.md — This file

### Configuration Files
- `.gitignore` — Protects .env, DB, logs, videos, tokens
- `.env.example` — Template with all required variables
- `.env` — Configured with ENCRYPTION_KEY (NOT COMMITTED)
- `requirements.txt` — Production deps
- `requirements-dev.txt` — Dev deps (pytest, ruff, mypy, etc.)

---

## Phase 1 Implementation Summary

### Files Created
1. `src/storage/tokens.py` — Fernet encryption/decryption utilities
2. `src/storage/database.py` — SQLite + SQLAlchemy ORM, migration runner, all models
3. `src/storage/migrations/001_initial_schema.sql` — Initial schema migration
4. `src/storage/__init__.py` — Package exports
5. `tests/unit/test_tokens.py` — 14 token encryption tests
6. `tests/unit/test_database.py` — 33 database layer tests

### Database Models Implemented
- **Account** — Connected social accounts with encrypted tokens
- **Video** — Video file metadata with checksum deduplication
- **Post** — Video + caption combinations
- **PublishJob** — Per-destination publishing jobs (independent)
- **PublishAttempt** — Detailed attempt history per job
- **SchemaVersion** — Migration version tracking

### Security Implementation
- **Token Encryption**: Fernet (AES-128-GCM) via `cryptography.fernet`
- **Key Management**: `ENCRYPTION_KEY` from environment (32-byte URL-safe base64)
- **Storage**: Encrypted tokens as base64-encoded TEXT
- **No Hardcoded Keys**: Key generated at setup, never committed
- **CHECK Constraints**: Enforced at database level (platform, status values)
- **Foreign Keys**: Enforced with CASCADE DELETE (PRAGMA foreign_keys=ON)

### Migration System
- Versioned SQL files in `src/storage/migrations/`
- `schema_version` table tracks applied migrations
- `python -m src.storage.database init` — Creates tables + runs migrations
- `python -m src.storage.database migrate` — Runs pending migrations only
- Safe to run repeatedly, preserves existing data

### Test Results (Phase 1)
```
47 passed in ~2s
- test_tokens.py: 14 tests (encryption, decryption, edge cases)
- test_database.py: 33 tests (init, models, constraints, relationships, indexes, encryption integration)
```

---

## Phase 2 Implementation Summary

### Files Created
1. `src/cli/display.py` — Formatting, tables, headers, status messages
2. `src/cli/prompts.py` — Input validation (text, int, choice, yes/no, menu selection)
3. `src/cli/menu.py` — MenuHandler class with navigation, routing, 6-option menu
4. `main.py` — Entry point with argparse (--help, --dry-run, --version)
5. `tests/unit/test_cli_display.py` — 11 display utility tests
4. `tests/unit/test_cli_prompts.py` — 24 prompt/input validation tests
5. `tests/unit/test_cli_menu.py` — 14 menu navigation tests
6. `tests/unit/test_main.py` — 4 entry point tests

### CLI Architecture
- **display.py**: Pure formatting functions (no state), easy to test/replace
- **prompts.py**: Reusable input validation (text, int, choice, yes/no, menu)
- **menu.py**: MenuHandler class — loop, display, route, handle choices 1-6
- **main.py**: argparse entry point — --help, --dry-run, --version, KeyboardInterrupt handling

### Menu Behavior
```
╔════════════════════════════════════════╗
║         SOCIAL PUBLISHER v1            ║
╠════════════════════════════════════════╣
║  1. Create Post                        ║
║  2. Connected Accounts                 ║
║  3. Publishing Queue                   ║
║  4. History                            ║
║  5. Settings                           ║
║  6. Exit                               ║
╚════════════════════════════════════════╝
```
- Options 1-5: Show "not implemented yet" message, pause, return to menu
- Option 6: Clean exit with "Goodbye!" header
- Invalid input: Re-prompts with error message
- Ctrl+C: Clean exit with "Interrupted. Goodbye!"

### Input Validation
- Menu selection: Validates 1-6 (or 1-7 with exit), rejects non-numeric, out of range
- Text input: Required/optional, default values, empty string handling
- Integer input: Min/max bounds, default values, non-numeric rejection
- Yes/No: y/n, yes/no, case-insensitive, default handling
- EOF/Ctrl+C: Returns None, handled gracefully

### Test Results (Phase 1 + 2)
```
100 passed in ~2s
- test_tokens.py: 14 tests (encryption, decryption, edge cases)
- test_database.py: 33 tests (init, models, constraints, relationships, indexes, encryption integration)
- test_cli_display.py: 11 tests (headers, menus, tables, status messages)
- test_cli_prompts.py: 24 tests (text, int, choice, yes/no, menu selection, EOF, Ctrl+C)
- test_cli_menu.py: 14 tests (init, routing, handlers, run loop, edge cases)
- test_main.py: 4 tests (args parsing, dry-run, KeyboardInterrupt, exceptions)
```

---

## Phase 3A Implementation Summary

### Files Created
1. `src/accounts/manager.py` — Account CRUD, listing, filtering, status management, token encryption
2. `src/accounts/__init__.py` — Package exports
3. `src/cli/account_menu.py` — Account management submenu with 7 options
4. `tests/unit/test_account_manager.py` — 38 comprehensive tests

### Account Manager Features
- **Create Account**: Store accounts with encrypted access/refresh tokens
- **Get Account**: Retrieve by internal ID
- **Find Account**: Lookup by platform + platform_account_id (respects uniqueness constraint)
- **List Accounts**: With optional filtering by platform and/or status
- **Update Account**: Modify display info, status, tokens (re-encrypted), expiry, metadata
- **Disconnect Account**: Sets status to 'disconnected', preserves historical data
- **Enable Account**: Sets status to 'active'
- **Get Active Accounts**: Filter for status='active', optionally by platform
- **Safe Display**: `get_account_display_info()` returns sanitized info (no tokens)
- **Internal Token Access**: `get_account_with_tokens()` for publishing (raw tokens, clearly marked)
- **Development Account Creation**: Explicitly labeled dev/test accounts with placeholder tokens
- **Error Handling**: Custom exceptions (AccountError, AccountNotFoundError, DuplicateAccountError, InvalidPlatformError, InvalidStatusError)
- **Validation**: Platform must be instagram/tiktok/youtube; status must be valid enum value
- **Duplicate Prevention**: Enforces (platform, platform_account_id) uniqueness

### Account Manager Security
- **Token Encryption**: Uses existing `TokenEncryption` (Fernet AES-128-GCM)
- **No Token Exposure**: Display/list functions never return raw tokens
- **Internal Access Only**: `get_account_with_tokens()` clearly marked for internal use only
- **Encryption Error Handling**: Wraps TokenEncryptionError in AccountError

### Account Submenu CLI
```
╔════════════════════════════════════════╗
║        CONNECTED ACCOUNTS              ║
╠════════════════════════════════════════╣
║  1. List Accounts                      ║
║  2. Account Details                    ║
║  3. Create Development Account         ║
║  4. Update Account                     ║
║  5. Disconnect Account                 ║
║  6. Enable Account                     ║
║  7. Back to Main Menu                  ║
╚════════════════════════════════════════╝
```
- List Accounts: Optional platform/status filters, table display
- Account Details: Shows all safe fields (no tokens)
- Create Development Account: Explicitly labeled DEV/TEST, placeholder tokens
- Update Account: Username, display name, status
- Disconnect Account: Sets status='disconnected', preserves history
- Enable Account: Sets status='active'
- Clear warnings for dev accounts and disconnect action

### Test Results (Phase 1 + 2 + 3A)
```
138 passed in ~12s
- test_tokens.py: 14 tests (encryption, decryption, edge cases)
- test_database.py: 33 tests (init, models, constraints, relationships, indexes, encryption integration)
- test_cli_display.py: 11 tests (headers, menus, tables, status messages)
- test_cli_prompts.py: 24 tests (text, int, choice, yes/no, menu selection, EOF, Ctrl+C)
- test_cli_menu.py: 14 tests (init, routing, handlers, run loop, edge cases)
- test_main.py: 4 tests (args parsing, dry-run, KeyboardInterrupt, exceptions)
- test_account_manager.py: 38 tests (CRUD, listing, filtering, updates, disconnect, enable, dev accounts, security, edge cases)
```

---

## Phase 3B Implementation Summary

### Files Created
1. `src/auth/state.py` — OAuth state management, PKCE generation/validation
2. `src/auth/callback_server.py`: local HTTP callback server (dynamic loopback port since the audit fixes)
3. `src/auth/errors.py` — OAuth-specific exception hierarchy
4. `src/auth/base.py` — Base classes: OAuthConfig, OAuthTokenResult, PlatformAuth
4. `src/auth/manager.py` — AuthManager coordinating all platforms
5. `src/auth/errors.py` — OAuth-specific exception hierarchy
6. `src/platforms/instagram/auth.py` — Instagram/Meta OAuth flow
7. `src/platforms/tiktok/auth.py` — TikTok OAuth flow
8. `src/platforms/youtube/auth.py` — YouTube/Google OAuth flow
9. `src/auth/manager.py` — AuthManager for coordinating platforms
9. `src/auth/__init__.py` — Package exports
10. `tests/unit/test_auth_state.py` — 10 tests for state/PKCE
10. `tests/unit/test_auth_callback.py` — 7 tests for callback server
11. `tests/unit/test_platform_auth.py` — 9 tests for platform auth adapters
11. `src/cli/account_menu.py` (updated) — Integrated Connect options
11. `src/auth/__init__.py` — Package exports
12. `src/auth/manager.py` — AuthManager class
12. `src/auth/__init__.py` — Package exports
12. `main.py` (updated) — Initialize auth manager
12. `src/cli/account_menu.py` (updated) — Connect options for each platform

### OAuth Infrastructure Features
- **OAuth State Management**: In-memory store with automatic expiration (10 min default)
- **PKCE (S256)**: Cryptographically secure verifier/challenge generation
- **State Management**: Cryptographically secure random state, single-use, expires
- **Callback Server**: local HTTP server on 127.0.0.1 with a dynamic port, timeout, and state/platform validation
- **Authorization URL Generation**: Platform-specific with proper parameters
- **Token Exchange**: Authorization code → access/refresh tokens with PKCE verifier
- **Account Identity**: platform-specific identity retrieval (Instagram `/me` `user_id`, TikTok open_id, YouTube channel)
- **Token Refresh**: Automatic refresh with encrypted storage
- **Token Revocation**: Platform-specific revocation endpoints
- **AuthManager**: Central coordinator for all platform adapters

### Platform-Specific OAuth Implementations

> The original per-platform notes that were here are superseded by the 2026-09-25 audit fixes below. Authoritative details: API_INTEGRATIONS.md and AUTHENTICATION.md.

### Phase 3B Audit Fixes (2026-09-25, Claude Code)

| Audit finding | Fix |
|---------------|-----|
| TikTok PKCE used base64url | `generate_pkce_challenge(verifier, encoding)`; `TikTokAuth.PKCE_CHALLENGE_ENCODING = "hex"`; RFC 7636 stays the default |
| TikTok scopes space-separated | `SCOPE_SEPARATOR = ","` (Login Kit docs) |
| TikTok refresh-token rotation | Refresh result carries the new refresh token; `refresh_account_tokens` encrypts and stores it |
| Expiry handling (all platforms) | `OAuthTokenResult.expires_at` = now(UTC) + `expires_in`; nothing hard-coded. AuthManager previously passed `expires_in=` to `create_account` (TypeError on any real connect). Fixed. |
| Instagram on legacy Facebook Login | Business Login for Instagram: `instagram.com/oauth/authorize`, scopes `instagram_business_basic,instagram_business_content_publish`, `POST api.instagram.com/oauth/access_token`, `ig_exchange_token`, `ig_refresh_token`, identity `graph.instagram.com/v25.0/me` `user_id`. No Page linking, no PKCE (undocumented). |
| Instagram revocation via `/me/permissions` | Removed. No documented revocation endpoint, so `revoke_tokens` returns False without a request; the account is disconnected locally only |
| YouTube fixed port 8080 | `OAuthCallbackServer(port=0)` binds a free port; `redirect_uri(platform)` uses the bound port; the adapter uses the same URI for the authorize and token requests |
| State not checked against callback platform | `consume_state(state, platform=)`; the handler compares with `/callback/<platform>`; AuthManager re-checks; the state is consumed on mismatch |
| Ruff errors (audit said 23; 80 found) | 0 remaining |

Other fixes found along the way: callback server exclusive bind (Windows `SO_REUSEADDR` allowed port sharing); `/favicon.ico` could end the flow; HTML-escaped error page; thread-safe wait (`asyncio.to_thread`) instead of setting an `asyncio.Event` from another thread; reconnect updates the existing account; adapters subclass `PlatformAuth` (duplicate code removed).

### AuthManager Features
- **Platform Configuration**: Load from environment variables
- **OAuth Flow Orchestration**: Browser launch, callback server, token exchange
- **Account Creation**: Automatic account creation with encrypted token storage
- **Token Management**: Refresh, revoke, disconnect
- **Account Selection**: Get active accounts by platform for publishing

### CLI Integration
- **Connected Accounts Menu**: Added Connect Instagram/TikTok/YouTube options
- **OAuth Flow UX**: Browser opens, waits for callback, shows success/error
- **Error Handling**: Missing config, user denial, timeout, invalid state
- **Development Accounts**: Still available for testing without OAuth

### Security Implementation
- **PKCE (S256)**: YouTube (base64url) and TikTok (hex); Instagram Login does not document PKCE. Verifier kept in memory only
- **State Parameter**: Cryptographically random, single-use, 10-min expiry
- **CSRF Protection**: State validation on callback
- **PKCE Verifier**: Stored with state, cleaned up after callback
- **Token Encryption**: Fernet (AES-128-GCM) at rest
- **No Token Exposure**: Display/list functions never return raw tokens
- **Callback Server**: Binds to 127.0.0.1, timeout, clean shutdown
- **No Token Logging**: Callbacks and errors sanitized

### Test Results (Phase 1 + 2 + 3A + 3B, after audit fixes)
```
214 passed (see TESTING.md for per-module counts); ruff check .: All checks passed
Real OAuth test: NOT RUN (credentials not configured)

Before fixes: 170 passed
- test_tokens.py: 14 tests (encryption, decryption, edge cases)
- test_database.py: 33 tests (init, models, constraints, relationships, indexes, encryption integration)
- test_cli_display.py: 11 tests (headers, menus, tables, status messages)
- test_cli_prompts.py: 24 tests (text, int, choice, yes/no, menu selection, EOF, Ctrl+C)
- test_cli_menu.py: 14 tests (init, routing, handlers, run loop, edge cases)
- test_main.py: 4 tests (args parsing, dry-run, KeyboardInterrupt, exceptions)
- test_account_manager.py: 38 tests (CRUD, listing, filtering, updates, disconnect, enable, dev accounts, security, edge cases)
- test_auth_state.py: 10 tests (state, PKCE, expiration, cleanup)
- test_auth_callback.py: 7 tests (server, success, error, missing code/state, invalid state)
- test_platform_auth.py: 9 tests (config, URLs, PKCE for all 3 platforms)
```

---

## Phase 4 Implementation Summary (2026-09-25, Claude Code)

### Files created
- `src/platforms/base.py`: `PlatformPublisher` ABC, `PublishContext`, `PublishOutcome`, `PublishError(retryable, uncertain)`, `redact()`, shared HTTP/poll helpers; forces the httpx logger to WARNING
- `src/core/validation.py`: generic media validation (exists/readable/non-empty/MP4-MOV-WebM/size, optional ffprobe), streamed SHA-256
- `src/core/jobs.py`: `JobStore`: post + jobs creation, `TRANSITIONS` state machine, atomic `claim`, attempt records, provider-state lookup
- `src/core/publisher.py`: `PublisherEngine`: `publish_post`, `resume_open_jobs`, `retry_job`, `plan_post` / `plan_destinations` (dry-run)
- `src/platforms/instagram/publisher.py`, `src/platforms/tiktok/publisher.py`, `src/platforms/youtube/publisher.py`
- `src/cli/publish_menu.py`: Create Post and Publishing Queue
- `src/storage/migrations/002_publishing.sql`: `publish_jobs.options_json`, unique `(post_id, account_id)`
- Tests: `test_publishers.py` (45), `test_publishing_engine.py` (36), `test_cli_publish.py` (5)

### Files modified
- `main.py`: migrates the DB at startup, builds the engine, real `--dry-run` (plan only, no network)
- `src/cli/menu.py`: wires Create Post / Publishing Queue; passes AuthManager to Connected Accounts
- `src/storage/database.py`: per-row timestamp defaults, `options_json`, unique job index, migration runner fixes, quiet `migrate(verbose=False)`
- Tests updated where behaviour intentionally changed: two relationship tests used duplicate (post, account) jobs; the schema-version test now expects [1, 2]; the dry-run test previously asserted "not implemented"

### Bugs found and fixed (each has a regression test)
1. `main.py` called `run_menu(auth_manager=...)` but `run_menu` didn't accept it, so the app crashed on normal start (hidden because tests mocked `run_menu`)
2. Connected Accounts was opened without the AuthManager, so "Connect" always said "Auth manager not initialized"
3. `created_at`/`started_at` defaults were `datetime.now()` evaluated once at import, so every row had the same timestamp
4. The migration runner skipped any SQL chunk starting with a comment, so most of 001 never ran (masked by `create_all`)

### How it works
See ARCHITECTURE.md → "Publishing (Phase 4)" for the state machine, engine flow, idempotency table and token handling; API_INTEGRATIONS.md for verified endpoints; DECISIONS.md ADR-011..014.

---

## What Has NOT Been Implemented ❌

### Code (Phase 5+)
- No real-provider verification (OAuth or publishing)
- No History / Settings menus
- No structured logging (ADR-010)
- No `.env` loading at startup
- No TikTok creator-info export screen per TikTok UX guidelines
- No Integration/E2E tests against real providers

### Infrastructure
- No OAuth credentials configured (requires developer setup)
- No platform developer accounts set up
- No logging setup
- Publishing implemented but never run against a real provider

---

## Recent Changes

| Date | Change |
|------|--------|
| 2024-01-XX | Repository initialized |
| 2024-01-XX | Project structure created |
| 2024-01-XX | All 11 documentation files created |
| 2024-01-XX | Configuration files created |
| 2024-01-XX | Git commit 90312a5 pushed to origin |
| 2026-09-25 | **Phase 1: Database Foundation implemented** |
| 2026-09-25 | **Phase 2: CLI Framework implemented** |
| 2026-09-25 | **Phase 3A: Account Management Core implemented** |
| 2026-09-25 | **Phase 3B: OAuth Verification + Infrastructure implemented** |
| 2026-09-25 | **Phase 3B audit fixes** (TikTok hex PKCE + rotation, Instagram Login migration, dynamic loopback port, state/platform binding, expiry model, Ruff 0) |
| 2026-09-25 | **Phase 4: Publishing engine** (engine, job state machine, 3 publishers, retries, idempotent resume, dry-run, minimal CLI; 305 tests) |

---

## Architecture Summary

```
main.py → Terminal CLI → Account Manager + Job Manager → Publisher Engine → Platform Adapters → Official APIs
                                          ↓                    ↓
                                    Storage (SQLite)    Validation
```

**Key Decisions (from DECISIONS.md):**
1. Terminal-based V1 (ADR-001)
2. Isolated platform adapters (ADR-002)
3. Official APIs only (ADR-003)
4. Independent publishing jobs (ADR-004)
5. SQLite for V1 (ADR-005)
6. Fernet token encryption (ADR-006)
7. Dry-run required (ADR-007)
8. Local OAuth callback server (ADR-008)
9. No AI/generation features (ADR-009)
10. Structured logging with sanitization (ADR-010)

---

## Important Files

| File | Purpose | Status |
|------|---------|--------|
| `main.py` | Entry point | ✅ IMPLEMENTED |
| `src/cli/menu.py` | Menu navigation & routing | ✅ IMPLEMENTED |
| `src/cli/prompts.py` | Interactive prompts & validation | ✅ IMPLEMENTED |
| `src/cli/display.py` | Formatting, tables, status messages | ✅ IMPLEMENTED |
| `src/cli/account_menu.py` | Account management submenu | ✅ IMPLEMENTED |
| `src/auth/manager.py` | OAuth manager | ✅ IMPLEMENTED |
| `src/auth/state.py` | OAuth state management | ✅ IMPLEMENTED |
| `src/auth/callback_server.py` | OAuth callback server | ✅ IMPLEMENTED |
| `src/auth/base.py` | OAuth base classes | ✅ IMPLEMENTED |
| `src/auth/errors.py` | OAuth error classes | ✅ IMPLEMENTED |
| `src/storage/database.py` | DB layer, migrations, models | ✅ IMPLEMENTED |
| `src/storage/tokens.py` | Token encryption | ✅ IMPLEMENTED |
| `src/storage/migrations/001_initial_schema.sql` | Initial migration | ✅ IMPLEMENTED |
| `src/accounts/manager.py` | Account CRUD, status, encryption | ✅ IMPLEMENTED |
| `src/accounts/__init__.py` | Account package exports | ✅ IMPLEMENTED |
| `src/platforms/instagram/auth.py` | Instagram OAuth | ✅ IMPLEMENTED |
| `src/platforms/tiktok/auth.py` | TikTok OAuth | ✅ IMPLEMENTED |
| `src/platforms/youtube/auth.py` | YouTube OAuth | ✅ IMPLEMENTED |
| `src/core/jobs.py` | Job store, state machine, attempts | ✅ IMPLEMENTED |
| `src/core/publisher.py` | Publishing engine | ✅ IMPLEMENTED |
| `src/core/validation.py` | Media validation | ✅ IMPLEMENTED |
| `src/platforms/base.py` | Publisher interface | ✅ IMPLEMENTED |
| `src/cli/publish_menu.py` | Create Post / Queue CLI | ✅ IMPLEMENTED |
| `src/platforms/*/auth.py` | Platform OAuth flows | ✅ IMPLEMENTED |
| `src/platforms/*/publisher.py` | Platform publishing | ✅ IMPLEMENTED (mock-tested) |
| `data/publisher.db` | SQLite database | ✅ CREATED |
| `.env` | Runtime config | 🔧 CONFIGURED |

---

## Important Decisions

1. **No passwords ever** — OAuth only, documented in SECURITY.md, AUTHENTICATION.md
2. **Platform adapters isolated** — Core engine knows only Protocol interface
3. **Independent jobs** — One failure ≠ all fail
4. **Dry-run mandatory** — `--dry-run` shows plan without API calls
5. **SQLite + Fernet** — Simple, secure, portable
6. **Verification is dated**: OAuth specs verified 2026-09-25; publishing specs not yet re-verified

---

## Database State

**Database exists and initialized.** Schema in DATABASE.md (IMPLEMENTED):
- `accounts` — Connected accounts with encrypted tokens
- `videos` — Video file metadata with checksum deduplication
- `posts` — Video + caption combinations
- `publish_jobs` — Per-destination publishing jobs (independent)
- `publish_attempts` — Attempt history per job
- `schema_version` — Migration tracking

Migration system: versioned SQL files in `src/storage/migrations/`

---

## Authentication State

**OAuth implemented for all three platforms; mock-tested; real OAuth NOT RUN.** Design in AUTHENTICATION.md:
- State (single-use, platform-bound) for all platforms; PKCE for YouTube (base64url) and TikTok (hex)
- Loopback callback server on `http://127.0.0.1:<dynamic-port>/callback/{platform}` (Instagram: fixed registered `INSTAGRAM_REDIRECT_URI`)
- `expires_at` from provider `expires_in`; `is_token_expiring()` helper; renewal via `AuthManager.refresh_account_tokens()`, called by the publishing engine before each job
- Encrypted storage via Fernet

---

## API Integration State

**OAuth and publishing endpoints verified against official documentation on 2026-09-25.** Real provider behaviour NOT verified.

| Platform | OAuth product | Docs verified | Mock tests | Real OAuth |
|----------|---------------|---------------|------------|------------|
| Instagram | Instagram API with Instagram Login | ✅ 2026-09-25 | ✅ | NOT RUN |
| TikTok | Login Kit for Desktop | ✅ 2026-09-25 | ✅ | NOT RUN |
| YouTube | Google OAuth 2.0 installed app | ✅ 2026-09-25 | ✅ | NOT RUN |

---

## Tests

**305 unit tests passing; Ruff clean; 0 skipped.** TESTING.md defines strategy:
- Unit: validation, models, encryption, state machine, CLI parsing
- Integration: database, account manager, publisher engine
- Platform: mocked API tests per adapter
- E2E: full publish flow (manual/CI)

Run: `pytest tests/unit/ -v`

---

## Known Issues

| Issue | Impact | Workaround |
|-------|--------|------------|
| Real OAuth not run on any platform | Provider behaviour unconfirmed | Configure dev apps + credentials, connect each platform once |
| Real publishing not run on any platform | Upload/publish behaviour unconfirmed | One private test post per platform (Phase 5) |
| Instagram needs a public https `video_url` | Local files can't go to Instagram | Host the file (ADR-012) |
| TikTok unaudited app | Only `SELF_ONLY` posts | TikTok app audit |
| YouTube unverified project | Uploads forced private | Google API project verification |
| Instagram redirect URI: Meta may require HTTPS | Plain loopback callback may be rejected | Check the dashboard; use an HTTPS tunnel/redirect if required |
| `main.py` does not call `load_dotenv()` | `.env` values are not picked up automatically | Export variables in the shell (or add `load_dotenv()`) |
| TikTok `refresh_expires_in` not persisted | Can't warn before the refresh token expires | Add a column in a later migration |
| No logging setup | No observability | Add in Phase 5 |
| History / Settings menus | Show "not implemented" | Phase 6 |

---

## Known Limitations

- Single-user CLI tool (no multi-user)
- No web UI (terminal only)
- No scheduling (publish now only)
- No analytics/insights
- No bulk operations beyond multi-account select
- Platform API limits apply (quotas, rate limits)

---

## Current Blockers

**None for code.** Real OAuth and publishing verification need developer credentials (Phase 5).

---

## Next Recommended Task

### Phase 5: Real Provider Verification & Hardening

1. Configure developer apps (Meta Instagram Login app, TikTok Login Kit for Desktop + Content Posting API, Google Desktop OAuth client) and export credentials
2. Connect one test account per platform (real OAuth)
3. Publish one private test post per platform: TikTok `SELF_ONLY`, YouTube `private`, Instagram test account with a hosted `video_url`
4. Fix anything real providers reveal; record the results in API_INTEGRATIONS.md
5. Then: TikTok export-screen UX, `.env` loading, sanitized logging, History menu

---

## How To Continue

1. **Read PROJECT_STATUS.md** — Current progress tracker
2. **Read ARCHITECTURE.md** — Understand component boundaries
3. **Read API_INTEGRATIONS.md**: OAuth + publishing verified against docs 2026-09-25; real provider runs still pending
4. **Verify `.env`** has ENCRYPTION_KEY set
5. **Run tests** to confirm baseline: `pytest tests/unit/ -v`
6. **Do Phase 5** (above). Don't claim real publishing works until it has actually been run
7. **Run tests** after each component
8. **Update PROJECT_STATUS.md** and **this file** after each meaningful change

---

## Things NOT To Break

1. **`.gitignore`** — Must always protect `.env`, `data/*.db`, `logs/`, `videos/`, tokens
2. **Documentation accuracy** — Never mark PLANNED as IMPLEMENTED
3. **API_INTEGRATIONS.md VERIFIED tags** — Don't remove without verification
4. **Security principles** — No passwords, no secrets in logs, no .env commits
5. **Architecture boundaries** — Platform code stays in adapters, core stays platform-agnostic
6. **Independent jobs** — Don't couple job execution
7. **Dry-run** — Must remain functional throughout development

---

## Verification Checklist (Run After Each Task)

- [ ] `git status` clean (only intended changes)
- [ ] No secrets in diff (`git diff` check)
- [ ] Tests pass (`pytest tests/unit/`)
- [ ] Lint passes (`ruff check .`)
- [ ] Type check passes (`mypy src`)
- [ ] PROJECT_STATUS.md updated
- [ ] CLAUDE_HANDOFF.md updated
- [ ] Relevant docs updated
- [ ] No regression in existing functionality