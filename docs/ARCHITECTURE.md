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
- **base.py** — Base classes: OAuthConfig, OAuthTokenResult, PlatformAuth
- **state.py** — OAuth state management, PKCE (S256) generation/validation
- **callback_server.py** — Local HTTP callback server (port 8080) with timeout, CSRF protection
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

### Instagram (Meta / Facebook Login for Instagram)
- **Authorization URL**: `https://www.facebook.com/v22.0/dialog/oauth`
- **Token URL**: `https://graph.facebook.com/v22.0/oauth/access_token`
- **Scopes**: `instagram_graph_user_profile`, `instagram_graph_user_media`, `pages_show_list`, `pages_read_engagement`
- **PKCE**: Required (S256)
- **Account Linking**: Instagram Business/Creator account linked to Facebook Page

### TikTok
- **Authorization URL**: `https://www.tiktok.com/v2/auth/authorize/`
- **Token URL**: `https://open.tiktokapis.com/v2/oauth/token/`
- **Scopes**: `video.upload`, `video.publish`, `user.info.basic`
- **PKCE**: Required (S256)

### YouTube (Google OAuth 2.0)
- **Authorization URL**: `https://accounts.google.com/o/oauth2/v2/auth`
- **Token URL**: `https://oauth2.googleapis.com/token`
- **Scopes**: `youtube.upload`, `youtube`, `youtube.readonly`
- **PKCE**: Required (S256)
- **Access Type**: `offline` (for refresh token)
- **Prompt**: `consent`

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
- Instagram: Meta OAuth (Instagram Graph API) — **IMPLEMENTED**
- TikTok: TikTok OAuth 2.0 (Creator API) — **IMPLEMENTED**
- YouTube: Google OAuth 2.0 (YouTube Data API v3) — **IMPLEMENTED**

Local callback server on `http://localhost:8080/callback/{platform}`.

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