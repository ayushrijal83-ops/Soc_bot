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

### 2. Account Manager (`src/accounts/manager.py`) — 📋 **PLANNED**
- Manages connected accounts per platform
- Handles OAuth authorization flows
- Stores/retrieves account credentials from database
- Token refresh coordination
- Account status tracking (active, expired, revoked, disconnected)

### 3. Job Manager (`src/core/jobs.py`)
- Creates independent publishing jobs per destination (platform + account)
- Manages job lifecycle: PENDING → UPLOADING → PROCESSING → PUBLISHED / FAILED
- Retry logic with exponential backoff
- Job queue persistence
- Concurrency control (max parallel uploads)

### 4. Publisher Engine (`src/core/publisher.py`)
- Platform-agnostic orchestration layer
- Coordinates validation, upload, and publishing per job
- Calls platform adapters through a common interface
- Aggregates results across all destinations
- Dry-run simulation

### 5. Platform Adapters (`src/platforms/{instagram,tiktok,youtube}/`)
Each adapter implements a common interface:
- **auth.py** — Platform-specific OAuth flow, token exchange, refresh
- **client.py** — API client wrapper, request/response handling, rate limiting
- **publisher.py** — Media upload, post creation, status polling

### 6. Storage (`src/storage/`) — ✅ **IMPLEMENTED**
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
        ├── Connected Accounts → Account Manager → OAuth flows
        ├── Publishing Queue → Job Manager → status display
        ├── History → Database queries → display.py
        ├── Settings → Configuration display/edit
        └── Exit
```

## Account Manager

Responsibilities:
- List connected accounts per platform
- Initiate OAuth authorization (opens browser)
- Handle OAuth callback (local HTTP server)
- Exchange authorization code for tokens
- Store tokens securely (encrypted)
- Refresh tokens before expiry
- Detect revoked/expired tokens
- Disconnect accounts (revoke tokens, remove from DB)

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

## Platform Adapters

Common Interface (Protocol):
```python
class PlatformAdapter(Protocol):
    platform_name: str
    
    def validate_media(self, video_path: str) -> ValidationResult
    def upload_media(self, video_path: str, caption: str, account: Account) -> UploadResult
    def poll_status(self, upload_id: str, account: Account) -> ProcessingStatus
    def publish(self, upload_id: str, account: Account) -> PublishResult
    def refresh_tokens(self, account: Account) -> TokenSet
```

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
- Instagram: Meta OAuth (Instagram Graph API)
- TikTok: TikTok OAuth 2.0 (Creator API)
- YouTube: Google OAuth 2.0 (YouTube Data API v3)

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