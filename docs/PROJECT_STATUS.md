# Project Status

## Current Phase
**Phase 3B: Official OAuth Verification + OAuth Infrastructure**: ✅ **COMPLETE, audit fixes applied 2026-09-25**

| Claim | Status |
|-------|--------|
| OAuth implemented (Instagram, TikTok, YouTube) | ✅ |
| Endpoints verified against current official docs | ✅ 2026-09-25 |
| OAuth tested with mocks | ✅ 214 unit tests |
| Real provider OAuth tested | ❌ NOT RUN (credentials not configured) |
| Publishing implemented | ❌ Phase 4 |

## Implementation State

### Completed ✅
- Repository initialized with MIT License
- Project directory structure created:
  - `src/cli/`, `src/core/`, `src/accounts/`, `src/storage/`, `src/auth/`
  - `src/platforms/instagram/`, `src/platforms/tiktok/`, `src/platforms/youtube/`
  - `docs/`, `data/`, `tests/`, `videos/`, `logs/`
- Comprehensive documentation suite created:
  - README.md (main entry point)
  - ARCHITECTURE.md (system design)
  - PROJECT_STATUS.md (this file)
  - API_INTEGRATIONS.md (platform API reference)
  - AUTHENTICATION.md (OAuth design)
  - DATABASE.md (schema design - **IMPLEMENTED**)
  - DEVELOPMENT.md (dev setup)
  - TESTING.md (test strategy)
  - SECURITY.md (security practices)
  - DECISIONS.md (ADR log)
  - TROUBLESHOOTING.md (issue resolution)
  - CLAUDE_HANDOFF.md (agent context)
- Configuration files:
  - .gitignore (protects .env, tokens, secrets)
  - .env.example (template)
  - requirements.txt (dependencies)
  - requirements-dev.txt (dev dependencies)

### Phase 1: Foundation — **COMPLETE** ✅
- [x] SQLite database layer with migrations (`src/storage/database.py`)
- [x] Token encryption utilities (`src/storage/tokens.py`)
- [x] Base models for Account, Video, Post, PublishJob, PublishAttempt
- [x] Configuration loading from .env
- [x] Database initialization CLI (`python -m src.storage.database init`)
- [x] Migration system with version tracking
- [x] Unit tests for database layer (47 tests passing)

### Phase 2: CLI Framework — **COMPLETE** ✅
- [x] Main entry point (`main.py`)
- [x] Menu system (`src/cli/menu.py`)
- [x] Interactive prompts (`src/cli/prompts.py`)
- [x] Display utilities (`src/cli/display.py`)
- [x] Input validation (menu selection, yes/no, required text, empty input, invalid numbers)
- [x] Ctrl+C handling with clean exit
- [x] Dry-run flag placeholder (`--dry-run`)
- [x] Unit tests for CLI framework (53 new tests)
- [x] All 100 unit tests passing

### Phase 3A: Account Management Core — **COMPLETE** ✅
- [x] Account Manager (`src/accounts/manager.py`)
- [x] Account CRUD operations (create, read, update, delete/disconnect)
- [x] Account listing with filtering (by platform, status)
- [x] Account search by platform + platform_account_id
- [x] Token encryption/decryption using existing Fernet infrastructure
- [x] Safe account display (no tokens in output)
- [x] Account status management (active, expired, revoked, disconnected)
- [x] Development/test account creation (explicitly labeled, no real OAuth)
- [x] Connected Accounts CLI submenu (list, details, create dev, update, disconnect, enable)
- [x] Account selection logic for future publishing
- [x] 38 unit tests for Account Manager
- [x] Total 138 unit tests passing

### Phase 3B: Official OAuth Verification + OAuth Infrastructure — **COMPLETE** ✅
- [x] OAuth callback server (127.0.0.1, dynamic port, exclusive bind)
- [x] Instagram OAuth flow (`src/platforms/instagram/auth.py`): Instagram API with Instagram Login, docs verified 2026-09-25
- [x] TikTok OAuth flow (`src/platforms/tiktok/auth.py`): Login Kit for Desktop (hex PKCE), docs verified 2026-09-25
- [x] YouTube OAuth flow (`src/platforms/youtube/auth.py`): Google installed-app loopback flow, docs verified 2026-09-25
- [x] Shared OAuth infrastructure (`src/auth/`): state management, PKCE, callback server, error handling
- [x] OAuth configuration from environment variables
- [x] Authorization URL generation with platform-specific PKCE (base64url / hex / none)
- [x] Token exchange with PKCE verifier (where supported)
- [x] Account identity retrieval from platform APIs
- [x] Token renewal (TikTok/YouTube refresh; Instagram `ig_refresh_token` re-exchange) and revocation where supported
- [x] AuthManager for coordinating all platforms
- [x] Connected Accounts CLI integration (Connect Instagram/TikTok/YouTube)
- [x] Platform credential validation and configuration
- [x] OAuth state management: single-use and platform-bound (CSRF)
- [x] Callback server with timeout, error handling, CSRF protection
- [x] Total 214 unit tests passing; `ruff check .` clean

#### Phase 3B audit fixes (2026-09-25)
- [x] TikTok PKCE uses a hex SHA-256 challenge (shared generator gained an `encoding` option; others stay RFC 7636)
- [x] TikTok scopes comma-separated; rotated refresh tokens persisted; `expires_in` respected
- [x] Instagram migrated from Facebook Login to Business Login for Instagram (authorize URL, scopes, api.instagram.com code exchange, `ig_exchange_token`, `ig_refresh_token`, `/me` `user_id` identity, honest "no revocation endpoint")
- [x] YouTube and all platforms use a dynamic loopback port; the redirect URI is built from the bound port
- [x] Callback rejects a state issued for a different platform before any code exchange
- [x] `expires_at` computed from provider `expires_in` (timezone-aware UTC); was previously passed as an unsupported `expires_in=` kwarg, so real connects would have crashed
- [x] Reconnecting an existing account updates it instead of raising a duplicate error
- [x] Callback server: exclusive bind, non-callback paths ignored, HTML-escaped errors, thread-safe wait
- [x] Adapters now subclass `PlatformAuth` (duplicate PKCE/validation code removed)
- [x] 80 Ruff errors fixed (0 remaining)

### In Progress 🔄
- None

### Planned 📋

#### Phase 4: Publishing Engine
- [ ] Job Manager (`src/core/jobs.py`)
- [ ] Publisher Engine (`src/core/publisher.py`)
- [ ] Validation (`src/core/validation.py`)
- [ ] Platform adapter interface (`src/platforms/base.py`)

#### Phase 5: Platform Adapters
- [ ] Instagram adapter (`src/platforms/instagram/`)
- [ ] TikTok adapter (`src/platforms/tiktok/`)
- [ ] YouTube adapter (`src/platforms/youtube/`)

#### Phase 6: Integration & Polish
- [ ] End-to-end publishing flow
- [ ] Dry-run mode
- [ ] Retry logic
- [ ] History display
- [ ] Integration tests

### Blocked 🚫
- None currently

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
| `src/storage/database.py` | Database layer | ✅ IMPLEMENTED |
| `src/storage/tokens.py` | Token encryption | ✅ IMPLEMENTED |
| `src/storage/migrations/001_initial_schema.sql` | Initial migration | ✅ IMPLEMENTED |
| `src/accounts/manager.py` | Account management core | ✅ IMPLEMENTED |
| `src/accounts/__init__.py` | Account package exports | ✅ IMPLEMENTED |
| `src/platforms/instagram/auth.py` | Instagram OAuth | ✅ IMPLEMENTED |
| `src/platforms/tiktok/auth.py` | TikTok OAuth | ✅ IMPLEMENTED |
| `src/platforms/youtube/auth.py` | YouTube OAuth | ✅ IMPLEMENTED |
| `src/core/jobs.py` | Job management | 📋 PLANNED |
| `src/core/publisher.py` | Publisher engine | 📋 PLANNED |
| `src/core/validation.py` | Media validation | 📋 PLANNED |
| `src/platforms/*/publisher.py` | Platform publishing | 📋 PLANNED |
| `data/publisher.db` | SQLite database | ✅ CREATED |
| `.env` | Environment config | 🔧 CONFIGURED |

## Current Tests
- **214 unit tests passing** in `tests/unit/` (see TESTING.md for the breakdown); `ruff check .`: 0 errors

## Known Limitations
- Real OAuth: NOT RUN for any platform (no credentials configured). Mocked tests are not provider verification.
- Instagram needs a fixed, registered `INSTAGRAM_REDIRECT_URI`. Meta may require HTTPS for it, which a plain loopback server cannot serve; check before the first real login.
- `main.py` does not load `.env` (python-dotenv is installed but not called), so variables must be set in the shell environment
- TikTok `refresh_expires_in` is not persisted (no schema column)
- Proactive token refresh before use is not scheduled yet (Phase 4 will call `is_token_expiring` / `refresh_account_tokens`)
- No logging setup yet
- Publishing features are placeholders only

## Next Recommended Task
**Phase 4: Publishing Engine** (`src/core/jobs.py`, `src/core/publisher.py`, `src/core/validation.py`, `src/platforms/base.py`)

This establishes the job management and publishing orchestration layer.