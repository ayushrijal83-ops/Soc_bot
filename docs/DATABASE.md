# Database Design

> **Status:** ✅ IMPLEMENTED — Phase 1 complete.

## Overview

SQLite database (`data/publisher.db`) for V1. Single-file, zero-config, sufficient for local CLI tool.

## Tables

### accounts
Connected social media accounts.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT | Internal ID |
| platform | TEXT | NOT NULL, CHECK(platform IN ('instagram','tiktok','youtube')) | Platform identifier |
| platform_account_id | TEXT | NOT NULL | Platform's user/channel ID |
| username | TEXT | NOT NULL | Display handle (@username or channel name) |
| display_name | TEXT | | Full name / channel title |
| access_token_enc | TEXT | NOT NULL | Encrypted access token (Fernet, base64-encoded) |
| refresh_token_enc | TEXT | | Encrypted refresh token (Fernet, base64-encoded) |
| expires_at | TIMESTAMP | | Access token expiry (UTC) |
| status | TEXT | NOT NULL, DEFAULT 'active', CHECK(status IN ('active','expired','revoked','disconnected')) | Account status |
| meta_json | TEXT | | JSON for platform-specific extra data |
| created_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Record creation |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Last update (auto-updated via SQLAlchemy) |

**Indexes:**
- `idx_accounts_platform` ON (platform)
- `uq_accounts_platform_id` ON (platform, platform_account_id) UNIQUE
- `idx_accounts_status` ON (status)

**Check Constraints:**
- `ck_accounts_platform`: platform IN ('instagram','tiktok','youtube')
- `ck_accounts_status`: status IN ('active','expired','revoked','disconnected')

### videos
Video files referenced by posts.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT | Internal ID |
| filename | TEXT | NOT NULL | Original filename |
| path | TEXT | NOT NULL | Absolute path to file |
| size_bytes | INTEGER | NOT NULL | File size in bytes |
| duration_seconds | INTEGER | | Video duration in seconds |
| mime_type | TEXT | | Detected MIME type |
| width | INTEGER | | Video width |
| height | INTEGER | | Video height |
| checksum | TEXT | | SHA256 for deduplication (64 hex chars) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Record creation |

**Indexes:**
- `uq_videos_checksum` ON (checksum) UNIQUE
- `idx_videos_path` ON (path)

### posts
User-created posts (video + caption).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT | Internal ID |
| video_id | INTEGER | NOT NULL, FK → videos(id) ON DELETE CASCADE | Video reference |
| caption | TEXT | | User-provided caption |
| created_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Creation time |

**Indexes:**
- `idx_posts_video` ON (video_id)

### publish_jobs
Individual publishing job per destination (platform + account).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT | Internal ID |
| post_id | INTEGER | NOT NULL, FK → posts(id) ON DELETE CASCADE | Parent post |
| account_id | INTEGER | NOT NULL, FK → accounts(id) ON DELETE CASCADE | Destination account |
| status | TEXT | NOT NULL, DEFAULT 'pending', CHECK(status IN ('pending','uploading','processing','published','failed','retrying')) | Job status |
| platform_media_id | TEXT | | Platform's media/post ID after publish |
| options_json | TEXT | | Per-destination publish options, e.g. `{"privacy_level": "SELF_ONLY"}`, `{"title": …, "privacy_status": …}`, `{"video_url": …}`; never secrets (migration 002) |
| error_message | TEXT | | Last error if failed |
| retry_count | INTEGER | NOT NULL, DEFAULT 0 | Number of retries attempted |
| next_retry_at | TIMESTAMP | | Scheduled retry time |
| created_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Job creation |
| published_at | TIMESTAMP | | When successfully published |

**Indexes:**
- `idx_jobs_post` ON (post_id)
- `idx_jobs_account` ON (account_id)
- `idx_jobs_status` ON (status)
- `idx_jobs_next_retry` ON (next_retry_at)
- `uq_jobs_post_account` UNIQUE ON (post_id, account_id): one job per destination per post (migration 002)

**Check Constraints:**
- `ck_jobs_status`: status IN ('pending','uploading','processing','published','failed','retrying')

### publish_attempts
Detailed attempt history per job.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT | Internal ID |
| job_id | INTEGER | NOT NULL, FK → publish_jobs(id) ON DELETE CASCADE | Parent job |
| attempt_number | INTEGER | NOT NULL | 1-based attempt |
| status | TEXT | NOT NULL, CHECK(status IN ('started','uploading','processing','success','failed')) | Attempt status |
| response_json | TEXT | | Raw API response |
| error_json | TEXT | | Error details if failed |
| started_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Attempt start |
| completed_at | TIMESTAMP | | Attempt end |

**Indexes:**
- `idx_attempts_job` ON (job_id)

**Unique Constraints:**
- `uq_attempts_job_number` ON (job_id, attempt_number)

**Check Constraints:**
- `ck_attempts_status`: status IN ('started','uploading','processing','success','failed')

### schema_version
Tracks applied migration versions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| version | INTEGER | PRIMARY KEY | Migration version number |
| applied_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | When applied |
| description | TEXT | | Migration description |

## Status Values

### Account Status
- `active` — Valid tokens, ready to publish
- `expired` — Access token expired, refresh needed
- `revoked` — Tokens revoked by platform/user
- `disconnected` — User manually disconnected

### Job Status
- `pending` — Created, not started
- `uploading` — Media upload in progress
- `processing` — Platform processing video
- `published` — Successfully published
- `failed` — Failed permanently (max retries exceeded)
- `retrying` — Failed, scheduled for retry

### Attempt Status
- `started` — Attempt initiated
- `uploading` — Media upload in progress
- `processing` — Platform processing video
- `success` — Successfully published
- `failed` — Attempt failed

## Relationships

```
accounts 1───< publish_jobs >───1 posts >───1 videos
                     |
                     └───< publish_attempts
```

- One Account → Many PublishJobs
- One Post → Many PublishJobs (one per destination account)
- One PublishJob → Many PublishAttempts (retry history)
- One Video → Many Posts (reuse videos)
- CASCADE DELETE: Video→Post→PublishJob→PublishAttempt

## Constraints

- Account uniqueness: (platform, platform_account_id) unique
- Video deduplication: checksum unique
- Job references valid post and account (FK with CASCADE DELETE)
- Attempt uniqueness: (job_id, attempt_number) unique
- Status values enforced via CHECK constraints
- Foreign keys enforced (PRAGMA foreign_keys=ON)
- CHECK constraints enforced (PRAGMA ignore_check_constraints=OFF)

## Token Encryption

- **Algorithm**: Fernet (AES-128-GCM) via `cryptography.fernet`
- **Key Source**: `ENCRYPTION_KEY` environment variable (32-byte URL-safe base64)
- **Storage**: Encrypted tokens stored as base64-encoded TEXT in database
- **Key Generation**: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- **Key Rotation**: Not yet implemented (future feature)

## Migrations

Use simple versioned SQL migration files in `src/storage/migrations/`:
- `001_initial_schema.sql` — Initial schema with all tables

Migration runner in `database.py` tracks applied versions in `schema_version` table.

## Usage

```bash
# Initialize database (creates tables, runs migrations)
python -m src.storage.database init

# Run pending migrations only
python -m src.storage.database migrate

# Health check
python -m src.storage.database health
```

```python
# In application code
from src.storage import Database, Account, Video, Post, PublishJob, PublishAttempt
from src.storage.tokens import TokenEncryption, generate_key

# Generate encryption key (once)
key = generate_key()

# Create database with encryption
encryption = TokenEncryption(key.encode())
db = Database("sqlite:///data/publisher.db", encryption=encryption)
db.init()

# Create account with encrypted tokens
with db.session() as session:
    account = Account(
        platform="instagram",
        platform_account_id="12345",
        username="my_user",
        access_token="access_token_from_oauth",
        refresh_token="refresh_token_from_oauth",
        _encryption=encryption,
    )
    session.add(account)
    session.commit()

# Decrypt tokens when needed
with db.session() as session:
    account = session.query(Account).filter_by(username="my_user").first()
    access_token = account.get_access_token(encryption)
```

## Publishing Usage (Phase 4)

- `publish_attempts.response_json` = `{"provider_state": {...}}`: provider IDs (Instagram `container_id`/`media_id`, TikTok `publish_id`/`upload_complete`, YouTube `video_id`) saved as soon as they exist, so retries resume instead of re-posting
- `publish_attempts.error_json` = `{"code", "message", "http_status", "retryable", "uncertain"}` with secrets redacted
- Attempt status: `started` → `uploading` / `processing` → `success` / `failed` (`processing` + `completed_at` = provider still working when polling ended)
- Never stored: access/refresh tokens, client secrets, auth codes, TikTok `upload_url`, YouTube session URI
- Migrations: `001_initial_schema.sql`, `002_publishing.sql`; `main.py` runs `create_all()` + `migrate()` on startup
