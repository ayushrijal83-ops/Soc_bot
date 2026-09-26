"""Content intake: package -> validation -> profile -> plan -> existing PublisherEngine.

This layer never talks to a platform. It turns a package plus the saved profile into the
same (account_id, options) destinations the manual Create Post flow uses, and lets the
engine and the platform adapters do the rest.
"""

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.accounts.manager import AccountManager
from src.content.detector import ContentDetector
from src.content.manager import ContentManager
from src.content.models import ContentPackage, content_root, stability_seconds
from src.content.profile import Profile, ProfileStore
from src.content.validator import ContentValidator, is_stable
from src.core.jobs import JobInfo
from src.core.publisher import JobResult, PublisherEngine
from src.storage.database import ContentItem, Database

SCAN_STAGES = ("incoming", "publishing", "failed")


@dataclass
class DestinationPlan:
    account_id: int
    platform: str
    account_label: str
    errors: list[str]
    notes: list[str]
    cover_status: str   # none | upload | not_supported | skipped | disabled
    cover_reason: str

    @property
    def ready(self) -> bool:
        return not self.errors


@dataclass
class InboxEntry:
    package: ContentPackage
    status: str                     # INVALID | COPYING | NO PROFILE | BLOCKED | READY | RESUME | FAILED | PUBLISHED
    item_status: str | None = None  # content_items.status, if the package is known
    post_id: int | None = None
    destinations: list[DestinationPlan] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Accounts that already have a job for this content (never planned or published again).
    done_accounts: set[int] = field(default_factory=set)

    @property
    def publishable(self) -> bool:
        return self.status in ("READY", "RESUME", "FAILED")


@dataclass
class PackageResult:
    content_id: str
    outcome: str                    # published | failed | in_progress | invalid | skipped
    message: str
    stage: str | None = None
    jobs: list[JobInfo] = field(default_factory=list)


def content_key(package: ContentPackage) -> str:
    """Stable identity of the package's video: name, size, first and last 64 KiB (no full-file hash).

    Deliberately excludes the folder name and caption: a package renamed during a move (timestamp
    suffix) or with an edited caption must still be recognised, so finished destinations are never
    published twice.
    """
    digest = hashlib.sha256(b"soc_bot-content-v1")
    if package.video_path is not None:
        size = package.video_path.stat().st_size
        digest.update(f"|{package.video_path.name.lower()}|{size}|".encode())
        with open(package.video_path, "rb") as f:
            digest.update(f.read(65536))
            if size > 65536:
                f.seek(max(0, size - 65536))
                digest.update(f.read(65536))
    return digest.hexdigest()


class ContentIntake:
    def __init__(
        self,
        database: Database,
        account_manager: AccountManager,
        engine: PublisherEngine,
        root: Path | None = None,
        stability: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
        probe_media: bool = True,
    ):
        self.database = database
        self.account_manager = account_manager
        self.engine = engine
        self.root = Path(root).resolve() if root else content_root()
        self.stability = stability_seconds() if stability is None else stability
        self.sleep = sleep
        self.manager = ContentManager(self.root)
        self.manager.ensure_dirs()
        self.detector = ContentDetector(self.root)
        self.validator = ContentValidator(probe_media=probe_media)
        self.profiles = ProfileStore(database)

    # --- inbox (read-only: safe for dry-run) --------------------------------------------

    def scan(self, stages: tuple[str, ...] = SCAN_STAGES) -> list[InboxEntry]:
        profile = self.profiles.load()
        entries = []
        for stage in stages:
            for package in self.detector.scan(stage):
                entries.append(self.inspect(package, profile))
        return entries

    def inspect(self, package: ContentPackage, profile: Profile | None, live: bool = False) -> InboxEntry:
        self.validator.validate(package)
        if package.valid and package.stage == "incoming" and not is_stable(package.package_path, self.stability, self.sleep):
            package.validation_status = "copying"
        entry = InboxEntry(package, status="INVALID")
        if package.validation_status == "copying":
            entry.status = "COPYING"
            return entry
        if not package.valid:
            return entry

        item = self._find_item(content_key(package))
        if item is not None:
            entry.item_status, entry.post_id = item.status, item.post_id
        if item is not None and item.status == "published":
            # Same video + same account is never published twice. Accounts added to the profile
            # since then (e.g. TikTok after YouTube) can still receive it.
            entry.done_accounts = {j.account_id for j in self.engine.store.jobs_for_post(item.post_id)} if item.post_id else set()
            new_accounts = [a for a in (profile.account_ids() if profile else []) if a not in entry.done_accounts]
            if not new_accounts:
                entry.status = "PUBLISHED"
                return entry
            entry.notes.append("Already published to the earlier destinations; only new profile destinations will be published.")
        if profile is None:
            entry.status = "NO PROFILE"
            entry.problems.append("No publishing profile yet: create one under Settings.")
            return entry

        entry.problems = self.profiles.check(profile)
        if not entry.problems:  # planning needs every referenced account to exist
            entry.destinations = self.plan(package, profile, live=live, skip=entry.done_accounts)
        if entry.problems or not all(d.ready for d in entry.destinations):
            entry.status = "BLOCKED"
        elif package.stage == "publishing" or (item is not None and item.status == "publishing"):
            entry.status = "RESUME"
        elif package.stage == "failed" or (item is not None and item.status == "failed"):
            entry.status = "FAILED"
        else:
            entry.status = "READY"
        return entry

    def destinations(self, package: ContentPackage, profile: Profile, video_dir: Path | None = None,
                     skip: set[int] | None = None) -> list[tuple[int, dict]]:
        """Profile -> (account_id, options) for the existing engine. ``video_dir`` = where the files will be."""
        base = video_dir or package.package_path
        result = []
        for platform, account_ids in profile.accounts.items():
            adapter = self.engine.publishers.get(platform)
            for account_id in account_ids:
                if skip and account_id in skip:
                    continue
                options: dict[str, Any] = {}
                if platform == "tiktok":
                    options["privacy_level"] = profile.tiktok_privacy_level
                elif platform == "youtube":
                    options["title"] = package.title or _first_line(package.caption_text)
                    options["privacy_status"] = profile.youtube_privacy_status
                    options["made_for_kids"] = profile.youtube_made_for_kids
                if profile.cover_enabled and package.cover_path and adapter is not None:
                    status, _ = adapter.cover_plan(str(package.cover_path))
                    if status == "upload":
                        options["cover_path"] = str(base / package.cover_path.name)
                result.append((account_id, options))
        return result

    def plan(self, package: ContentPackage, profile: Profile, live: bool = False,
             skip: set[int] | None = None) -> list[DestinationPlan]:
        """Every destination: validation + cover handling. No writes.

        live=False (inbox list, dry-run): no network at all. live=True (the VERIFY screen): also run each
        adapter's read-only preflight (e.g. TikTok creator_info) so the user confirms against real limits.
        """
        dests = self.destinations(package, profile, skip=skip)
        items = self.engine.plan_destinations(str(package.video_path), package.caption_text or "", dests)
        if live:
            media = self._media(package)
            for (account_id, options), item in zip(dests, items, strict=True):
                if not item.errors:
                    notes, errors = self.engine.preflight(account_id, options, media, package.caption_text or "")
                    item.notes.extend(notes)
                    item.errors.extend(errors)
        plans = []
        for (account_id, options), item in zip(dests, items, strict=True):
            adapter = self.engine.publishers.get(item.platform)
            cover_status, cover_reason = self._cover(package, profile, adapter)
            errors = list(item.errors)
            if item.platform == "youtube" and any("title" in e for e in errors) and not package.title:
                errors.append("Add a title.txt to the package (YouTube titles come from its first caption line otherwise).")
            plans.append(DestinationPlan(account_id, item.platform, item.account_label, errors, item.notes,
                                         cover_status, cover_reason))
        return plans

    def live_entry(self, entry: InboxEntry) -> InboxEntry:
        """Re-inspect a package with provider preflight checks, for the verification screen."""
        package = self.detector.detect(entry.package.package_path, entry.package.stage)
        return self.inspect(package, self.profiles.load(), live=True)

    def _media(self, package: ContentPackage):
        from src.core.validation import validate_video_file

        return validate_video_file(package.video_path, probe=self.validator.probe_media).media

    @staticmethod
    def _cover(package: ContentPackage, profile: Profile, adapter) -> tuple[str, str]:
        if not package.cover_path:
            return "none", "no cover in package"
        if not profile.cover_enabled:
            return "disabled", "covers are disabled in the profile"
        if adapter is None:
            return "not_supported", "no publisher"
        return adapter.cover_plan(str(package.cover_path))

    # --- publishing ---------------------------------------------------------------------

    def publish(
        self,
        package: ContentPackage,
        retry_failed: bool = False,
        on_update: Callable[[JobResult], None] | None = None,
    ) -> PackageResult:
        """Publish one package with the saved profile. The caller has already confirmed (VERIFY) or
        the profile is AUTO. Runs to completion without asking anything."""
        profile = self.profiles.load()
        if profile is None:
            return PackageResult(package.content_id, "skipped", "No publishing profile yet.")
        problems = self.profiles.check(profile)
        if problems:
            return PackageResult(package.content_id, "skipped", "; ".join(problems))

        # Re-detect from disk right before publishing: the folder may have changed since the inbox view.
        package = self.detector.detect(package.package_path, package.stage)
        entry = self.inspect(package, profile)
        if entry.status == "COPYING":
            return PackageResult(package.content_id, "skipped", "Files are still being copied; try again shortly.")
        if entry.status == "PUBLISHED":
            return PackageResult(package.content_id, "skipped", "Already published; not publishing again.",
                                 stage=package.stage)
        key = content_key(package) if package.video_path else None
        if entry.status in ("INVALID", "BLOCKED"):
            reasons = package.validation_errors + entry.problems + [
                f"{d.platform} {d.account_label}: {e}" for d in entry.destinations for e in d.errors]
            moved = self.manager.move(package.package_path, "failed")
            if key:
                self._save_item(key, package, moved, "failed", error="; ".join(reasons))
            return PackageResult(package.content_id, "invalid", "; ".join(reasons), stage="failed")

        # Into publishing/ first, then create or reuse the post so paths point at publishing/.
        moved = self.manager.move(package.package_path, "publishing")
        package.package_path = moved
        package.video_path = moved / package.video_path.name
        if package.cover_path:
            package.cover_path = moved / package.cover_path.name
        item = self._save_item(key, package, moved, "publishing")
        dests = self.destinations(package, profile, moved, skip=entry.done_accounts)
        cover_by_account = {d.account_id: (d.cover_status, d.cover_reason)
                            for d in self.plan(package, profile, skip=entry.done_accounts)}

        if item.post_id is None:
            post_id = self.engine.store.create_post(str(package.video_path), package.caption_text or "", dests,
                                                    auto_retry=True)
            # The video row is de-duplicated by checksum and may carry an older path.
            self.engine.store.update_video_path(post_id, str(package.video_path))
            self._link_post(item.id, post_id)
            new_jobs = [j.id for j in self.engine.store.jobs_for_post(post_id)]
        else:
            post_id = item.post_id
            self.engine.store.update_video_path(post_id, str(package.video_path))
            new_jobs = self.engine.store.add_missing_jobs(post_id, dests)
        for job in self.engine.store.jobs_for_post(post_id):
            if job.id in new_jobs:
                status, reason = cover_by_account.get(job.account_id, ("none", ""))
                initial = {"upload": "pending", "none": None}.get(status, status)
                self.engine.store.set_cover_status(job.id, initial, reason if initial not in (None, "pending") else None)

        self.engine.publish_post(post_id, on_update=on_update)
        if retry_failed:
            for job in self.engine.store.jobs_for_post(post_id):
                if job.status == "failed":
                    self.engine.retry_job(job.id, on_update=on_update)
        return self._finish(item.id, package, profile, post_id)

    def publish_ready(self, on_update=None) -> list[PackageResult]:
        """AUTO mode: publish every READY/RESUME package without asking. Validation still applies:
        invalid or blocked packages are moved to failed/ instead of being published."""
        results = []
        for entry in self.scan():
            if entry.status in ("READY", "RESUME", "INVALID", "BLOCKED"):
                results.append(self.publish(entry.package, on_update=on_update))
        return results

    def _finish(self, item_id: int, package: ContentPackage, profile: Profile, post_id: int) -> PackageResult:
        jobs = self.engine.store.jobs_for_post(post_id)
        statuses = {j.status for j in jobs}
        if statuses == {"published"}:
            stage = "archive" if profile.after_success == "archive" else "published"
            moved = self.manager.move(package.package_path, stage)
            self._update_item(item_id, status="published", package_path=str(moved),
                              published_at=datetime.now(timezone.utc), error_message=None)
            return PackageResult(package.content_id, "published", "All destinations published.", stage, jobs)
        if "failed" in statuses:
            moved = self.manager.move(package.package_path, "failed")
            failed = [f"{j.platform} {j.account_label}" for j in jobs if j.status == "failed"]
            message = "Failed: " + ", ".join(failed)
            self._update_item(item_id, status="failed", package_path=str(moved), error_message=message)
            return PackageResult(package.content_id, "failed", message, "failed", jobs)
        self._update_item(item_id, status="publishing", package_path=str(package.package_path))
        return PackageResult(package.content_id, "in_progress",
                             "Some destinations are still processing; open the Content Inbox again to continue.",
                             "publishing", jobs)

    # --- content_items --------------------------------------------------------------------

    def _find_item(self, key: str) -> ContentItem | None:
        with self.database.session() as session:
            return session.query(ContentItem).filter_by(content_key=key).first()

    def _save_item(self, key: str, package: ContentPackage, path: Path, status: str, error: str | None = None) -> ContentItem:
        """Insert or update the item for this content key (unique: one item per package content)."""
        with self.database.session() as session:
            item = session.query(ContentItem).filter_by(content_key=key).first()
            if item is None:
                item = ContentItem(content_key=key, package_name=package.content_id, detected_at=package.detected_at)
                session.add(item)
            item.package_path = str(path)
            item.video_path = str(path / package.video_path.name) if package.video_path else None
            item.caption_path = str(path / package.caption_path.name) if package.caption_path else None
            item.cover_path = str(path / package.cover_path.name) if package.cover_path else None
            item.status = status
            item.error_message = error
            session.commit()
            return item

    def _link_post(self, item_id: int, post_id: int) -> None:
        self._update_item(item_id, post_id=post_id)

    def _update_item(self, item_id: int, **values) -> None:
        with self.database.session() as session:
            item = session.get(ContentItem, item_id)
            for k, v in values.items():
                setattr(item, k, v)
            session.commit()

    def history(self, limit: int = 20) -> list[tuple[ContentItem, list[JobInfo]]]:
        with self.database.session() as session:
            items = session.query(ContentItem).order_by(ContentItem.id.desc()).limit(limit).all()
        return [(item, self.engine.store.jobs_for_post(item.post_id) if item.post_id else []) for item in items]


def _first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""
