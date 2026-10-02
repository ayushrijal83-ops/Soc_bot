# ruff: noqa: F811 - pytest fixtures imported from other test modules
"""Audience Strategy V1: profiles, validation, selection during post creation, immutable snapshots."""

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from src.content.audience import (
    ISO_COUNTRIES,
    Audience,
    AudienceError,
    AudienceStore,
    parse_countries,
    summary,
)
from src.content.profile import Profile, ProfileStore
from src.storage.database import Database, Post
from tests.unit import test_content
from tests.unit.test_content import env as content_env  # noqa: F401
from tests.unit.test_content import intake_with, make_package, save_profile
from tests.unit.test_publishing_engine import env  # noqa: F401
from tests.unit.test_tui import run_app, services  # noqa: F401

BUILTINS = ["Global", "United States", "Canada", "United Kingdom", "Australia", "Germany", "France", "Japan",
            "South Korea", "Netherlands", "Sweden", "Norway", "Denmark", "Switzerland", "Singapore", "New Zealand"]


@pytest.fixture
def store(env):
    return AudienceStore(env[0])


def by_name(store, name):
    return next(a for a in store.list(include_disabled=True) if a.name == name)


# --- A/B/C: built-in, country and multi-country profiles -------------------------------------

def test_builtin_profiles_seeded_with_iso_codes(store):
    profiles = store.list()
    assert [a.name for a in profiles] == BUILTINS and all(a.builtin and a.enabled for a in profiles)
    glob = profiles[0]
    assert glob.countries == [] and glob.timezone_strategy == "global" and glob.label == "Global"
    codes = [c for a in profiles for c in a.countries]
    assert codes == ["US", "CA", "GB", "AU", "DE", "FR", "JP", "KR", "NL", "SE", "NO", "DK", "CH", "SG", "NZ"]
    us = by_name(store, "United States")
    assert (us.language, us.caption_locale, us.timezone_strategy) == ("en", "en-US", "audience_local")
    assert len(ISO_COUNTRIES) == 249


def test_create_country_profile_any_iso_code(store):
    saved = store.save(Audience("Mexico", ["MX"], "es", "es-MX", "audience_local"))
    assert saved.id and not saved.builtin
    assert store.get(saved.id).countries == ["MX"]  # not one of the built-ins: no schema/code change needed


def test_create_multi_country_custom_profile(store):
    saved = store.save(Audience("North America", parse_countries("us, ca"), "EN", "en_us", "audience_local",
                                "US + Canada"))
    loaded = store.get(saved.id)
    assert loaded.countries == ["US", "CA"] and loaded.language == "en" and loaded.caption_locale == "en-US"
    assert loaded.label == "North America (US, CA)"
    assert loaded.name in [a.name for a in store.list()]


# --- D/E/F: validation -----------------------------------------------------------------------

@pytest.mark.parametrize("codes, message", [
    (["XX"], "Not ISO 3166-1"),
    (["UK"], "Not ISO 3166-1"),     # the ISO code is GB
    (["USA"], "Not ISO 3166-1"),
    (["US", "CA", "US"], "Duplicate country codes: US"),
    ([], "at least one country"),
])
def test_rejects_invalid_duplicate_and_empty(store, codes, message):
    with pytest.raises(AudienceError, match=message):
        store.save(Audience("Bad", codes))
    assert "Bad" not in [a.name for a in store.list(include_disabled=True)]


def test_rejects_bad_locale_strategy_and_duplicate_name(store):
    with pytest.raises(AudienceError, match="Caption locale"):
        store.save(Audience("X", ["US"], caption_locale="english"))
    with pytest.raises(AudienceError, match="Caption locale"):
        store.save(Audience("X", ["US"], caption_locale="en-XX"))
    with pytest.raises(AudienceError, match="Language"):
        store.save(Audience("X", ["US"], language="english"))
    with pytest.raises(AudienceError, match="Timezone strategy"):
        store.save(Audience("X", ["US"], timezone_strategy="night"))
    with pytest.raises(AudienceError, match="already exists"):
        store.save(Audience("Canada", ["CA"]))


# --- G/H/I/J: selection, persistence, immutable snapshot ------------------------------------

def test_selected_strategy_persisted_as_snapshot(services):
    audiences = services.audiences
    us = by_name(audiences, "United States")
    ids = [a.id for a in services.accounts.list("instagram")][:2]
    plan = services.publishing.plan(services.video, "hi", None, ids)
    plan.audience_id = us.id
    post_id = services.publishing.create_batch(plan)
    expected = {"profile_id": us.id, "name": "United States", "countries": ["US"], "language": "en",
                "caption_locale": "en-US", "timezone_strategy": "audience_local"}
    assert audiences.for_post(post_id) == expected
    assert services.publishing.batch(post_id).audience == expected
    with services.engine.store.database.session() as session:
        assert session.get(Post, post_id).audience_profile_id == us.id
    # Publishing is unchanged by the strategy (nothing extra reaches the adapter).
    assert services.publishing.publish_batch(post_id).status == "completed"


def test_editing_or_disabling_profile_keeps_post_history(services):
    audiences = services.audiences
    us = by_name(audiences, "United States")
    plan = services.publishing.plan(services.video, "hi", None, [services.accounts.list("instagram")[0].id])
    plan.audience_id = us.id
    post_id = services.publishing.create_batch(plan)
    before = audiences.for_post(post_id)

    us.countries, us.caption_locale, us.name = ["US", "CA"], "en-CA", "USA + Canada"
    audiences.save(us)
    us.enabled = False
    audiences.save(us)
    assert audiences.for_post(post_id) == before and before["countries"] == ["US"]
    assert audiences.attach(post_id, by_name(audiences, "Japan").id) == before  # never replaced
    assert "USA + Canada" not in [a.name for a in audiences.list()]           # disabled: hidden for new posts

    plan.audience_id = us.id
    with pytest.raises(ValueError, match="no longer available"):
        services.publishing.create_batch(plan, include_already_published=True)


def test_content_intake_records_profile_strategy(content_env):
    db = content_env[0]
    jp = by_name(AudienceStore(db), "Japan")
    save_profile(content_env, audience_profile_id=jp.id)
    make_package(content_env[3])
    intake = intake_with(content_env, test_content.TestIntakePublishing().pubs())
    intake.publish(intake.scan()[0].package)
    post_id = intake.history()[0][0].post_id
    assert AudienceStore(db).for_post(post_id)["countries"] == ["JP"]


def test_publishing_profile_audience_is_optional_and_checked(content_env):
    db = content_env[0]
    legacy = Profile.from_json(json.dumps({"name": "default", "mode": "auto"}))  # saved before audience existed
    assert legacy.audience_profile_id is None
    profile = save_profile(content_env)
    assert not ProfileStore(db).check(profile)
    profile.audience_profile_id = 9999
    assert any("Audience profile #9999" in p for p in ProfileStore(db).check(profile))


# --- K: posts without a strategy, and upgrading an existing database --------------------------

def test_post_without_strategy_keeps_working(services):
    ids = [a.id for a in services.accounts.list("instagram")][:1]
    post_id = services.publishing.create_batch(services.publishing.plan(services.video, "", None, ids))
    assert services.audiences.for_post(post_id) is None
    view = services.publishing.publish_batch(post_id)
    assert view.status == "completed" and view.audience is None
    assert summary(None) == "none (Global)"


def test_existing_database_upgrades_with_old_posts_untouched(tmp_path):
    migrations = Path(__file__).resolve().parents[2] / "src" / "storage" / "migrations"
    db = Database(f"sqlite:///{tmp_path / 'old.db'}")
    for number, name in enumerate(["001_initial_schema", "002_publishing", "003_content_intake",
                                   "004_batch_retry"], 1):
        db.apply_migration(number, name, (migrations / f"{name}.sql").read_text())
    with db.session() as session:
        session.execute(text("INSERT INTO videos (filename, path, size_bytes) VALUES ('a.mp4', 'a.mp4', 1)"))
        session.execute(text("INSERT INTO posts (video_id, caption) VALUES (1, 'old post')"))
        session.commit()

    db.create_all()  # what main.py does on start
    db.migrate(verbose=False)
    assert db.get_applied_migrations() == [1, 2, 3, 4, 5, 6]
    with db.session() as session:
        old = session.get(Post, 1)
        assert old.caption == "old post" and old.audience_json is None and old.audience_profile_id is None
    assert [a.name for a in AudienceStore(db).list()] == BUILTINS
    db.engine.dispose()


# --- TUI: choose the strategy in Create Post, see it on Review ---------------------------------

def test_tui_create_post_selects_audience_and_shows_it_on_review(services):
    from textual.widgets import Input, Select, SelectionList, Static

    async def script(app, pilot):
        await pilot.press("c")
        await pilot.pause()
        s = app.screen
        s.query_one("#video-path", Input).value = services.video
        s.action_next()
        await pilot.pause()
        select = s.query_one("#audience", Select)
        assert select.value == by_name(services.audiences, "Global").id  # default
        select.value = by_name(services.audiences, "Germany").id
        s.action_next()
        await pilot.pause()
        s.toggle_platform("instagram")
        await pilot.pause()
        s.query_one("#accounts", SelectionList).select_all()
        await pilot.pause()
        s.action_next()
        await pilot.pause()
        s.action_next()
        await pilot.pause()
        review = str(s.query_one("#review", Static).render())
        assert "Germany (DE)" in review and "de-DE" in review
        s._publish(True)
        await pilot.pause()

    run_app(services, script)
    post_id = services.publishing.batches()[0].post_id
    assert services.audiences.for_post(post_id)["name"] == "Germany"
