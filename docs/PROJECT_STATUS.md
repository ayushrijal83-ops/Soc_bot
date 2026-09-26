# Project Status

## Current Phase
**Phase 5B: Real TikTok OAuth + SELF_ONLY Publishing Verification**: 🔄 **CODE READY; REAL TEST BLOCKED on TikTok developer credentials (2026-09-25)**

| Claim | Status |
|-------|--------|
| TikTok docs re-verified | ✅ 2026-09-25 |
| TikTok OAuth / publishing tested with a mocked TikTok API | ✅ (402 tests, Ruff clean) |
| Real TikTok OAuth | ❌ NOT RUN: `TIKTOK_CLIENT_KEY/SECRET` empty, no developer app yet |
| Real TikTok SELF_ONLY publish | ❌ NOT RUN (depends on the above) |
| Real YouTube OAuth / publishing / thumbnail | ✅ (Phase 5, 5A) |
| Real Instagram | ❌ NOT RUN (Phase 5C) |

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

### Phase 5: Real Provider Verification (IN PROGRESS, started 2026-09-25)

**YouTube configuration: done (real OAuth NOT RUN yet)**
- [x] Google Cloud OAuth client (Desktop app) created by the user; `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` are in the project `.env` (values never printed or committed)
- [x] `main.py` loads the project `.env` at startup (`load_environment()`, python-dotenv, `override=False`) before any configuration is read, so real environment variables take priority
- [x] Verified without printing values: `.env` loaded, YouTube configured, the only configured platform, dynamic loopback redirect (`YOUTUBE_REDIRECT_URI` empty), callback 127.0.0.1:0
- [x] `.gitignore` now also ignores `secrets/` and `client_secret*.json`. A downloaded `secrets/youtube_client.json` was untracked and not ignored.
- [x] Tests: `.env` loading, real-env priority, missing file, YouTube configured from `.env`, loaded before services start (fake values only; tests never read the real `.env`)
- [x] `ENCRYPTION_KEY` set by the user
- [x] Real YouTube OAuth: 2 channels connected (user)
- [x] Real YouTube upload: done by the user, and again in the Phase 5A regression (private)
- Instagram / TikTok: not configured, untouched

### Phase 5A: Smart Content Intake — **COMPLETE** ✅
- [x] `src/content/` (models, detector, validator, manager, profile, intake) and `src/cli/content_menu.py`
- [x] Migration 003: `content_items`, `publishing_profiles`, `publish_jobs.cover_status/cover_error`
- [x] Content Inbox (menu 5), Settings → publishing profile (menu 6), History (menu 4), `--scan`, dry-run for content
- [x] VERIFY (one confirmation) and AUTO (no confirmation, validation still enforced)
- [x] Lifecycle incoming → publishing → published/archive | failed; crash resume; retry of failed destinations only
- [x] Duplicate protection: unique content key + unique (post, account) job
- [x] Cover capability model; YouTube `thumbnails.set`; TikTok truthfully `not_supported` (Instagram: supported since 2026-09-26 via `cover_url`)
- [x] Bugs fixed: the main menu's prompt added a second, dead "Exit" option (regression test); a `.gitignore` `content/` pattern would have hidden `src/content/` (anchored to `/content/`)
- [x] Real YouTube regression: private upload `rAGivy-c3DM` + custom thumbnail published; a second publish of the same package was refused

### Phase 5B: Real TikTok Verification — 🔄 code complete, real run pending
- [x] Docs re-verified; implementation matches (hex PKCE, scopes, loopback redirect, creator_info → init → chunked PUT → status)
- [x] **Bug fixed:** TikTok v2 `"error": {"code": "ok"}` envelope made every real connection fail at identity lookup
- [x] Least-privilege scopes `user.info.basic,video.publish`; granted scopes stored and checked (`REQUIRED_SCOPES`)
- [x] creator_info preflight on the VERIFY screen (privacy options, max duration); never in dry-run
- [x] Connect CLI: "Connecting TikTok... / Browser authorization required / TikTok account connected." + granted scopes + missing-scope warning
- [x] Duplicate rule refined: an already-published video can go to newly added profile accounts only (ADR-021)
- [x] Test package `content/incoming/tiktok_test/` (video + caption, no cover) prepared
- [ ] User: create the TikTok app, fill `.env`, connect the account, save the profile (SELF_ONLY)
- [ ] Real SELF_ONLY publish via Content Inbox, then record the result here

### Phase 5C: Instagram OAuth audit & fix — 🔄 code fixed, real OAuth NOT yet tested
- [x] **Root cause of "Sorry, this page isn't available":** `.env` `INSTAGRAM_APP_ID/SECRET` = Meta app "Soc_bot" credentials, not the Instagram App ID/secret (verified: Graph API accepts the pair as Meta app "Soc_bot"; the Instagram token endpoint answers "Invalid platform app")
- [x] Instagram now requires a registered `INSTAGRAM_REDIRECT_URI` (dynamic ports can never match); https paste mode + GitHub Pages callback page added
- [x] Fixed: `permissions` returned as a list would have crashed scope parsing after the exchange; `#_` stripped from codes; the "Invalid platform app" error now explains the fix
- [x] 23 new Instagram OAuth tests; 425 total passing; TikTok/YouTube files untouched
- [ ] User: put the Instagram App ID/secret + `INSTAGRAM_REDIRECT_URI` in `.env`; register the redirect in Business login settings; add @nopex_12b to the app; push `docs/oauth/instagram-callback.html` (paste mode)
- [ ] Real Instagram OAuth (Connected Accounts → Connect Instagram)

### Instagram local media delivery — ✅ implemented, real run pending storage credentials (2026-09-25)
- [x] Audit: the YouTube Data API exposes no direct media URL; watch/Shorts URLs are HTML pages and aren't valid Instagram `video_url`s; scraping/yt-dlp is prohibited
- [x] `src/media_storage/`: `ObjectStorage` + `S3ObjectStorage` (boto3; S3/R2/MinIO), `MediaSourceProvider` + `ObjectStorageMediaProvider`, settings/factory
- [x] `InstagramPublisher` takes the local file: upload → presigned HTTPS URL → container → poll → publish → delete (`finally`); fresh media per attempt; nothing secret persisted or logged
- [x] Create Post no longer asks for a URL; content packages no longer use `video_url.txt`; dry-run makes no storage calls; Settings → Check Instagram media storage
- [x] Bug fixed: re-used video rows kept a stale path (`Video file not found: video.mp4`), which broke real job 4
- [x] Bug fixed: storage 4xx (AccessDenied) was treated as retryable
- [x] 39 new tests; 466 total passing; YouTube/TikTok files untouched
- [x] 0x0.st provider (`MEDIA_STORAGE_PROVIDER=0x0`): public temporary upload, token delete, 56 tests (522 total)
- [x] TempFile.org provider (`MEDIA_STORAGE_PROVIDER=tempfile`), 52 tests; 0x0.st uploads are disabled by the service
- [x] **Real Instagram publish ✅ (2026-09-25):** Create Post → local `videos/test_youtub.mp4` → TempFile → Reel on noxivra_01 (media id 17981564450901718, https://www.instagram.com/reel/DdttFw8CqnI/); TempFile copy deleted; no URL prompt; nothing stored in the DB
- [x] Size-based routing `MEDIA_STORAGE_PROVIDER=auto` (TempFile ≤ 99 MB, S3 above; provider capabilities; no failure fallback), 28 tests; **602 passing**
- [x] Real small-video test in auto mode: TempFile → Instagram published (job 7, media 18073405427476313), copy deleted
- [ ] **Real large-video S3 → Instagram test: NOT DONE**, because S3 isn't configured (no `MEDIA_STORAGE_*` credentials). A valid 150 MB test MP4 is correctly routed away from TempFile and blocked until S3 is set up
- [ ] **Real two-Instagram-account test: NOT DONE**, because only one Instagram account is connected
- [ ] User: set `MEDIA_STORAGE_PROVIDER=auto` in `.env` (+ S3 settings for videos over 99 MB)

### Cloudflare Quick Tunnel media provider: ✅ implemented, real tunnel test + real Instagram publish passed (2026-09-25)
- [x] Free research: qurl.sh 413, temp.sh (GET = HTML page), file.io 405, Litterbox 500, Pixeldrain (hotlinking premium-only), Filebin (cookie wall), so none can host 131.9 MB
- [x] `src/media_storage/cloudflare_tunnel.py`: 127.0.0.1 single-file server (random 128-bit route, GET/HEAD/Range, streamed, 404 elsewhere, no request logs) + `cloudflared tunnel --url` child process, URL parsing, startup timeout, public reachability wait, cleanup never raises
- [x] `MEDIA_STORAGE_PROVIDER=cloudflare_tunnel`; AUTO = TempFile → Cloudflare Tunnel → S3 (0x0 never); plan/dry-run start nothing; Create Post notice
- [x] Instagram polling window grows with size (131.9 MB → 9 min, cap `INSTAGRAM_MAX_POLL_MINUTES`=15)
- [x] Real local check: 131,925,281-byte `videos/0612.mp4` served on 127.0.0.1, HEAD/GET 200 `video/mp4`, SHA-256 identical, 404 elsewhere, server gone after cleanup
- [x] 62 new tests; **701 passing**
- [x] **Real direct tunnel test ✅ (2026-09-25, cloudflared 2026.9.3):** `videos/0612.mp4` through a real Quick Tunnel. Public GET 200 `video/mp4`, Content-Length 131,925,281, SHA-256 identical (9a28387d…0b7d), Range 206, other paths 404, ~10.5 MB/s; after cleanup cloudflared is gone, the URL returns 502 and the source is unchanged
- [x] Bugs found by the real run and fixed: the parser took `https://api.trycloudflare.com` (printed by cloudflared) as the tunnel URL; the readiness check used the local resolver, which cached "no such host" (trycloudflare.com negative TTL 60 s). Now: hyphenated names only, public DoH (2 resolvers), first lookup after 5 s, default timeout 90 s
- [x] **Real Instagram publish through the tunnel ✅ (2026-09-25):** Create Post (AUTO) → `videos/0612.mp4` (131.9 MB) → Cloudflare Quick Tunnel → Reel on noxivra_01. Post 8 / job 9, media id 18155048788569324, https://www.instagram.com/reel/DduAxFSgaZt/, 84 s end to end. Link saved to `instagram.json`; no cloudflared left; tunnel URL in no log, DB or link file; source unchanged (same SHA-256)

### Instagram fan-out + one cover for all accounts: ✅ implemented (2026-09-26)
- [x] Instagram `cover_url` (JPEG ≤ 8 MB, stdlib structure check; invalid cover BLOCKS), served with the video on the same per-job tunnel
- [x] Router is cover-aware (`supports_cover`; only the Cloudflare Quick Tunnel today)
- [x] Create Post: cover prompt, multi-select accounts (A/N/toggle/ranges), one batch summary (valid/invalid, concurrency, provider, bandwidth), ONE confirmation, grouped duplicate question
- [x] Engine: Instagram jobs in a pool of `INSTAGRAM_MAX_CONCURRENT_PUBLISHES` (default 5); failure isolation; resume skips published/failed; Ctrl+C leaves queued jobs pending; published-links writes locked
- [x] Publishing Queue: batches (post) with aggregate status and per-job ✓/✗ lines, no URLs
- [x] 45 new tests; **746 passing**
- [x] Real TEST 1 (10 MB + cover → noxivra_01): https://www.instagram.com/reel/DdvCpWvEspn/, cover verified by API `thumbnail_url` readback
- [x] Real TEST 2 (131.9 MB 0612.mp4 + cover → noxivra_01): https://www.instagram.com/reel/DdvDAyhCGzR/, cover verified (same thumbnail bytes), sources unchanged
- [x] **Real 11-account fan-out ✅ (2026-09-26, post 11, jobs 12-22):** 10.1 MB video + one cover → all 11 connected accounts (noxivra_01-10, noxivra_100), concurrency 5. Observed max 5 running / 5 cloudflared; slots refilled as jobs finished; 11/11 published in 4 min 10 s; all 11 covers verified by API thumbnail readback; 11 links saved; sources unchanged
- [x] **Real 5-account run ✅ (post 12, jobs 23-27):** `videos/1114(1).mp4` (40.0 MB, the user's chosen test video; `0612.mp4` is no longer in the project) + one cover → noxivra_02-06, all 5 concurrent (5 cloudflared), 5/5 published in 1 min 24 s, covers verified, sources unchanged. A >99 MB × 5 run was not repeated
- [~] Real TEST 5 (resume): published jobs were not touched, but the run also resumed a **stale** RETRYING job from 2026-09-25 (post 4 / job 5) and published an unintended Reel (https://www.instagram.com/reel/DdvDNpKDoMk/)

### Published-Link Library — ✅ implemented (2026-09-25)
- [x] `content/published_links/{youtube,instagram,tiktok}.json`, atomic UTF-8 writes, malformed-file quarantine, account+provider_id dedupe, temporary-URL rejection
- [x] YouTube watch URL from the video id; Instagram `permalink` fetched after publish; TikTok: no link (no documented URL)
- [x] Main menu 6 "Published Links" (list / copy to clipboard / import from history); Settings = 7, Exit = 8
- [x] Real verification (no new publish): import from history saved 3 YouTube + 3 Instagram links (real Graph permalinks), 0 TikTok; a second import added 0; clip.exe copy checked with Get-Clipboard
- [x] 37 new tests; **639 passing**

### In Progress 🔄
- Phase 5: Instagram / TikTok real verification (not configured)

### Planned 📋

#### Phase 5: Real Provider Verification & Hardening
- [ ] Configure developer apps + credentials; run real OAuth for each platform
- [ ] Real private test post per platform (TikTok `SELF_ONLY`, YouTube `private`, Instagram test account)
- [ ] TikTok export screen per its UX guidelines (creator nickname, live privacy options, interaction toggles)
- [x] Load `.env` at startup
- [ ] Structured, sanitized logging (ADR-010)
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
- **746 unit tests passing** in `tests/unit/` (see TESTING.md for the breakdown); `ruff check .`: 0 errors

## Known Limitations
- Real OAuth: NOT RUN for any platform (no credentials configured). Mocked tests are not provider verification.
- Instagram needs a fixed, registered `INSTAGRAM_REDIRECT_URI`. Meta may require HTTPS for it, which a plain loopback server cannot serve; check before the first real login.
- `.env` loading: done in Phase 5 (`main.load_environment`, real environment wins)
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
**Phase 5B: real TikTok (then Instagram) verification.** Configure a TikTok Login Kit for Desktop + Content Posting API app, connect a test account, and publish a `SELF_ONLY` package through the Content Inbox. For Instagram, decide how videos get a public `video_url`. Optional: a background inbox watcher that calls `ContentIntake.scan()` / `publish_ready()`.
