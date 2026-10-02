-- 005_audience_strategy.sql
-- Audience Strategy V1: reusable audience profiles (content-strategy metadata, NOT platform targeting)
-- and the strategy each post was created with.
-- posts.audience_profile_id: link to the profile (SET NULL if it ever disappears).
-- posts.audience_json:       immutable snapshot taken at creation, editing a profile never changes it.
-- Posts created before this migration keep NULL in both = no strategy (treated as Global).

CREATE TABLE IF NOT EXISTS audience_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    countries_json TEXT NOT NULL DEFAULT '[]',
    language TEXT,
    caption_locale TEXT,
    timezone_strategy TEXT NOT NULL DEFAULT 'global' CHECK(timezone_strategy IN ('global','audience_local','manual')),
    builtin INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_audience_profiles_name ON audience_profiles(name);

ALTER TABLE posts ADD COLUMN audience_profile_id INTEGER REFERENCES audience_profiles(id) ON DELETE SET NULL;

ALTER TABLE posts ADD COLUMN audience_json TEXT;

-- Built-in profiles (ISO 3166-1 alpha-2 country codes). Users may edit or disable them.
INSERT OR IGNORE INTO audience_profiles (name, description, countries_json, language, caption_locale, timezone_strategy, builtin) VALUES
    ('Global', 'No specific country', '[]', NULL, NULL, 'global', 1),
    ('United States', 'Audience in the United States', '["US"]', 'en', 'en-US', 'audience_local', 1),
    ('Canada', 'Audience in Canada', '["CA"]', 'en', 'en-CA', 'audience_local', 1),
    ('United Kingdom', 'Audience in the United Kingdom', '["GB"]', 'en', 'en-GB', 'audience_local', 1),
    ('Australia', 'Audience in Australia', '["AU"]', 'en', 'en-AU', 'audience_local', 1),
    ('Germany', 'Audience in Germany', '["DE"]', 'de', 'de-DE', 'audience_local', 1),
    ('France', 'Audience in France', '["FR"]', 'fr', 'fr-FR', 'audience_local', 1),
    ('Japan', 'Audience in Japan', '["JP"]', 'ja', 'ja-JP', 'audience_local', 1),
    ('South Korea', 'Audience in South Korea', '["KR"]', 'ko', 'ko-KR', 'audience_local', 1),
    ('Netherlands', 'Audience in the Netherlands', '["NL"]', 'nl', 'nl-NL', 'audience_local', 1),
    ('Sweden', 'Audience in Sweden', '["SE"]', 'sv', 'sv-SE', 'audience_local', 1),
    ('Norway', 'Audience in Norway', '["NO"]', 'nb', 'nb-NO', 'audience_local', 1),
    ('Denmark', 'Audience in Denmark', '["DK"]', 'da', 'da-DK', 'audience_local', 1),
    ('Switzerland', 'Audience in Switzerland (multilingual, German default)', '["CH"]', 'de', 'de-CH', 'audience_local', 1),
    ('Singapore', 'Audience in Singapore', '["SG"]', 'en', 'en-SG', 'audience_local', 1),
    ('New Zealand', 'Audience in New Zealand', '["NZ"]', 'en', 'en-NZ', 'audience_local', 1);
