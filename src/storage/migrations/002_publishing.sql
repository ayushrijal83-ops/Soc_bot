-- 002_publishing.sql
-- Phase 4: per-destination publish options and duplicate-job protection.

ALTER TABLE publish_jobs ADD COLUMN options_json TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_post_account ON publish_jobs(post_id, account_id);

