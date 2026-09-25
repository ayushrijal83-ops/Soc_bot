# Architecture

## System Overview

```
                      main.py
                        |
                  Terminal CLI
                        |
           +------------+------------+
           |                         |
    Account Manager   JobStore (src/core/jobs.py)
           |                         |
           +------------+------------+
                        |
                 Publisher Engine
                        |
        +----------------+----------------+
        |                |                |
     Instagram         TikTok          YouTube
      Adapter          Adapter          Adapter
        |                |                |
     Official API     Official API     Official API
```

## Core Components

### 1. Terminal CLI (`src/cli/`) — ✅ **IMPLEMENTED**
- **menu.py** — Main menu loop, navigation, user input handling
- **prompts.py** — Interactive prompts for video selection, caption entry, platform/account selection
- **display.py** — Rich text formatting, tables, progress bars, status display
- **account_menu.py** — Account management submenu (list, details, connect, create dev, update, disconnect, enable)
- **publish_menu.py** — Phase 4 minimal CLI: Create Post (plan → confirm → publish with live status) and Publishing Queue

### 2. Account Manager (`src/accounts/manager.py`) — ✅ **IMPLEMENTED**
- Manages connected accounts per platform
- Stores/retrieves account credentials from database (encrypted)
- Account status tracking (active, expired, revoked, disconnected)
- Account listing with filtering (platform, status)
- Account search by platform + platform_account_id
- Safe display (no tokens in output)
- Internal token access for publishing
- Development/test account creation (explicitly labeled)

### 3. Auth Manager (`src/auth/manager.py`) — ✅ **IMPLEMENTED**
- Coordinates OAuth flows for all platforms
- Manages platform configurations from environment
- Orchestrates OAuth flows: browser launch, callback server, token exchange
- Creates/updates accounts with encrypted token storage
- Manages token refresh and revocation
- Provides account selection for publishing

### 4. Auth Infrastructure (`src/auth/`) — ✅ **IMPLEMENTED**
- **base.py**: `OAuthConfig`, `OAuthTokenResult` (with `expires_at` from `expires_in`), `PlatformAuth` base class that all three adapters subclass, and the `expires_at_from` / `is_token_expiring` helpers
- **state.py**: OAuth state (single-use, platform-bound) and PKCE; `generate_pkce_challenge(verifier, encoding)` supports `base64url` (RFC 7636) and `hex` (TikTok)
- **callback_server.py**: one shared `OAuthCallbackServer` on 127.0.0.1, dynamic port (0) by default, exposing the bound `port` and `redirect_uri(platform)`, with timeout and state/platform validation
- **errors.py** — OAuth-specific exception hierarchy
- **manager.py** — AuthManager coordinating all platform adapters

### 5. Job Store (`src/core/jobs.py`): ✅ **IMPLEMENTED** (Phase 4)
- Creates a post plus one `PublishJob` per destination account (the video is de-duplicated by SHA-256)
- Enforces the job state machine with conditional UPDATEs (`transition`, atomic `claim`)
- Records every provider attempt in `publish_attempts`: provider IDs only, never tokens

### 6. Publisher Engine (`src/core/publisher.py`): ✅ **IMPLEMENTED** (Phase 4)
- Platform-agnostic: no platform HTTP; calls `PlatformPublisher` adapters
- Per job: validate, check the account, renew the token if needed, publish/resume, record the attempt, update state
- Destinations are isolated: an error (even an unexpected exception) in one job never stops or rolls back another
- Bounded retries for retryable errors; resumes processing jobs; dry-run plan without network

### 7. Platform Adapters (`src/platforms/`): ✅ **IMPLEMENTED** (mock-tested; real publishing NOT RUN)
- **base.py**: `PlatformPublisher` interface, `PublishContext`, `PublishOutcome`, `PublishError` (retryable / uncertain), `redact()`
- **`<platform>/auth.py`**: OAuth (Phase 3B)
- **`<platform>/publisher.py`**: platform publishing and platform-specific validation

### 7a. Media Validation (`src/core/validation.py`): ✅ **IMPLEMENTED**
- Generic checks only: exists, regular file, readable, non-empty, MP4/MOV/WebM, size; ffprobe metadata when installed
- Platform limits live in each adapter's `validate()`

### 7b. Content Intake (`src/content/`): ✅ **IMPLEMENTED** (Phase 5A)
```
content/incoming/<package>/  ->  ContentDetector  ->  ContentValidator  ->  PublishingProfile
      -> ContentIntake.plan() (engine.plan_destinations + adapter.cover_plan)  ->  one confirmation (VERIFY) / none (AUTO)
      -> ContentManager.move(publishing/)  ->  JobStore.create_post / add_missing_jobs  ->  existing PublisherEngine
      -> published/ | archive/ | failed/ | stays in publishing/ (still processing / crash)
```
- **models.py**: `ContentPackage`, `content_root()` (`CONTENT_ROOT`), `stability_seconds()` (`CONTENT_STABILITY_SECONDS`)
- **detector.py**: finds packages and their video/caption/cover (ambiguity = invalid; symlinks never followed)
- **validator.py**: video via `core.validation`, UTF-8 caption (unmodified), cover magic bytes, partial-copy stability
- **manager.py**: moves packages between stages, only inside the content root, never deletes
- **profile.py**: `Profile` + `ProfileStore` (`publishing_profiles`, account IDs only)
- **intake.py**: `ContentIntake`: scan/inspect (read-only), plan, publish (one package), publish_ready (AUTO), history
- No platform-specific code: per-platform options come from the profile, and cover handling comes from the adapter's capability methods. See [CONTENT_INTAKE.md](CONTENT_INTAKE.md).

### 7c. Media Delivery (`src/media_storage/`): ✅ **IMPLEMENTED** (Instagram only; real run pending storage config)
```
InstagramPublisher ──► MediaSourceProvider (prepare / get_public_url / cleanup; max_file_size capability)
                            ├─► MediaStorageRouter (MEDIA_STORAGE_PROVIDER=auto): picks TempFile or S3 by file size
                            ├─► ObjectStorageMediaProvider ──► ObjectStorage ──► S3ObjectStorage (boto3; S3/R2/MinIO)   [MEDIA_STORAGE_PROVIDER=s3]
                            ├─► TempFileMediaStorage (httpx; public TempFile.org upload + id delete)                   [MEDIA_STORAGE_PROVIDER=tempfile]
                            ├─► CloudflareTunnelMediaProvider (127.0.0.1 single-file server + cloudflared Quick Tunnel) [MEDIA_STORAGE_PROVIDER=cloudflare_tunnel]
                            └─► ZeroX0MediaStorage (httpx; public 0x0.st upload + token delete)                        [MEDIA_STORAGE_PROVIDER=0x0]
```
`create_media_provider()` picks the provider from `MEDIA_STORAGE_PROVIDER` (default `s3`; never falls back to a public host). AUTO order: TempFile → Cloudflare Quick Tunnel → S3 (0x0 is never in AUTO). The tunnel provider is not object storage: it serves the local file through a temporary `trycloudflare.com` URL while Instagram fetches it, and cleanup stops the tunnel (see API_INTEGRATIONS.md → Cloudflare Quick Tunnel).
Temporary private object + presigned HTTPS URL for platforms that fetch media from a URL. The provider is created by the Instagram adapter via `create_media_provider()`; the engine only gets `delivery_notes()` text for plans. See API_INTEGRATIONS.md → Instagram Media Delivery.

### 7d. Published-Link Library (`src/core/published_links.py`, `src/cli/links_menu.py`): ✅ **IMPLEMENTED**
`PublisherEngine(links=PublishedLinks())` calls `_record_link` after the `published` transition, and `adapter.published_url(media_id, state)` gives the permanent URL (YouTube watch URL / Instagram permalink / TikTok `None`). Links go to per-platform JSON files under `content/published_links/`, not the DB. See CONTENT_INTAKE.md → Published Links.

### 8. Storage (`src/storage/`) — ✅ **IMPLEMENTED**
- **database.py** — SQLite connection, migrations, ORM/models
- **tokens.py** — Token encryption/decryption, secure storage

## Data Flow

```
User Input (Video + Caption)
          |
          v
    Validation
          |
          v
Platform/Account Selection
          |
          v
    Job Creation (1 job per destination)
          |
          v
    For Each Job (sequential, isolated; see ADR-011):
       Publisher Engine
             |
             v
       Platform Adapter
             |
             v
       Official API
             |
             v
       Result (Success/Failure)
          |
          v
    Aggregate Results → Display (job + attempt rows are the history)
```

## CLI Layer — ✅ **IMPLEMENTED**

```
main.py
  └── CLI Menu (menu.py)
        ├── Create Post → publish_menu.py → validation.py → plan → JobStore → PublisherEngine
        ├── Connected Accounts → account_menu.py → AuthManager → Account Manager
        ├── Publishing Queue → publish_menu.py → list jobs / continue open jobs / retry failed
        ├── History → not implemented
        ├── Settings → not implemented
        └── Exit
```

## Account Manager

Responsibilities:
- List connected accounts per platform
- Store/retrieve account credentials from database (encrypted)
- Account status tracking (active, expired, revoked, disconnected)
- Account listing with filtering (platform, status)
- Account search by platform + platform_account_id
- Safe display (no tokens in output)
- Internal token access for publishing
- Development/test account creation (explicitly labeled)

## Auth Manager — ✅ **IMPLEMENTED**

Responsibilities:
- Coordinate OAuth flows for all platforms
- Manage platform configurations from environment
- Orchestrate OAuth flows: browser launch, callback server, token exchange
- Create/update accounts with encrypted token storage
- Manage token refresh and revocation
- Provide account selection for publishing

## Publishing (Phase 4)

```
Post #15 (video + caption)
   +-- Job #101 -> Instagram account A   (independent)
   +-- Job #102 -> TikTok account B      (independent)
   +-- Job #103 -> YouTube channel C     (independent)
```

### Job state machine (`publish_jobs.status`)

```
pending ──claim──► uploading ──► processing ──► published   (terminal)
   │                  │   │           │  ▲
   │                  │   └───────────┼──┘ (fast providers: uploading ──► published)
   │                  ▼               ▼
   └───(validation)─► failed ◄────────┘
                        │ ▲
      retryable error:  │ │ bounded (3), then failed
   uploading/processing ──► retrying ──claim──► uploading
                        └── manual retry: failed ──► retrying
```

Allowed transitions are listed in `src/core/jobs.TRANSITIONS`. `processing ──► processing` means "still working; checked again later". A job that is still processing when polling ends is **not** failed.

### Engine flow

```
publish_post(post_id)
  for each job (isolated, try/except per job):
    published/failed     -> skip (never republish)
    pending/retrying     -> generic + platform validation (failure = failed, no retry)
                            claim (atomic pending|retrying -> uploading)
    uploading (found on start = crashed process)
                         -> resume only if adapter.can_restart(saved state), else failed "outcome unknown"
    processing           -> resume (poll / finish with saved provider IDs)
    attempt loop:
      start attempt -> check account active -> renew token if expiring -> adapter.publish(ctx, on_progress)
        on_progress: persists provider IDs to the attempt immediately + moves job to uploading/processing
      PublishError retryable & retries left -> retrying, sleep(30/60/120 s), claim, next attempt
      otherwise -> failed (safe error text)
      success -> published (platform_media_id) or processing
```

### Adapter interface (`src/platforms/base.py`)

```python
class PlatformPublisher(ABC):
    PLATFORM: str
    TOKEN_REFRESH_MARGIN: int          # renew when the token expires within this many seconds
    RESTART_SAFE: bool                 # may a crashed mid-upload job be started again?
    def validate(caption, options, media) -> list[str]           # no network
    def publish(ctx, on_progress) -> PublishOutcome              # resumes from ctx.state
    def can_restart(state) -> bool
    # Phase 5A cover capability
    def supports_cover_upload() -> bool      # YouTube: True
    def supports_cover_timestamp() -> bool   # TikTok: True (not configured yet)
    def validate_cover(path) -> list[str]
    def cover_plan(path) -> (status, reason) # none | upload | not_supported | skipped
```

`PublishOutcome.cover_status/cover_error` carry the cover result separately; the engine stores them in `publish_jobs.cover_status/cover_error` without touching the video status.

`publish()` is resumable: given the provider IDs saved by earlier attempts, it continues instead of starting over. A separate `get_status()` isn't needed. `cancel()` was not implemented: none of the three APIs offers a cancel for an in-flight publish (deleting a published video is a different action).

### Idempotency / duplicate protection

| Layer | Mechanism |
|-------|-----------|
| Database | Unique index `(post_id, account_id)`: one job per destination per post |
| Engine | Atomic claim; `published` is terminal; failed jobs only run again on explicit `retry_job` |
| CLI | Warns before publishing the same file (checksum) to an account it was already published to |
| Instagram | Container ID saved before polling. Resume checks `status_code`: `PUBLISHED` means done (no second `media_publish`); `FINISHED` means publish once; `ERROR`/`EXPIRED` means a new container (the old one can never publish) |
| TikTok | `publish_id` saved before the upload, `upload_complete` after it. Resume after the upload only polls status. An interrupted upload is re-initialised; TikTok can't publish an incomplete upload |
| YouTube | `video_id` saved as soon as the upload completes; resume only polls. The session URI is kept in memory only. If the final chunk's response is lost, the session is queried; if that also fails, the job is marked failed with `uncertain` and is **not** auto-retried (the video may exist) |

Unavoidable uncertainty: a crash between the provider accepting a request and the local write of its ID. On Instagram and TikTok that leaves an orphan container or upload that never publishes. On YouTube a crash during the final chunk leaves the outcome unknown, so the job is failed as "outcome unknown; check the channel" rather than re-uploaded.

Single-process assumption: a job found in `uploading` at the start of a run is treated as crashed. Two CLI instances publishing at once are not supported.

### Token handling
The engine loads the account (it must be `active`) and checks `expires_at` against the adapter's `TOKEN_REFRESH_MARGIN` (Instagram 7 days, others 5 min). If the token is expiring it calls `AuthManager.refresh_account_tokens`, which uses the platform's documented mechanism (Instagram `ig_refresh_token`, TikTok/YouTube refresh token, rotation persisted, Fernet-encrypted by AccountManager). If renewal fails and the token has already expired, the job fails with "reconnect the account"; if it is still valid, publishing continues. Tokens exist only in the adapter's `PublishContext` and request headers.

## Platform Adapters — Auth ✅ IMPLEMENTED

```
PlatformAuth (src/auth/base.py)
  PKCE_CHALLENGE_ENCODING = "base64url"   SCOPE_SEPARATOR = " "
  generate_pkce_pair() / get_authorization_url() / validate_configuration()
     |
     +-- YouTubeAuth     (inherits defaults; access_type=offline, prompt=consent)
     +-- TikTokAuth      (PKCE "hex", scopes ",", client_key param)
     +-- InstagramAuth   (no PKCE, scopes ",", REFRESH_USES_ACCESS_TOKEN)

OAuthCallbackServer (shared) --binds 127.0.0.1:0--> actual port --> redirect_uri --> adapter.config
```

Endpoint details and verification status: see [API_INTEGRATIONS.md](API_INTEGRATIONS.md) (verified 2026-09-25).

- **Instagram**: Business Login for Instagram (`instagram.com/oauth/authorize` → `api.instagram.com/oauth/access_token` → `ig_exchange_token`). No Facebook Page. Identity is `/me` `user_id`.
- **TikTok**: Login Kit for Desktop, with a hex PKCE challenge and refresh-token rotation.
- **YouTube**: Google OAuth for installed apps on a dynamic loopback port.

## Storage

SQLite database (`data/publisher.db`):
- Accounts table
- Videos table
- Posts table
- Publish Jobs table
- Publish Attempts table (optional)

Token encryption using Fernet (symmetric encryption) with key from environment.

## Authentication

OAuth 2.0 / 2.1 flows per platform:
- Instagram: Instagram API with Instagram Login: **IMPLEMENTED, mock-tested, real OAuth NOT RUN**
- TikTok: Login Kit for Desktop: **IMPLEMENTED, mock-tested, real OAuth NOT RUN**
- YouTube: Google OAuth 2.0 installed-app flow: **IMPLEMENTED, mock-tested, real OAuth NOT RUN**

Local callback server on `http://127.0.0.1:<dynamic-port>/callback/{platform}` (or a fixed `*_REDIRECT_URI`).

## Error Handling

- Platform API errors → mapped to common error types
- Network errors → retry with backoff
- Token expiry → renewed before publishing (not on a mid-request 401)
- Validation errors → fail fast, no API call
- Errors stored per job/attempt with `redact()` applied; no structured logging yet (ADR-010 still planned)

## Retry Architecture (implemented)

- Max 3 retries per job (`retry_delays = (30, 60, 120)` seconds), run in-process while the CLI waits
- Retryable: network error/timeout, 5xx, 429, provider-flagged transient errors (Instagram `is_transient`), expired Instagram container, TikTok `rate_limit_exceeded`/`internal_error`
- Non-retryable: validation, 401/invalid token, insufficient scope, permission denied, quota exceeded, rejected content, inactive account, **uncertain outcome**
- `retry_count`, `next_retry_at`, `error_message` persisted on the job; each attempt's error in `publish_attempts.error_json`
- Retries resume from saved provider IDs instead of starting over