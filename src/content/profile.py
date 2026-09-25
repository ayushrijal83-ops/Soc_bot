"""Publishing profile: which accounts receive content packages, and how.

Stored as JSON in ``publishing_profiles``. Holds account IDs and non-secret options only;
tokens stay with the accounts (encrypted). One "default" profile for now; the table is keyed
by name so more profiles can be added later.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from src.storage.database import Account, Database, PublishingProfile

PLATFORMS = ("instagram", "tiktok", "youtube")
MODES = ("verify", "auto")
AFTER_SUCCESS = ("published", "archive")
DEFAULT_NAME = "default"


@dataclass
class Profile:
    name: str = DEFAULT_NAME
    accounts: dict[str, list[int]] = field(default_factory=lambda: {p: [] for p in PLATFORMS})
    cover_enabled: bool = True
    after_success: str = "published"
    mode: str = "verify"
    # Per-platform options the adapters require, chosen once by the user.
    tiktok_privacy_level: str | None = None
    youtube_privacy_status: str = "private"
    youtube_made_for_kids: bool = False

    def account_ids(self) -> list[int]:
        return [aid for p in PLATFORMS for aid in self.accounts.get(p, [])]

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "Profile":
        data: dict[str, Any] = json.loads(text)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        profile = cls(**known)
        profile.accounts = {p: [int(a) for a in profile.accounts.get(p, [])] for p in PLATFORMS}
        return profile


class ProfileError(Exception):
    pass


class ProfileStore:
    def __init__(self, database: Database):
        self.database = database

    def load(self, name: str = DEFAULT_NAME) -> Profile | None:
        with self.database.session() as session:
            row = session.query(PublishingProfile).filter_by(name=name).first()
            return Profile.from_json(row.settings_json) if row else None

    def save(self, profile: Profile) -> Profile:
        problems = self.check(profile, require_active=False)
        if problems:
            raise ProfileError("; ".join(problems))
        with self.database.session() as session:
            row = session.query(PublishingProfile).filter_by(name=profile.name).first()
            if row is None:
                session.add(PublishingProfile(name=profile.name, settings_json=profile.to_json()))
            else:
                row.settings_json = profile.to_json()
            session.commit()
        return profile

    def reset(self, name: str = DEFAULT_NAME) -> bool:
        with self.database.session() as session:
            deleted = session.query(PublishingProfile).filter_by(name=name).delete()
            session.commit()
            return bool(deleted)

    def check(self, profile: Profile, require_active: bool = True) -> list[str]:
        """Problems that stop the profile from being used. Empty list = ready."""
        problems = []
        if profile.mode not in MODES:
            problems.append(f"Unknown mode {profile.mode!r}")
        if profile.after_success not in AFTER_SUCCESS:
            problems.append(f"Unknown after-success action {profile.after_success!r}")
        if not profile.account_ids():
            problems.append("No publishing accounts are enabled in the default profile.")
        with self.database.session() as session:
            for platform in PLATFORMS:
                for account_id in profile.accounts.get(platform, []):
                    account = session.get(Account, account_id)
                    if account is None:
                        problems.append(f"{platform} account #{account_id} no longer exists.")
                    elif account.platform != platform:
                        problems.append(f"Account #{account_id} is a {account.platform} account, not {platform}.")
                    elif require_active and account.status != "active":
                        label = account.display_name or account.username
                        problems.append(f"{platform.title()} {label} is not currently authorized ({account.status}).")
        if profile.accounts.get("tiktok") and not profile.tiktok_privacy_level:
            problems.append("TikTok privacy level is not chosen in the profile.")
        return problems
