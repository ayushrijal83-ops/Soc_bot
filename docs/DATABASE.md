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
| auto_retry | INTEGER | | 1 = one automatic retry round for failed Instagram jobs (004) |
| audience_profile_id | INTEGER | FK → audience_profiles(id) ON DELETE SET NULL | Audience strategy chosen for this post (005); NULL = none |
| audience_json | TEXT | | **Immutable snapshot** of that strategy at creation (005); see "Audience Strategy" below |
| scheduled_at | TIMESTAMP | | Publish instant, naive UTC (006); NULL = publish now |
| schedule_status | TEXT | CHECK in (scheduled, released, missed, cancelled) (006) | NULL = ordinary post; see "Scheduling" below |
| schedule_json | TEXT | | How the time was chosen + append-only history (006) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | Creation time |

**Indexes:**
- `idx_posts_video` ON (video_id)
- `idx_posts_schedule` ON (schedule_status, scheduled_at) (006)

### publish_jobs
Individual publishing job per destination (platform + account).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT | Internal ID |
| post_id | INTEGER | NOT NULL, FK → posts(id) ON DELETE CASCADE | Parent post |
| account_id | INTEGER | NOT NULL, FK → accounts(id) ON DELETE CASCADE | Destination account |
| status | TEXT | NOT NULL, DEFAULT 'pending', CHECK(status IN ('pending','uploading','processing','published','failed','retrying')) | Job status |
| platform_media_id | TEXT | | Platform's media/post ID after publish |
| cover_status | TEXT | | Cover/thumbnail result: none, pending, published, failed, not_supported, skipped (migration 003) |
| cover_error | TEXT | | Why the cover failed / wasn't used (migration 003) |
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
- `002_publishing.sql` — `publish_jobs.options_json` + unique (post_id, account_id)
- `003_content_intake.sql` — content_items, publishing_profiles, cover status columns
- `004_batch_retry.sql` — `posts.auto_retry`, `publish_jobs.auto_retry_used`
- `005_audience_strategy.sql` — `audience_profiles` (+ 16 built-in rows), `posts.audience_profile_id`, `posts.audience_json`
- `006_scheduling.sql` — `posts.scheduled_at`, `posts.schedule_status`, `posts.schedule_json`, index `idx_posts_schedule`

The runner splits statements on `;`, also inside `--` comments and string literals, so a migration must not contain
`;` anywhere except at statement ends. Tables built by `create_all()` only get *server* defaults declared in the ORM:
SQL seed rows rely on them (`AudienceProfile` declares them for that reason).

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

## Phase 5A tables (migration `003_content_intake.sql`)

### content_items
One row per content package (identified by its video), linked to the post that publishes it.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK | |
| content_key | TEXT | NOT NULL, **UNIQUE** (`uq_content_items_key`) | SHA-256 of the video's name, size, and first/last 64 KiB |
| package_name | TEXT | NOT NULL | Folder name when first seen |
| package_path | TEXT | NOT NULL | Current folder (updated on every move) |
| video_path / caption_path / cover_path | TEXT | | Current file paths |
| status | TEXT | NOT NULL, CHECK in (detected, publishing, published, failed) | Package-level status (job rows remain authoritative per destination) |
| post_id | INTEGER | FK posts(id) ON DELETE SET NULL | The post whose jobs publish this package |
| error_message | TEXT | | Why it failed / was invalid |
| detected_at, created_at, updated_at, published_at | TIMESTAMP | | |

### publishing_profiles
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK | |
| name | TEXT | NOT NULL, **UNIQUE** | `default` (only one used for now) |
| settings_json | TEXT | NOT NULL | `{"accounts": {"youtube": [ids]...}, "cover_enabled", "after_success", "mode", "tiktok_privacy_level", "youtube_privacy_status", "youtube_made_for_kids", "audience_profile_id"}`: account IDs only, never tokens |
| created_at, updated_at | TIMESTAMP | | |

`audience_profile_id` (005) is the audience strategy recorded on every post the Content Inbox creates. Profiles saved
before 005 have no such key and load as `None` (no strategy).

## Audience Strategy (migration `005_audience_strategy.sql`)

Content-strategy metadata only: never sent to a platform, never credentials or personal data. It does not and
cannot guarantee distribution to a country (see ARCHITECTURE.md §7b-2).

### audience_profiles
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK | |
| name | TEXT | NOT NULL, **UNIQUE** (`uq_audience_profiles_name`) | e.g. `United States`, `North America` |
| description | TEXT | | Optional |
| countries_json | TEXT | NOT NULL, DEFAULT `'[]'` | JSON list of ISO 3166-1 alpha-2 codes, e.g. `["US","CA"]`; `[]` = global (built-in Global only) |
| language | TEXT | | ISO 639 code, e.g. `en` |
| caption_locale | TEXT | | e.g. `en-US` |
| timezone_strategy | TEXT | NOT NULL, DEFAULT `global`, CHECK in (global, audience_local, manual) | `AudienceTimeStrategy` |
| builtin | INTEGER | NOT NULL, DEFAULT 0 | 1 = seeded by migration 005 |
| enabled | INTEGER | NOT NULL, DEFAULT 1 | 0 = hidden from new posts (profiles are never deleted) |
| created_at, updated_at | TIMESTAMP | NOT NULL, DEFAULT CURRENT_TIMESTAMP | |

**Why a JSON list and not a join table:** countries are always read and written as one set with their profile and
never queried per country, and every code is validated in `src/content/audience.py` against the ISO table. This
follows the existing `settings_json` convention; any valid ISO code can be stored without a schema change. If
per-country queries are ever needed (analytics), add a join table then.

**Built-in rows** (seeded with `INSERT OR IGNORE`): Global `[]`; United States `US`/en-US; Canada `CA`/en-CA;
United Kingdom `GB`/en-GB; Australia `AU`/en-AU; Germany `DE`/de-DE; France `FR`/fr-FR; Japan `JP`/ja-JP;
South Korea `KR`/ko-KR; Netherlands `NL`/nl-NL; Sweden `SE`/sv-SE; Norway `NO`/nb-NO; Denmark `DK`/da-DK;
Switzerland `CH`/de-CH; Singapore `SG`/en-SG; New Zealand `NZ`/en-NZ.

### Timing suggestions (V2.1): no schema change
Audience-aware timing suggestions (ARCHITECTURE.md §7b-3) are computed in memory from the snapshot below and are
**not stored**. Recomputing from a post's `audience_json` uses the countries recorded at creation; results also
depend on the installed tzdata release. (Chosen publish times are stored by V2.2 scheduling, below.)

## Scheduling (V2.2, migration `006_scheduling.sql`)

Scheduling is held at the **post** level; jobs stay ordinary jobs and the job state machine is unchanged.

| `schedule_status` | Meaning |
|---|---|
| NULL | Ordinary post (publish now). All posts created before 006. |
| `scheduled` | **Held**: waiting for `scheduled_at`. |
| `missed` | **Held**: the due scheduler found it more than 60 minutes late; never published automatically. |
| `cancelled` | **Held**: cancelled by the user; its jobs stay `pending` (not turned into failures). |
| `released` | Handed to the normal publishing flow; publishing outcomes live in the jobs. |

- **Hold gate** (`src/core/jobs.py`): jobs of a held post are never claimed, returned by `open_post_ids()` (resume)
  or by `retry_owed_post_ids()` (automatic retry). The condition is part of the same SQL statement.
- **Creation is atomic**: `JobStore.create_post(..., scheduled_at, schedule_status, schedule_json)` writes the post,
  its hold and its pending jobs in one transaction.
- **Transitions** (SchedulingService, DueScheduler) are single conditional UPDATEs on the current status (plus the
  time window for the scheduler) and the current `schedule_json` (compare-and-swap), so concurrent actors have
  exactly one winner and history entries are never lost.
- **`schedule_json`** (sorted keys): `source` (manual | suggestion), `local`, `zone`, `zone_source`, `fold`,
  `dst_overlap`, `utc`, `tzdata_version`, optional `suggestion` {start_utc, end_utc, score, coverage, engine_version,
  tzdata_version}, and append-only `history` [{at, action, from_utc, to_utc, ...}] with actions scheduled,
  rescheduled, cancelled, publish_now, missed, released. No secrets; `scheduled_at` (UTC) is authoritative.

### Historical snapshot (`posts.audience_json`)
Written once when the post is created (`AudienceStore.attach`) and never replaced:
```json
{"profile_id": 2, "name": "United States", "countries": ["US"], "language": "en",
 "caption_locale": "en-US", "timezone_strategy": "audience_local"}
```
Editing, renaming or disabling the profile later leaves this untouched; `audience_profile_id` is only a link for
future analytics grouping. Posts created before migration 005 keep NULL in both columns (= no strategy) and publish
exactly as before.
