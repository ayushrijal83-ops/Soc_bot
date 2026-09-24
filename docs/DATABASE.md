# Database Design (PLANNED)

> **Status:** PLANNED — Not yet implemented. Schema subject to change.

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
| access_token_enc | BLOB | NOT NULL | Encrypted access token |
| refresh_token_enc | BLOB | | Encrypted refresh token |
| expires_at | TIMESTAMP | | Access token expiry (UTC) |
| status | TEXT | NOT NULL, DEFAULT 'active', CHECK(status IN ('active','expired','revoked','disconnected')) | Account status |
| meta_json | TEXT | | JSON for platform-specific extra data |
| created_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Record creation |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Last update |

**Indexes:**
- `idx_accounts_platform` ON (platform)
- `idx_accounts_platform_id` ON (platform, platform_account_id) UNIQUE
- `idx_accounts_status` ON (status)

### videos
Video files referenced by posts.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT | Internal ID |
| filename | TEXT | NOT NULL | Original filename |
| path | TEXT | NOT NULL | Absolute path to file |
| size_bytes | INTEGER | NOT NULL | File size in bytes |
| duration_seconds | REAL | | Video duration |
| mime_type | TEXT | | Detected MIME type |
| width | INTEGER | | Video width |
| height | INTEGER | | Video height |
| checksum | TEXT | | SHA256 for deduplication |
| created_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Record creation |

**Indexes:**
- `idx_videos_checksum` ON (checksum) UNIQUE
- `idx_videos_path` ON (path)

### posts
User-created posts (video + caption).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT | Internal ID |
| video_id | INTEGER | NOT NULL, FK → videos(id) | Video reference |
| caption | TEXT | | User-provided caption |
| created_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Creation time |

### publish_jobs
Individual publishing job per destination (platform + account).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT | Internal ID |
| post_id | INTEGER | NOT NULL, FK → posts(id) | Parent post |
| account_id | INTEGER | NOT NULL, FK → accounts(id) | Destination account |
| status | TEXT | NOT NULL, DEFAULT 'pending', CHECK(status IN ('pending','uploading','processing','published','failed','retrying')) | Job status |
| platform_media_id | TEXT | | Platform's media/post ID after publish |
| error_message | TEXT | | Last error if failed |
| retry_count | INTEGER | NOT NULL, DEFAULT 0 | Number of retries attempted |
| next_retry_at | TIMESTAMP | | Scheduled retry time |
| created_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Job creation |
| published_at | TIMESTAMP | | When successfully published |

**Indexes:**
- `idx_jobs_post` ON (post_id)
- `idx_jobs_account` ON (account_id)
- `idx_jobs_status` ON (status)
- `idx_jobs_next_retry` ON (next_retry_at) WHERE status='retrying'

### publish_attempts (Optional)
Detailed attempt history per job.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT | Internal ID |
| job_id | INTEGER | NOT NULL, FK → publish_jobs(id) | Parent job |
| attempt_number | INTEGER | NOT NULL | 1-based attempt |
| status | TEXT | NOT NULL, CHECK(status IN ('started','uploading','processing','success','failed')) | Attempt status |
| response_json | TEXT | | Raw API response |
| error_json | TEXT | | Error details if failed |
| started_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Attempt start |
| completed_at | TIMESTAMP | | Attempt end |

**Indexes:**
- `idx_attempts_job` ON (job_id)

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

## Relationships

```
accounts 1───< publish_jobs >───1 posts >───1 videos
                    |
                    └───< publish_attempts
```

## Constraints

- Account uniqueness: (platform, platform_account_id) unique
- Video deduplication: checksum unique
- Job references valid post and account
- Status transitions enforced in application logic

## Migrations

Use simple versioned SQL migration files in `src/storage/migrations/`:
- `001_initial_schema.sql`
- `002_add_attempts_table.sql`
- etc.

Migration runner in `database.py` tracks applied versions in `schema_version` table.