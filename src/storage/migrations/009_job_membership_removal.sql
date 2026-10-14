-- 009_job_membership_removal.sql
-- Account Groups, phase 3: safely stop a not-yet-started job because its account was removed
-- from its group (or the group was deleted) while the batch is still active.
-- publish_jobs.membership_removed_at: NULL = normal. Set (CAS, pending/retrying only) on removal.
-- This is deliberately an additive nullable column, not a new publish_jobs.status value: SQLite
-- cannot extend an existing column CHECK constraint without rebuilding the table, so ck_jobs_status
-- stays untouched. claim() and open_post_ids() both exclude a job once this is set, so it is never
-- claimed and never keeps resume / --run-due busy. Re-adding the account clears it back to NULL
-- (restore): the existing unique index on (post_id, account_id) already guarantees this is the
-- SAME job row, never a duplicate.
-- The migration runner splits on semicolons, so none appear except at statement ends.

ALTER TABLE publish_jobs ADD COLUMN membership_removed_at TIMESTAMP;
