-- 001_initial_schema.sql
-- Initial database schema for Social Publisher
-- Applied by migration runner in database.py

-- ============================================================
-- accounts: Connected social media accounts
-- ============================================================
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL CHECK(platform IN ('instagram','tiktok','youtube')),
    platform_account_id TEXT NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT,
    access_token_enc TEXT NOT NULL,
    refresh_token_enc TEXT,
    expires_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','expired','revoked','disconnected')),
    meta_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_platform_id ON accounts(platform, platform_account_id);
CREATE INDEX IF NOT EXISTS idx_accounts_platform ON accounts(platform);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);

-- ============================================================
-- videos: Video file metadata
-- ============================================================
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    duration_seconds INTEGER,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    checksum TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_videos_checksum ON videos(checksum);
CREATE INDEX IF NOT EXISTS idx_videos_path ON videos(path);

-- ============================================================
-- posts: User-created posts (video + caption)
-- ============================================================
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    caption TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_posts_video ON posts(video_id);

-- ============================================================
-- publish_jobs: Individual publishing job per destination
-- ============================================================
CREATE TABLE IF NOT EXISTS publish_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','uploading','processing','published','failed','retrying')),
    platform_media_id TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_post ON publish_jobs(post_id);
CREATE INDEX IF NOT EXISTS idx_jobs_account ON publish_jobs(account_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON publish_jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_next_retry ON publish_jobs(next_retry_at) WHERE status = 'retrying';

-- ============================================================
-- publish_attempts: Detailed attempt history per job
-- ============================================================
CREATE TABLE IF NOT EXISTS publish_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES publish_jobs(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('started','uploading','processing','success','failed')),
    response_json TEXT,
    error_json TEXT,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(job_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS idx_attempts_job ON publish_attempts(job_id);

-- ============================================================
-- schema_version: Tracks applied migration versions
-- ============================================================
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- ============================================================
-- Initial data: Record this migration
-- ============================================================
INSERT OR IGNORE INTO schema_version (version, description) VALUES (1, 'initial_schema');