-- 007_account_groups.sql
-- Account Groups, phase 1: group accounts into content niches (e.g. AI Anime, Motivation).
-- An account belongs to zero or one group: a single nullable accounts.group_id column, not a
-- join table. Deleting a group sets accounts.group_id back to NULL (ON DELETE SET NULL).
-- Accounts are never disconnected or deleted by group membership changes.
-- The migration runner splits on semicolons, so none appear except at statement ends.

CREATE TABLE IF NOT EXISTS account_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_account_groups_name ON account_groups(name);

ALTER TABLE accounts ADD COLUMN group_id INTEGER REFERENCES account_groups(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_accounts_group ON accounts(group_id);
