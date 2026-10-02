-- 006_scheduling.sql
-- V2.2 Real Scheduling, phase 1: post-level schedule hold (schema only, no scheduler yet).
-- posts.scheduled_at:    publish instant as naive UTC (NULL = publish now, all older posts).
-- posts.schedule_status: NULL = ordinary post (unchanged behaviour).
--                        scheduled / missed / cancelled = HELD: JobStore never claims, resumes or
--                        auto-retries the post's jobs (they stay pending).
--                        released = handed to the engine, jobs run normally.
-- posts.schedule_json:   how the time was chosen and its change history (no secrets).
-- The migration runner splits on semicolons, so none appear except at statement ends.

ALTER TABLE posts ADD COLUMN scheduled_at TIMESTAMP;

ALTER TABLE posts ADD COLUMN schedule_status TEXT CHECK(schedule_status IN ('scheduled','released','missed','cancelled'));

ALTER TABLE posts ADD COLUMN schedule_json TEXT;

CREATE INDEX IF NOT EXISTS idx_posts_schedule ON posts(schedule_status, scheduled_at);
