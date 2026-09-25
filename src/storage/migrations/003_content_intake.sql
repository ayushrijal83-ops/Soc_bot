-- 003_content_intake.sql
-- Phase 5A: content packages, publishing profiles, per-job cover status.

ALTER TABLE publish_jobs ADD COLUMN cover_status TEXT;

ALTER TABLE publish_jobs ADD COLUMN cover_error TEXT;

CREATE TABLE IF NOT EXISTS content_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_key TEXT NOT NULL,
    package_name TEXT NOT NULL,
    package_path TEXT NOT NULL,
    video_path TEXT,
    caption_path TEXT,
    cover_path TEXT,
    status TEXT NOT NULL DEFAULT 'detected' CHECK(status IN ('detected','publishing','published','failed')),
    post_id INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    error_message TEXT,
    detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_content_items_key ON content_items(content_key);

CREATE INDEX IF NOT EXISTS idx_content_items_status ON content_items(status);

CREATE TABLE IF NOT EXISTS publishing_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_publishing_profiles_name ON publishing_profiles(name);
