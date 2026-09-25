# Architecture

## System Overview

```
                      main.py
                        |
                  Terminal CLI
                        |
           +------------+------------+
           |                         |
    Account Manager            Job Manager
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
- **account_menu.py** — Account management submenu (list, details, create dev, update, disconnect, enable)

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

### 5. Job Manager (`src/core/jobs.py`) — 📋 **PLANNED**
- Creates independent publishing jobs per destination (platform + account)
- Manages job lifecycle: PENDING → UPLOADING → PROCESSING → PUBLISHED / FAILED
- Retry logic with exponential backoff
- Job queue persistence
- Concurrency control (max parallel uploads)

### 6. Publisher Engine (`src/core/publisher.py`) — 📋 **PLANNED**
- Platform-agnostic orchestration layer
- Coordinates validation, upload, and publishing per job
- Calls platform adapters through a common interface
- Aggregates results across all destinations
- Dry-run simulation

### 7. Platform Adapters (`src/platforms/{instagram,tiktok,youtube}/`) — 📋 **PLANNED** (auth ✅ IMPLEMENTED)
Each adapter implements a common interface:
- **auth.py** — Platform-specific OAuth flow, token exchange, refresh ✅ **IMPLEMENTED**
- **client.py** — API client wrapper, request/response handling, rate limiting
- **publisher.py** — Media upload, post creation, status polling

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
    For Each Job (Parallel):
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
    Aggregate Results → Display → Save History
```

## CLI Layer — ✅ **IMPLEMENTED**

```
main.py
  └── CLI Menu (menu.py)
        ├── Create Post → prompts.py → validation.py → Job Manager
        ├── Connected Accounts → account_menu.py → AuthManager → Account Manager
        ├── Publishing Queue → Job Manager → status display
        ├── History → Database queries → display.py
        ├── Settings → Configuration display/edit
        └── Exit
```

## Account Manager

Responsibilities:
- List connected accounts per platform
- Store/retrieve account credentials from database (encrypted)
- Token refresh coordination (future)
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

## Job Manager

Job Lifecycle:
```
PENDING
    |
    v (start)
UPLOADING  ──→  PROCESSING  ──→  PUBLISHED
    |              |
    |              └──→ FAILED ──→ RETRYING (max 3) ──→ FAILED
    └──→ FAILED ──→ RETRYING (max 3) ──→ FAILED
```

Each job is independent:
- Own status, error message, platform media ID
- Own retry count and schedule
- Failure of one job does not affect others

## Publisher Engine

Interface:
```python
class PublisherEngine:
    def validate_media(self, video_path: str, platforms: list) -> ValidationResult
    def create_jobs(self, post: Post, destinations: list[Account]) -> list[Job]
    def execute_job(self, job: Job) -> JobResult
    def execute_all(self, jobs: list[Job]) -> list[JobResult]
    def dry_run(self, post: Post, destinations: list[Account]) -> DryRunPlan
```

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
- Token expiry → auto-refresh → retry
- Validation errors → fail fast, no API call
- All errors logged with context (job ID, account, platform)

## Retry Architecture

- Max 3 retries per job
- Exponential backoff: 30s, 60s, 120s
- Retryable errors: network timeout, 5xx, rate limit (429)
- Non-retryable: 4xx (except 429), validation, auth revoked
- Retry state persisted in database