-- 010_account_deletion.sql
-- Permanent account deletion, phase 1: a tombstone, not a row delete.
-- accounts.deleted_at: NULL = normal. Set once, permanently, when an account is deleted.
-- publish_jobs.account_id has ON DELETE CASCADE, and Account.publish_jobs cascades at the ORM
-- level too, so an actual DELETE FROM accounts would silently wipe every job and attempt for
-- that account -- never done. Deletion instead: destroys the stored credentials (overwritten,
-- never nulled: access_token_enc is NOT NULL), unassigns the account from its group, stops its
-- not-yet-started jobs (publish_jobs.membership_removed_at, migration 009 -- reused as-is: a job
-- that will never be claimed again, regardless of whether the reason was group removal or
-- account deletion), and sets deleted_at. The account row, its jobs, attempts and published
-- links are never removed: history and audit integrity are preserved.
-- The migration runner splits on semicolons, so none appear except at statement ends.

ALTER TABLE accounts ADD COLUMN deleted_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_accounts_deleted ON accounts(deleted_at);
