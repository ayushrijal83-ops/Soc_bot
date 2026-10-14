-- 008_group_batches.sql
-- Account Groups, phase 2: track the group a group-publishing batch was created from.
-- posts.origin_group_id:   link to the group (SET NULL if the group is later deleted).
-- posts.origin_group_name: immutable snapshot of the group's name taken at batch creation, so the
--                           origin stays visible even after the group is renamed or deleted.
-- NULL in both = an ordinary post (individual-account publishing, unchanged behaviour).
-- The migration runner splits on semicolons, so none appear except at statement ends.

ALTER TABLE posts ADD COLUMN origin_group_id INTEGER REFERENCES account_groups(id) ON DELETE SET NULL;

ALTER TABLE posts ADD COLUMN origin_group_name TEXT;

CREATE INDEX IF NOT EXISTS idx_posts_origin_group ON posts(origin_group_id);
