# Project Status

## Current Phase
**Phase 4: Publishing Engine**: ✅ **COMPLETE (code + mocked tests), 2026-09-25**

| Claim | Status |
|-------|--------|
| OAuth implemented (Instagram, TikTok, YouTube) | ✅ |
| OAuth + publishing endpoints verified against current official docs | ✅ 2026-09-25 |
| OAuth + publishing tested with mocks | ✅ 305 unit tests |
| Real provider OAuth tested | ❌ NOT RUN (credentials not configured) |
| Publishing implemented | ✅ Phase 4 |
| Real provider publishing tested | ❌ NOT RUN. **Mocked tests verified. Real provider publishing NOT verified.** |

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

### Phase 4: Publishing Engine — **COMPLETE** ✅ (mocked tests only)
- [x] Publishing APIs verified against official docs (2026-09-25); see API_INTEGRATIONS.md
- [x] `src/platforms/base.py`: `PlatformPublisher` interface, `PublishError` (retryable/uncertain), `redact()`
- [x] `src/core/validation.py`: generic media validation (+ ffprobe metadata when installed)
- [x] `src/core/jobs.py`: post/job creation, job state machine, atomic claim, attempt records
- [x] `src/core/publisher.py`: engine with failure isolation, bounded retries, token renewal, resume, dry-run plan
- [x] Instagram publisher: Reels via container (`video_url`) → status polling → `media_publish`
- [x] TikTok publisher: creator info → Direct Post init → chunked upload → status polling
- [x] YouTube publisher: resumable chunked upload with offset resume → processing polling
- [x] Idempotency: provider IDs persisted before each next step; resume instead of re-post; uncertain outcomes never auto-retried
- [x] Migration 002 (`publish_jobs.options_json`, unique `(post_id, account_id)`)
- [x] CLI: Create Post (plan → confirm → live status) and Publishing Queue (list / continue / retry)
- [x] `python main.py --dry-run`: validates unpublished posts and prints the plan; never contacts a platform
- [x] Bugs found and fixed (regression-tested): `main.py` crashed calling `run_menu(auth_manager=...)`; Connected Accounts never received the AuthManager; timestamp defaults were fixed at import time; the migration runner skipped comment-prefixed SQL statements
- [x] 305 unit tests passing, Ruff clean

### In Progress 🔄
- None

### Planned 📋

#### Phase 5: Real Provider Verification & Hardening
- [ ] Configure developer apps + credentials; run real OAuth for each platform
- [ ] Real private test post per platform (TikTok `SELF_ONLY`, YouTube `private`, Instagram test account)
- [ ] TikTok export screen per its UX guidelines (creator nickname, live privacy options, interaction toggles)
- [ ] Load `.env` at startup; structured, sanitized logging (ADR-010)
- [ ] Handle a 401 mid-upload by refreshing and retrying once

#### Phase 6: Integration & Polish
- [ ] History and Settings menus
- [ ] Parallel job execution (optional; ADR-011)
- [ ] Integration/e2e tests against provider sandboxes where they exist

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
| `src/core/jobs.py` | Job store & state machine | ✅ IMPLEMENTED |
| `src/core/publisher.py` | Publisher engine | ✅ IMPLEMENTED |
| `src/core/validation.py` | Media validation | ✅ IMPLEMENTED |
| `src/platforms/base.py` | Publisher interface | ✅ IMPLEMENTED |
| `src/platforms/*/publisher.py` | Platform publishing | ✅ IMPLEMENTED (mock-tested) |
| `src/cli/publish_menu.py` | Create Post / Publishing Queue | ✅ IMPLEMENTED |
| `src/storage/migrations/002_publishing.sql` | Publishing migration | ✅ IMPLEMENTED |
| `data/publisher.db` | SQLite database | ✅ CREATED |
| `.env` | Environment config | 🔧 CONFIGURED |

## Current Tests
- **305 unit tests passing** in `tests/unit/` (see TESTING.md for the breakdown); `ruff check .`: 0 errors

## Known Limitations
- Real OAuth: NOT RUN for any platform (no credentials configured). Mocked tests are not provider verification.
- Instagram needs a fixed, registered `INSTAGRAM_REDIRECT_URI`. Meta may require HTTPS for it, which a plain loopback server cannot serve; check before the first real login.
- `main.py` does not load `.env` (python-dotenv is installed but not called), so variables must be set in the shell environment
- TikTok `refresh_expires_in` is not persisted (no schema column)
- Real publishing: NOT RUN for any platform. Mocked tests verified; real provider publishing NOT verified.
- Instagram publishing needs a public https `video_url` (no local upload with Instagram Login; ADR-012)
- TikTok: unaudited apps can only post `SELF_ONLY`; the CLI does not yet render TikTok's full creator-info export screen
- YouTube: unverified Google projects upload as private; daily upload quota; a 401 during a very long upload is not handled
- Jobs run sequentially (ADR-011); a single CLI process is assumed (a job found `uploading` at start is treated as crashed)
- Retry back-off (30/60/120 s) runs in the foreground while the CLI waits
- ffprobe is optional; without it duration/resolution are not checked locally
- No logging setup yet; History and Settings menus not implemented

## Next Recommended Task
**Phase 5: Real Provider Verification & Hardening.** Configure developer credentials, run real OAuth, and make one private test post per platform, then fix whatever real providers reveal. Mocked tests can't confirm provider behaviour.
