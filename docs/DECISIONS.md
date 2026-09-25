# Architecture Decision Records (ADRs)

## ADR-001: V1 is Terminal-Based

**Date:** 2024-01-XX
**Status:** Accepted

### Context
Need to choose UI paradigm for V1.

### Decision
V1 will be a terminal-based CLI application using Python's standard library + `rich` for formatting.

### Rationale
- Fast development: no frontend build, no browser compatibility
- Clean separation: publishing engine independent of UI
- Portable: runs on servers, CI, headless environments
- User base: developers/technical users comfortable with CLI
- Future UI (web, desktop) can reuse core engine

### Consequences
- Limited accessibility for non-technical users
- No drag-and-drop, rich media preview
- Menu navigation via keyboard only

---

## ADR-002: Platform Adapters Are Isolated

**Date:** 2024-01-XX
**Status:** Accepted

### Context
Instagram, TikTok, YouTube APIs differ significantly in auth, upload, publishing.

### Decision
Each platform gets its own adapter package (`src/platforms/{instagram,tiktok,youtube}/`) implementing a common `PlatformAdapter` protocol. Core engine knows only the protocol.

### Rationale
- Instagram: Graph API, container-based, requires Facebook Page
- TikTok: Creator API, direct upload URL, draft/publish separation
- YouTube: Resumable upload, quota-based, long processing
- Isolated changes: TikTok API update doesn't break Instagram
- Testable: Mock each adapter independently
- Replaceable: Swap adapter without touching core

### Consequences
- Some code duplication (HTTP clients, retry logic)
- Need shared base classes for common patterns
- Adapter protocol must be stable

---

## ADR-003: Official APIs Only

**Date:** 2024-01-XX
**Status:** Accepted

### Context
Unofficial APIs (scraping, private endpoints) exist but are unreliable.

### Decision
Use only official, documented, supported APIs:
- Instagram: Instagram Graph API (Meta)
- TikTok: TikTok Creator API / Content Posting API
- YouTube: YouTube Data API v3

### Rationale
- Reliability: Official APIs have SLAs, deprecation notices
- Security: No credential phishing, no session hijacking
- Compliance: Terms of service compliant
- Maintainability: Documented, versioned, supported
- Account safety: No risk of platform bans

### Consequences
- Stricter account requirements (Business/Creator accounts)
- OAuth setup required per platform
- Rate limits and quotas apply
- Some features unavailable (e.g., Instagram personal accounts)

---

## ADR-004: Independent Publishing Jobs

**Date:** 2024-01-XX
**Status:** Accepted

### Context
User selects multiple accounts across platforms for one post.

### Decision
Each (post, account) combination creates an independent `PublishJob`. Jobs execute in parallel. Failure of one job does not affect others.

### Rationale
- User expects partial success (e.g., 3 of 4 accounts published)
- Retry per destination, not per post
- Clear status per destination in history
- Parallel execution = faster overall publishing
- Matches mental model: "post to these accounts"

### Consequences
- More database records (1 job per destination)
- Complex retry logic per job
- Aggregated results display needed

---

## ADR-005: SQLite for V1 Database

**Date:** 2024-01-XX
**Status:** Accepted

### Context
Need local persistence for accounts, jobs, history.

### Decision
Use SQLite (`data/publisher.db`) for V1. Single file, zero config, embedded.

### Rationale
- No separate database server needed
- Portable: copy file = copy entire state
- Sufficient for single-user CLI tool
- ACID compliant
- Easy backup/restore
- Can migrate to PostgreSQL later if needed

### Consequences
- No concurrent multi-process access (fine for CLI)
- Limited horizontal scaling
- File locking on Windows can be tricky

---

## ADR-006: Fernet for Token Encryption

**Date:** 2024-01-XX
**Status:** Accepted

### Context
Access/refresh tokens must be encrypted at rest.

### Decision
Use `cryptography.fernet.Fernet` (AES-128-GCM) with key from `ENCRYPTION_KEY` env var.

### Rationale
- Standard, audited implementation
- Authenticated encryption (tamper-proof)
- Simple API: `encrypt()` / `decrypt()`
- Key rotation possible (re-encrypt all)
- No external dependencies beyond `cryptography`

### Consequences
- Key management required (generate, store, rotate)
- If key lost, all tokens unrecoverable
- Symmetric: same key encrypts/decrypts

---

## ADR-007: Dry-Run Mode Required

**Date:** 2024-01-XX
**Status:** Accepted

### Context
Users need to verify publishing plan before committing.

### Decision
Implement `--dry-run` flag that shows exact plan without API calls.

### Rationale
- Prevents accidental publishes
- Validates media, accounts, captions
- Shows exactly which accounts will receive post
- Useful for CI/CD verification
- Builds user trust

### Consequences
- Must simulate all validation steps
- Plan display must match actual execution
- No API calls means no real media IDs

---

## ADR-008: Local OAuth Callback Server

**Date:** 2024-01-XX
**Status:** Accepted

### Context
OAuth flows require redirect URI to receive authorization code.

### Decision
Run local HTTP server on `http://localhost:8080/callback/{platform}` during authorization.

### Rationale
- Standard OAuth pattern for desktop/CLI apps
- No external callback service needed
- User sees success/failure in browser
- Automatic browser opening via `webbrowser` module
- Works on all platforms

### Consequences
- Port 8080 must be available
- Firewall/antivirus may block
- Need graceful shutdown after callback
- State parameter validates callback authenticity

---

## ADR-009: No AI/Generation Features

**Date:** 2024-01-XX
**Status:** Accepted

### Context
Many social tools add AI caption generation, video editing, etc.

### Decision
This tool is **distribution/publishing only**. No AI generation, no video editing, no content creation.

### Rationale
- Focus: Do one thing well (reliable publishing)
- Avoid scope creep
- User provides finished video + caption
- Separation of concerns: creation ≠ distribution
- Simpler maintenance, testing, security

### Consequences
- Users must use other tools for content creation
- No "smart" features (hashtag suggestions, timing optimization)

---

## ADR-010: Structured Logging with Sanitization

**Date:** 2024-01-XX
**Status:** Accepted

### Context
Need observability without leaking secrets.

### Decision
Use structured JSON logging. Sanitize all log output: no tokens, secrets, file paths, captions.

### Rationale
- Machine-parseable for debugging
- Safe for shared logs
- Correlation IDs trace jobs across components
- Debug level for development only

### Consequences
- More verbose log format
- Manual debugging requires correlation ID lookup
- Must audit all log statements for secrets

---

## ADR-011: Jobs Run Sequentially, Not in Parallel (supersedes part of ADR-004)

**Date:** 2026-09-25
**Status:** Accepted

### Context
ADR-004 said jobs "execute in parallel". The Phase 4 engine is synchronous.

### Decision
Destinations are processed one after another in `publish_post`. Independence (ADR-004) is kept: every job has its own state, attempts, retries and error handling, and one job's failure (including an unexpected exception) never stops or rolls back another.

### Rationale
- Simple, deterministic, easy to test; no shared-connection or SQLite write contention
- Provider rate limits (TikTok 6 inits/min, Instagram 100 posts/day) make parallel uploads of little value
- Token renewal uses `asyncio.run` inside `AuthManager`, which is safe from synchronous code

### Consequences
- Total time is the sum of the jobs, including retry back-off sleeps
- Upgrade path: a thread pool around `_run_job` (each job already uses its own DB sessions)

---

## ADR-012: Instagram Publishing Requires a Public `video_url`

**Date:** 2026-09-25
**Status:** Accepted

### Context
Meta documents two ways to supply Reels video: `video_url` (Meta downloads it) and a resumable upload to `rupload.facebook.com`. The content-publishing docs list rupload only for the **Facebook Login** product; the project uses **Instagram Login** (Phase 3B).

### Decision
Instagram jobs need an `https://` `video_url` option that the user supplies. Local files are not uploaded to Instagram.

### Consequences
- Users must host the video somewhere Meta can fetch it
- If Meta documents rupload for Instagram Login later, add it as a second source in `InstagramPublisher`

---

## ADR-013: Provider IDs Persisted per Attempt for Idempotent Resume

**Date:** 2026-09-25
**Status:** Accepted

### Decision
Adapters report provider IDs (Instagram container ID, TikTok `publish_id`, YouTube `video_id`) through `on_progress` as soon as they exist. The engine stores them in `publish_attempts.response_json` before the next step. Retries and later runs pass them back so adapters resume rather than re-post. No new "provider state" table was added.

### Consequences
- Secrets such as the TikTok `upload_url` and the YouTube session URI are never stored. An interrupted upload restarts (TikTok) or fails as "uncertain" (YouTube)
- Outcomes that can't be determined are marked `uncertain` and never auto-retried

---

## ADR-014: Schema Migration 002 and Migration Runner Fixes

**Date:** 2026-09-25
**Status:** Accepted

### Decision
Migration `002_publishing.sql` adds `publish_jobs.options_json` (per-destination options such as the TikTok privacy level or YouTube title) and a unique index on `(post_id, account_id)`. The runner now strips comment lines instead of skipping whole comment-prefixed statements (previously most of 001 was silently skipped), tolerates "duplicate column" when `create_all()` already created the column, and records the version with `merge`. `main.py` runs `create_all()` + `migrate()` at startup.

---

## ADR-015: Content Intake Is a Layer Above the Engine

**Date:** 2026-09-25
**Status:** Accepted

### Decision
`src/content/` turns a folder package plus the saved profile into the same `(account_id, options)` destinations that manual Create Post uses, and calls the existing `JobStore` and `PublisherEngine`. It has no platform HTTP and no platform branches beyond building each platform's required options from the profile. Covers go through adapter capability methods.

### Consequences
- One publishing path, with the same retries, isolation, idempotency and attempt records
- A package is moved to `publishing/` before its post is created, so stored file paths stay valid; resumed or retried packages are moved back there first

---

## ADR-016: Content Identity = Video Fingerprint, Not Folder Name

**Date:** 2026-09-25
**Status:** Accepted

### Decision
`content_key` = SHA-256 of the video's name, size, and first/last 64 KiB (unique in `content_items`). Folder name and caption are excluded.

### Rationale
Folders can be renamed during moves (timestamp suffix) and captions edited before a retry; neither may turn a half-published package into a "new" one. Hashing the whole video would be slow for large files.

### Consequences
Re-posting the identical video file as a new package is refused ("already published").

---

## ADR-017: Cover Support per Platform Capability

**Date:** 2026-09-25
**Status:** Accepted

### Decision
Adapters declare `supports_cover_upload()` / `supports_cover_timestamp()` / `validate_cover()` / `cover_plan()`. Only YouTube uploads an image (`thumbnails.set`, after the video). TikTok (frame-only `video_cover_timestamp_ms`) and Instagram (`cover_url` documented only for Facebook Login, and it needs a public URL) report `not_supported`. The cover result is stored in `publish_jobs.cover_status/cover_error`, separate from the video status.

### Consequences
A thumbnail failure never fails or re-uploads a published video. It is not retried automatically.

---

## ADR-018: One Confirmation per Package (VERIFY), None in AUTO

**Date:** 2026-09-25
**Status:** Accepted

### Decision
VERIFY shows the whole plan and asks once; `ContentIntake.publish()` then never prompts. AUTO publishes READY/RESUME packages without prompting but still runs full validation and moves INVALID/BLOCKED packages to `failed/`. Failed destinations are retried only when the user confirms retrying a FAILED package; AUTO never retries failed jobs, to avoid retry loops.

