-- 004_batch_retry.sql
-- Instagram batches: one automatic retry round for the batch's failed jobs.
-- posts.auto_retry: NULL = never retried automatically (all posts created before this migration),
--                   1    = failed Instagram jobs of this post get ONE automatic retry after the initial round.
-- publish_jobs.auto_retry_used: 1 once the automatic retry was started (or a manual retry took over):
--                   never a second automatic retry, also across crashes/resume.

ALTER TABLE posts ADD COLUMN auto_retry INTEGER;

ALTER TABLE publish_jobs ADD COLUMN auto_retry_used INTEGER NOT NULL DEFAULT 0;
