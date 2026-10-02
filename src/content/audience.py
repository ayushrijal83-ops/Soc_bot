"""Audience strategy: reusable content-strategy metadata attached to posts.

An audience profile names the countries (ISO 3166-1 alpha-2), language, caption locale and posting-time
strategy a post is *meant for*. It is NOT geographic targeting: organic Instagram/TikTok/YouTube
publishing has no audience-country parameter, so nothing here is ever sent to a platform. The platforms
alone decide who sees a post. The strategy is kept for scheduling, captions and analytics later.

History is reproducible: a post stores an immutable JSON snapshot of the strategy chosen when it was
created (``posts.audience_json``), so editing or disabling a profile never rewrites existing posts.
Built-in profiles are seeded by migration 005; custom ones are created by the user.
"""

import json
import re
from dataclasses import dataclass, field

from src.storage.database import AudienceProfile, Database, Post

# Officially assigned ISO 3166-1 alpha-2 codes (249). Data, not logic: any of them is a valid audience.
ISO_COUNTRIES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW
BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI
FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN
IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME
MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF
PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV
SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE
YT ZA ZM ZW""".split())  # noqa: SIM905 - compact, readable table


class AudienceTimeStrategy:
    """When a post should go out. Only recorded in V1; no scheduler reads it yet."""

    GLOBAL = "global"                  # no preferred time zone
    AUDIENCE_LOCAL = "audience_local"  # good local time in the audience's countries
    MANUAL = "manual"                  # the user picks the time


TIME_STRATEGIES = (AudienceTimeStrategy.GLOBAL, AudienceTimeStrategy.AUDIENCE_LOCAL, AudienceTimeStrategy.MANUAL)
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
LOCALE_RE = re.compile(r"^([a-z]{2,3})-([A-Z]{2})$")


class AudienceError(Exception):
    pass


@dataclass
class Audience:
    name: str
    countries: list[str] = field(default_factory=list)  # ISO codes; empty = global (built-in Global only)
    language: str | None = None
    caption_locale: str | None = None
    timezone_strategy: str = AudienceTimeStrategy.GLOBAL
    description: str = ""
    enabled: bool = True
    builtin: bool = False
    id: int | None = None

    @property
    def label(self) -> str:
        return f"{self.name} ({', '.join(self.countries)})" if self.countries else self.name

    def snapshot(self) -> dict:
        """What a post keeps forever, whatever happens to the profile later."""
        return {"profile_id": self.id, "name": self.name, "countries": list(self.countries),
                "language": self.language, "caption_locale": self.caption_locale,
                "timezone_strategy": self.timezone_strategy}


def parse_countries(text: str) -> list[str]:
    """'us, ca gb' -> ['US', 'CA', 'GB'] (order kept; validation happens in ``problems``)."""
    return [c.strip().upper() for c in re.split(r"[,\s]+", text or "") if c.strip()]


def normalize_locale(value: str | None) -> str | None:
    """'en_us' / 'EN-us' -> 'en-US'; blank -> None."""
    value = (value or "").strip().replace("_", "-")
    if not value:
        return None
    lang, _, region = value.partition("-")
    return f"{lang.lower()}-{region.upper()}" if region else lang.lower()


def summary(snapshot: dict | None) -> str:
    """One line for review screens and history."""
    if not snapshot:
        return "none (Global)"
    parts = [snapshot["name"] + (f" ({', '.join(snapshot['countries'])})" if snapshot.get("countries") else "")]
    if snapshot.get("caption_locale") or snapshot.get("language"):
        parts.append(snapshot.get("caption_locale") or snapshot["language"])
    parts.append(snapshot.get("timezone_strategy", AudienceTimeStrategy.GLOBAL).replace("_", "-") + " time")
    return "  ·  ".join(parts)


def problems(audience: Audience) -> list[str]:
    """Why the profile can't be saved. Empty list = valid."""
    found = []
    if not audience.name.strip():
        found.append("Name is required.")
    if not audience.countries and not audience.builtin:
        found.append("Choose at least one country (ISO code such as US, CA, GB).")
    invalid = [c for c in audience.countries if c not in ISO_COUNTRIES]
    if invalid:
        found.append(f"Not ISO 3166-1 alpha-2 country codes: {', '.join(invalid)}")
    duplicates = sorted({c for c in audience.countries if audience.countries.count(c) > 1})
    if duplicates:
        found.append(f"Duplicate country codes: {', '.join(duplicates)}")
    if audience.language and not LANGUAGE_RE.match(audience.language):
        found.append(f"Language must be an ISO 639 code such as en, de, ja (got {audience.language!r}).")
    if audience.caption_locale:
        match = LOCALE_RE.match(audience.caption_locale)
        if not match or match.group(2) not in ISO_COUNTRIES:
            found.append(f"Caption locale must look like en-US (got {audience.caption_locale!r}).")
    if audience.timezone_strategy not in TIME_STRATEGIES:
        found.append(f"Timezone strategy must be one of {', '.join(TIME_STRATEGIES)}.")
    return found


def _from_row(row: AudienceProfile) -> Audience:
    return Audience(row.name, json.loads(row.countries_json or "[]"), row.language, row.caption_locale,
                    row.timezone_strategy, row.description or "", bool(row.enabled), bool(row.builtin), row.id)


class AudienceStore:
    """Audience profiles (no credentials, no personal data) and their link to posts."""

    def __init__(self, database: Database):
        self.database = database

    def list(self, include_disabled: bool = False) -> list[Audience]:
        """Built-ins first (Global on top, seeded order), then custom profiles by name."""
        with self.database.session() as session:
            query = session.query(AudienceProfile)
            if not include_disabled:
                query = query.filter(AudienceProfile.enabled == 1)
            rows = query.order_by(AudienceProfile.builtin.desc(), AudienceProfile.id).all()
            audiences = [_from_row(r) for r in rows]
        return [a for a in audiences if a.builtin] + sorted((a for a in audiences if not a.builtin),
                                                            key=lambda a: a.name.lower())

    def get(self, profile_id: int) -> Audience | None:
        with self.database.session() as session:
            row = session.get(AudienceProfile, profile_id)
            return _from_row(row) if row else None

    def save(self, audience: Audience) -> Audience:
        """Create (id None) or update a profile. Existing posts keep their snapshot."""
        audience.name = audience.name.strip()
        audience.language = (audience.language or "").strip().lower() or None
        audience.caption_locale = normalize_locale(audience.caption_locale)
        found = problems(audience)
        with self.database.session() as session:
            clash = session.query(AudienceProfile).filter(AudienceProfile.name == audience.name,
                                                          AudienceProfile.id != (audience.id or 0)).first()
            if clash:
                found.append(f"A profile named {audience.name!r} already exists.")
            if found:
                raise AudienceError("; ".join(found))
            row = session.get(AudienceProfile, audience.id) if audience.id else None
            if audience.id and row is None:
                raise AudienceError(f"Audience profile #{audience.id} not found")
            if row is None:
                row = AudienceProfile(builtin=0)
                session.add(row)
            row.name, row.description = audience.name, audience.description
            row.countries_json = json.dumps(audience.countries)
            row.language, row.caption_locale = audience.language, audience.caption_locale
            row.timezone_strategy, row.enabled = audience.timezone_strategy, int(audience.enabled)
            session.commit()
            audience.id, audience.builtin = row.id, bool(row.builtin)
        return audience

    def attach(self, post_id: int, profile_id: int) -> dict:
        """Record the chosen strategy on a post, once. An existing snapshot is never replaced."""
        audience = self.get(profile_id)
        if audience is None or not audience.enabled:
            raise AudienceError(f"Audience profile #{profile_id} is not available")
        with self.database.session() as session:
            post = session.get(Post, post_id)
            if post is None:
                raise AudienceError(f"Post {post_id} not found")
            if post.audience_json:
                return json.loads(post.audience_json)
            snapshot = audience.snapshot()
            post.audience_profile_id, post.audience_json = audience.id, json.dumps(snapshot)
            session.commit()
            return snapshot

    def for_post(self, post_id: int) -> dict | None:
        """The snapshot as selected at creation; None for posts made before audience strategy existed."""
        with self.database.session() as session:
            post = session.get(Post, post_id)
            return json.loads(post.audience_json) if post is not None and post.audience_json else None
