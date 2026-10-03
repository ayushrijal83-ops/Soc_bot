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
**Status:** Accepted, not implemented yet (V2.3 writes plain-text, redacted `soc_bot.*` log lines)

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

---

## ADR-019: Least-Privilege TikTok Scopes and Recorded Grants

**Date:** 2026-09-25
**Status:** Accepted

### Decision
Request `user.info.basic,video.publish` only (not `video.upload`). Store the granted scopes per account and check each publisher's `REQUIRED_SCOPES` before planning or publishing.

### Rationale
Requesting a scope the app doesn't have enabled can make TikTok authorization fail. Without recorded grants, a missing permission only showed up as a provider 401 mid-publish.

---

## ADR-020: Provider Preflight on the VERIFY Screen

**Date:** 2026-09-25
**Status:** Accepted

### Decision
`PlatformPublisher.preflight(ctx)` runs read-only provider checks that are shown before the single confirmation. TikTok implements it with creator_info (nickname, allowed privacy levels, max duration). It runs only on the VERIFY screen; the inbox list, `--scan` listing and dry-run never contact providers. Publishing re-checks creator_info as before.

### Consequences
Opening the VERIFY screen for a TikTok package makes one creator_info call and may renew an expiring token.

---

## ADR-021: Duplicate Rule Is Per Video + Account

**Date:** 2026-09-25
**Status:** Accepted (refines ADR-016)

### Decision
A published content item is `PUBLISHED` only if every profile account already has a job for it. Accounts added to the profile later (e.g. TikTok after YouTube) get new jobs for just those accounts; accounts that already have a job are never planned or published again.

### Rationale
The Phase 5A behaviour blocked a video published to YouTube from ever reaching TikTok. The intended rule was "same content + same account never twice".

---

## ADR-022: Instagram Uses a Registered Redirect; https Paste Mode

**Date:** 2026-09-25
**Status:** Accepted

### Context
Meta redirects only to URIs registered exactly in Business login settings, and community reports say they must be https. A desktop app can't serve https on a registered, fixed public address.

### Decision
Instagram requires `INSTAGRAM_REDIRECT_URI`. A loopback URI uses the local callback server on that fixed port. Any https non-loopback URI uses paste mode: a static GitHub Pages page receives the redirect, and the user pastes the address into Soc_bot, which applies the same state/platform/code validation as the callback server. Only platforms that declare `REQUIRES_REGISTERED_REDIRECT` (Instagram) can use paste mode; TikTok and YouTube are unchanged.

### Consequences
- One extra copy/paste step for Instagram
- The one-time code passes through GitHub Pages' request logs (it's short-lived, single-use and needs the secret)

---

## ADR-023: Instagram Local Media via Temporary Private Object Storage (supersedes ADR-012)

**Date:** 2026-09-25
**Status:** Accepted

### Context
ADR-012 required the user to supply a public `video_url`. That was a bad UX and led to invalid inputs (a YouTube Shorts page URL). Instagram Login publishing only documents `video_url` (Meta downloads the media).

### Decision
`InstagramPublisher` gets the local file and uses a provider-neutral `MediaSourceProvider` (`src/media_storage/`). The implemented provider uploads to a **private** S3-compatible bucket, gives Meta a **presigned HTTPS GET URL** (default 900 s), and deletes the object when the attempt ends. Rejected alternatives:
- A YouTube watch or Shorts URL: not a media file, and the YouTube API offers no direct media URL.
- Scraping YouTube or yt-dlp: against YouTube's Terms, and brittle.
- A Cloudflare Quick Tunnel from a local server: development-only, needs an extra binary, and exposes the machine.
- A public bucket or permanent hosting.

### Consequences
- Instagram publishing needs `MEDIA_STORAGE_*` (a bucket on AWS S3, R2 or MinIO). New dependency: `boto3`.
- A YouTube "Media Hub" channel is an optional archive destination only, never a media source.
- The engine stays storage-agnostic; YouTube and TikTok are unchanged.

---

## ADR-024: 0x0.st as an Opt-in Temporary Media Provider

**Date:** 2026-09-25
**Status:** Accepted

### Decision
Add `ZeroX0MediaStorage` as a second `MediaSourceProvider`, selected with `MEDIA_STORAGE_PROVIDER=0x0`. The default stays `s3`; Soc_bot never falls back to a public host. The operator's client guidelines are followed: unique user agent, `secret` URLs, short `expires`, token-based delete after each attempt, and prominent public-hosting warnings.

### Consequences
- Instagram works without a bucket, at the cost of privacy: the video is public on a third-party host for up to `expires` hours, and 0x0.st logs the uploader's IP and user agent.
- 0x0.st's Terms forbid "automated mass uploads", so it's documented as not for AUTO or bulk publishing.
- The public Privacy Policy (`docs/privacy.html`) now describes media delivery to the configured storage.

---

## ADR-025: TempFile.org Provider (0x0.st uploads disabled)

**Date:** 2026-09-25
**Status:** Accepted

### Context
0x0.st turned off uploads, so the ADR-024 provider can't currently be used. Instagram still needs an HTTPS media URL.

### Decision
Add `TempFileMediaStorage` (`MEDIA_STORAGE_PROVIDER=tempfile`) behind the same `MediaSourceProvider` interface. No Instagram changes. The media URL is built from a validated file id (`/<id>/download`) because the response's `url` is an HTML page. The id is treated as a secret because it also authorises unauthenticated deletion. 0x0.st and S3 stay implemented. URL-safety and local-file checks are now shared (`media_storage/base.py`).

### Consequences
- Real Instagram publishing works without a bucket (proven 2026-09-25), with public temporary exposure (default ≤ 1 h), a 100 MB limit, and the provider's terms (no bulk/automated uploads).
- The public Privacy Policy lists TempFile.org.

---

## ADR-026: Size-Based Media Routing (`auto`)

**Date:** 2026-09-25
**Status:** Accepted

### Decision
`MediaStorageRouter` (a `MediaSourceProvider`) picks the first configured provider in `("tempfile", "s3")` whose declared `max_file_size` fits the file, minus a 1 MB margin. It is deterministic and capability-based, with no failure fallback. Each Instagram job keeps its own temporary object. Existing single-provider values keep their meaning; `auto` is opt-in.

### Consequences
- Users don't change `.env` per video size.
- Small videos are publicly hosted for a short time, large ones privately. The plan says which before confirmation.
- Large videos need S3 credentials; without them, the plan blocks them clearly.
- One upload per Instagram account (no shared object): simpler and race-free, at the cost of extra uploads.

## ADR-027: Instagram Fan-Out With One Cover (2026-09-26)

### Context
One video + one cover + caption must reach many Instagram accounts. Instagram fetches media and covers from public URLs, and large batches must not open dozens of tunnels at once.

### Decision
- A **post is the batch** (existing tables): one job per account, with the cover path in each job's options (the same local file).
- `PublisherEngine.publish_post` runs Instagram jobs in a bounded thread pool (`INSTAGRAM_MAX_CONCURRENT_PUBLISHES`, default 5). At the time each job had its own media session (server + Quick Tunnel). **Superseded by ADR-028:** one shared media session (one tunnel) per batch.
- Providers declare `supports_cover`. The router never picks one that can't serve the cover, so a chosen cover is never dropped.
- Instagram sends `cover_url` (JPEG ≤ 8 MB) from the IG User Media reference, verified to work with Instagram Login by a real publish + `thumbnail_url` readback.

### Consequences
- No schema change. Resume, duplicate rules and failure isolation are unchanged per job.
- Bandwidth scales with accounts (each fetch is a full transfer); the confirmation shows the estimate.
- TempFile/S3 can't carry covers yet. Add `supports_cover` there if a no-cloudflared setup needs covers.

## ADR-028: One Shared Media Session per Instagram Batch + One Automatic Retry Round (2026-09-26)

### Context
Per-job Quick Tunnels triggered Cloudflare's provisioning limit (HTTP 429) after about 45 creations; an 11-account batch then failed entirely.

### Decision
- A batch (post) prepares its media ONCE (`SharedMediaSession`): one local server, one Quick Tunnel, one video route + one cover route, closed after the whole batch. A dead tunnel is replaced once per batch; failed starts have a 30 s cooldown.
- After the initial round drains, the batch's failed Instagram jobs get ONE automatic retry (`posts.auto_retry`, `publish_jobs.auto_retry_used`, migration 004), same pool limit, same session.

### Consequences
- Tunnel creations per batch: 1 (plus at most a replacement if cloudflared dies).
- All jobs of a batch share the same temporary URLs (same files; no per-account secret involved).
- Old posts are never retried automatically; a manual retry disables the automatic one for that job.


## ADR-029: Scheduling D1 — Schedule Held at the Post Level (V2.2, 2026-10-02)

### Context
Jobs created early with a new job status would reach `PublisherEngine._run_job`, which uploads any status it does not recognise without a claim, and Resume publishes every open job.

### Decision
A scheduled post is an ordinary post with ordinary `pending` jobs, held by `posts.schedule_status` (`scheduled` / `missed` / `cancelled` = held, `released` / NULL = not held; migration 006). `JobStore.claim`, `open_post_ids` and `retry_owed_post_ids` exclude held posts in the same SQL statement. The job state machine and its CHECK constraint are unchanged.

### Consequences
- A held post cannot be claimed, resumed or auto-retried by any path, including a direct `publish_post`.
- Scheduled posts are created atomically with their jobs (`JobStore.create_post(..., schedule_status=...)`).

## ADR-030: Scheduling D2 — Timezone Resolution Order (V2.2)

### Decision
A wall-clock date + time is read in: an explicitly chosen zone, else the audience's primary zone (`audience_local` strategy; highest audience weight), else `SOC_BOT_TIMEZONE` (if a valid IANA name), else UTC. The resolved zone and its source are always shown before confirming.

### Consequences
For multi-country audiences the primary zone may differ from the user's own clock; the confirmation shows the zone and the UTC instant. UTC is stored (naive UTC); DST gaps are refused, DST overlaps use the first occurrence.

## ADR-031: Scheduling D3 — 60-Minute Missed Grace (V2.2)

### Decision
`DueScheduler`: a post is due while `scheduled_at <= now <= scheduled_at + 60 min`; strictly later it becomes `missed` and is never published automatically.

### Consequences
A post is not published hours late without the user noticing (e.g. after the PC was off). Missed posts wait for the user: Publish now, Reschedule or Cancel.

## ADR-032: Scheduling D4 — One Scheduling Time per Post (V2.2)

### Decision
`scheduled_at` belongs to the post; all destinations of the post share it. Different times for different destinations are separate posts.

### Consequences
Keeps Instagram's one shared media session per batch and a single hold gate per post.

## ADR-033: Scheduling D5 — No Automatic Continuation of an Interrupted Released Post (V2.2)

### Decision
The scheduler only releases posts that are still `scheduled`. A post that was released but whose publishing was interrupted (crash, kill) is not re-released or continued by the scheduler; it appears as an open batch and the user continues it with Resume.

### Consequences
No unattended re-run of a partially published batch; an interrupted upload on a restart-unsafe platform fails as "outcome unknown" and is never retried automatically (see ADR-038).

## ADR-034: Scheduling D6 — Allowed Range: Now + 2 Minutes to Now + 365 Days (V2.2)

### Decision
`SchedulingService` refuses times earlier than now + 2 minutes (`too_soon`) or later than now + 365 days (`too_far`); a suggestion that became too close is refused as `stale_suggestion`.

## ADR-035: Scheduling D7 — No Deferred Long Rate-Limit Retries (V2.2)

### Decision
Scheduling adds no retry layer. Once released, a post uses the existing engine retries only (in-place 30/60/120 s retries, Instagram's one automatic retry round, manual retry). A publishing failure leaves the post `released`; it is never put back to `scheduled`.

### Consequences
Daily limits (e.g. Instagram's 100 posts per 24 h) end as a failed job; the user retries later.

## ADR-036: Scheduling D8 — Content Inbox Scheduling Excluded (V2.2)

### Decision
Content Inbox packages are published immediately (VERIFY / AUTO) as before; they cannot be scheduled. Packages are folder-driven and moved at publish time, so holding them needs its own design.

## ADR-037: Scheduling D9 — Scheduler Callers: TUI Timer and `--run-due` (V2.2)

### Decision
One `DueScheduler.run_due()` pass, called by the TUI (right after start, then every `SOC_BOT_SCHEDULER_POLL_SECONDS`, 10–300, default 30) and by `python main.py --run-due` (one pass, exit 0 done / 3 lock busy / 1 error / 130 Ctrl+C), e.g. from Windows Task Scheduler. No daemon or Windows service. Every pass holds the process-level publishing lock (`data/publishing.lock`).

### Consequences
Scheduled posts publish only while the TUI or a `--run-due` task runs, and not while the PC is off or asleep.

## ADR-038: Every Publishing Failure Keeps a Structured Error Record (V2.3, 2026-10-02)

### Context
`PublisherEngine._fail(attempt_id=None)` (interrupted upload, validation failure, no publisher, internal error) stored only the job's free-text `error_message`. `last_error()` reads `publish_attempts.error_json`, so it returned `{}`: the automatic retry round could not see `uncertain` and would re-upload an interrupted job of a restart-unsafe adapter on an auto-retry platform, and friendly errors fell back to "Publishing failed.".

### Decision
- `_fail` with no attempt row creates one failed attempt carrying the standard `PublishError.to_dict()` (code, message, http_status, retryable, uncertain). A job that is already published gets no failure record.
- `_retry_candidates` treats a failed job with no recorded error (rows from before this fix) as an unknown outcome: never retried automatically.

### Consequences
- No schema change; old jobs and attempts read as before.
- Failures before any provider call now show one `failed` attempt in the attempt history.
- Unknown outcomes are retried only by the user (manual retry), after checking the account.
