"""CLI for Phase 5A: Content Inbox, publishing profile settings, and history."""

from src.accounts.manager import AccountManager
from src.cli.display import (
    clear_screen,
    confirm,
    print_error,
    print_header,
    print_info,
    print_success,
    print_table,
    print_warning,
)
from src.cli.prompts import prompt_choice, prompt_text
from src.content.intake import ContentIntake, InboxEntry, PackageResult
from src.content.profile import PLATFORMS, Profile, ProfileError
from src.core.publisher import JobResult
from src.platforms.tiktok.publisher import PRIVACY_LEVELS
from src.platforms.youtube.publisher import PRIVACY_STATUSES

COVER_LABELS = {
    "upload": "will upload after the video",
    "none": "no cover",
    "disabled": "disabled in profile",
    "not_supported": "not supported",
    "skipped": "skipped",
    "pending": "pending",
    "published": "PUBLISHED",
    "failed": "FAILED",
}


# --- inbox ------------------------------------------------------------------------------

def print_inbox(entries: list[InboxEntry]) -> None:
    rows = []
    for i, e in enumerate(entries, 1):
        p = e.package
        rows.append([
            str(i), p.content_id, p.stage,
            p.video_path.name if p.video_path else "-",
            "yes" if p.caption_path else "-",
            p.cover_path.name if p.cover_path else "-",
            e.status,
        ])
    print_table(["#", "Package", "Folder", "Video", "Caption", "Cover", "Status"], rows)


def print_entry(entry: InboxEntry) -> None:
    """The one-time verification screen."""
    p = entry.package
    print_header("NEW CONTENT" if entry.status == "READY" else f"CONTENT: {entry.status}")
    print(f"Package:  {p.content_id}  ({p.stage}/)")
    print(f"Video:    {p.video_path.name if p.video_path else 'MISSING'}")
    caption = (p.caption_text or "").strip()
    preview = caption if len(caption) <= 300 else caption[:300] + " …"
    print(f"Caption:\n{preview if preview else '(missing)'}")
    print(f"Cover:    {p.cover_path.name if p.cover_path else 'none'}")
    for error in p.validation_errors:
        print_error(error)
    for warning in p.validation_warnings:
        print_warning(warning)
    for problem in entry.problems:
        print_error(problem)
    for note in entry.notes:
        print_info(note)
    if entry.destinations:
        print_header("DESTINATIONS")
        for platform in PLATFORMS:
            dests = [d for d in entry.destinations if d.platform == platform]
            if not dests:
                continue
            print(f"{platform.title()}:")
            for d in dests:
                mark = "✓" if d.ready else "✗"
                cover = COVER_LABELS.get(d.cover_status, d.cover_status)
                reason = f" ({d.cover_reason})" if d.cover_status in ("not_supported", "skipped") else ""
                print(f"  {mark} {d.account_label}    cover: {cover}{reason}")
                for e in d.errors:
                    print(f"      ERROR: {e}")
                for n in d.notes:
                    print(f"      note: {n}")


def print_job_update(update: JobResult) -> None:
    line = f"  {update.platform:<10} {update.account_label:<24} {update.status.upper()}"
    if update.error:
        line += f"  ({update.error})"
    print(line)


def print_result(result: PackageResult) -> None:
    print_header(f"RESULT: {result.content_id}")
    for job in result.jobs:
        line = f"  {job.platform:<10} {job.account_label:<24} VIDEO: {job.status.upper()}"
        if job.cover_status:
            line += f"   COVER: {COVER_LABELS.get(job.cover_status, job.cover_status)}"
        print(line)
        if job.error_message:
            print(f"      {job.error_message}")
        if job.cover_error:
            print(f"      cover: {job.cover_error}")
    {"published": print_success, "failed": print_error}.get(result.outcome, print_warning)(result.message)
    if result.stage:
        print_info(f"Package folder is now in {result.stage}/")


def run_content_inbox(intake: ContentIntake) -> None:
    clear_screen()
    print_header("CONTENT INBOX")
    print_info(f"Scanning {intake.root / 'incoming'} ...")
    entries = intake.scan()
    if not entries:
        print_info("No content packages found. Put a folder with video + caption.txt (+ cover.jpg) in content/incoming/.")
        return
    print_inbox(entries)

    profile = intake.profiles.load()
    if profile is not None and profile.mode == "auto":
        print_info("Profile mode AUTO: publishing every ready package without confirmation.")
        for result in intake.publish_ready(on_update=print_job_update):
            print_result(result)
        return

    choice = prompt_text("Package number to review (blank = back)", required=False, default="")
    if not choice:
        return
    try:
        entry = entries[int(choice) - 1]
    except (ValueError, IndexError):
        print_error("Invalid package number.")
        return
    verify_and_publish(intake, entry)


def verify_and_publish(intake: ContentIntake, entry: InboxEntry) -> PackageResult | None:
    """VERIFY mode: show the plan, ask ONE confirmation, then publish everything without asking again."""
    if entry.publishable:
        print_info("Checking destinations with the providers (read-only)...")
        entry = intake.live_entry(entry)  # e.g. TikTok creator_info: real privacy options + limits
    print_entry(entry)
    if not entry.publishable:
        print_error(f"{entry.package.content_id} can't be published ({entry.status}).")
        return None
    question = {
        "READY": "Publish to all selected destinations?",
        "RESUME": "Continue this unfinished publish? (finished destinations are not published again)",
        "FAILED": "Retry the failed destinations? (published ones are not published again)",
    }[entry.status]
    print_header("READY TO PUBLISH")
    if not confirm(question, default=False):
        print_info("Cancelled. Nothing was published.")
        return None
    print_info("Publishing...")
    result = intake.publish(entry.package, retry_failed=entry.status == "FAILED", on_update=print_job_update)
    print_result(result)
    return result


# --- profile settings --------------------------------------------------------------------

def run_settings(intake: ContentIntake, account_manager: AccountManager) -> None:
    clear_screen()
    print_header("SETTINGS")
    choice = prompt_choice("Option", ["Create/Edit Publishing Profile", "View Publishing Profile",
                                      "Reset Publishing Profile", "Check Instagram media storage", "Back"], default=5)
    if choice == 1:
        edit_profile(intake, account_manager)
    elif choice == 2:
        show_profile(intake, account_manager)
    elif choice == 3 and confirm("Delete the default publishing profile?", default=False):
        print_success("Profile reset." if intake.profiles.reset() else "There was no profile.")
    elif choice == 4:
        check_media_storage()


def check_media_storage() -> bool:
    """Validate MEDIA_STORAGE_* settings and run the provider's upload-free check."""
    from src.media_storage import (
        MediaStorageError,
        StorageSettings,
        create_media_provider,
    )

    settings = StorageSettings.from_env()
    problems = settings.problems()
    if problems:
        print_error("Instagram temporary media storage is not configured.")
        for problem in problems:
            print_info(f"  {problem}")
        return False
    if settings.provider == "0x0":
        print_warning(f"Provider: 0x0.st ({settings.zerox0_url}): a PUBLIC third-party file host. Videos leave this "
                      f"computer; anyone with the link can download them until deleted (max {settings.zerox0_expires_hours} h).")
    elif settings.provider == "auto":
        print_info("Media storage mode: AUTO (by video size; no fallback between providers after a failure)")
        print_warning("Small videos: TempFile.org, PUBLIC temporary hosting (anyone with the link can download "
                      "until Soc_bot deletes it).")
        print_info("Large videos: S3, PRIVATE temporary object storage; Instagram gets a presigned HTTPS URL.")
    elif settings.provider == "tempfile":
        print_warning("Provider: TempFile.org. Temporary PUBLIC hosting: enabled; no credentials required. Videos leave "
                      "this computer; anyone with the link can download them until Soc_bot deletes them "
                      f"(expiry {settings.tempfile_expiry_hours} h, max 100 MB).")
    else:
        print_info(f"Provider: {settings.provider}; bucket: {settings.bucket}; "
                   f"endpoint: {settings.endpoint or 'AWS (' + settings.region + ')'}; URL lifetime: {settings.ttl}s")
    try:
        message = create_media_provider().health_check()
    except MediaStorageError as e:
        print_error(str(e))
        return False
    print_success(message)
    return True


def show_profile(intake: ContentIntake, account_manager: AccountManager, profile: Profile | None = None) -> None:
    profile = profile or intake.profiles.load()
    if profile is None:
        print_info("No publishing profile yet.")
        return
    print_header("PUBLISHING PROFILE")
    accounts = {a.id: a for a in account_manager.list_accounts()}
    for platform in PLATFORMS:
        print(platform.title())
        ids = profile.accounts.get(platform, [])
        if not ids:
            print("  (none)")
        for aid in ids:
            a = accounts.get(aid)
            label = f"{a.display_name or a.username} [{a.status}]" if a else f"#{aid} (missing)"
            print(f"  [x] {label}")
    print(f"Cover/Thumbnail: {'[x] Enabled' if profile.cover_enabled else '[ ] Disabled'}")
    print(f"After successful publishing: move to {profile.after_success}/")
    print(f"Publishing mode: {profile.mode.upper()}")
    if profile.accounts.get("tiktok"):
        print(f"TikTok privacy level: {profile.tiktok_privacy_level}")
    if profile.accounts.get("youtube"):
        print(f"YouTube privacy: {profile.youtube_privacy_status}; made for kids: {profile.youtube_made_for_kids}")
    for problem in intake.profiles.check(profile):
        print_warning(problem)


def edit_profile(intake: ContentIntake, account_manager: AccountManager) -> Profile | None:
    current = intake.profiles.load() or Profile()
    profile = Profile(**{**current.__dict__, "accounts": {p: list(v) for p, v in current.accounts.items()}})
    all_accounts = account_manager.list_accounts()
    for platform in PLATFORMS:
        options = [a for a in all_accounts if a.platform == platform]
        if not options:
            profile.accounts[platform] = []
            continue
        print_header(platform.title())
        for i, a in enumerate(options, 1):
            mark = "x" if a.id in current.accounts.get(platform, []) else " "
            print(f"  [{mark}] {i}. {a.display_name or a.username} [{a.status}]")
        default = ",".join(str(i) for i, a in enumerate(options, 1) if a.id in current.accounts.get(platform, []))
        answer = prompt_text("  Accounts (comma-separated numbers, blank = none)", required=False, default=default)
        if answer is None:
            return None
        picked = []
        for part in answer.split(","):
            if part.strip().isdigit() and 1 <= int(part) <= len(options):
                picked.append(options[int(part) - 1].id)
        profile.accounts[platform] = sorted(set(picked))

    if profile.accounts["tiktok"]:
        print_info("TikTok requires the privacy level to be chosen by you. Unaudited apps can only use SELF_ONLY.")
        choice = prompt_choice("TikTok privacy level", list(PRIVACY_LEVELS))
        if choice is None:
            return None
        profile.tiktok_privacy_level = PRIVACY_LEVELS[choice - 1]
    if profile.accounts["youtube"]:
        choice = prompt_choice("YouTube privacy", list(PRIVACY_STATUSES),
                               default=PRIVACY_STATUSES.index(profile.youtube_privacy_status) + 1)
        if choice is None:
            return None
        profile.youtube_privacy_status = PRIVACY_STATUSES[choice - 1]
        profile.youtube_made_for_kids = confirm("YouTube: are these videos made for kids?", default=profile.youtube_made_for_kids)

    profile.cover_enabled = confirm("Use cover/thumbnail images where the platform supports it?", default=profile.cover_enabled)
    after = prompt_choice("After successful publishing", ["Move to published", "Move to archive"],
                          default=1 if profile.after_success == "published" else 2)
    mode = prompt_choice("Publishing mode", ["VERIFY (confirm each package once)", "AUTO (no confirmation)"],
                         default=1 if profile.mode == "verify" else 2)
    if after is None or mode is None:
        return None
    profile.after_success = ("published", "archive")[after - 1]
    profile.mode = ("verify", "auto")[mode - 1]

    show_profile(intake, account_manager, profile)
    if not confirm("Save profile?", default=True):
        print_info("Not saved.")
        return None
    try:
        intake.profiles.save(profile)
    except ProfileError as e:
        print_error(str(e))
        return None
    print_success("Profile saved.")
    return profile


# --- history -----------------------------------------------------------------------------

def run_history(intake: ContentIntake) -> None:
    clear_screen()
    print_header("HISTORY")
    history = intake.history()
    if not history:
        print_info("No content has been published yet.")
        return
    for item, jobs in history:
        print(f"\nContent: {item.package_name}   [{item.status.upper()}]")
        print(f"Created: {item.created_at:%Y-%m-%d %H:%M}" + (f"   Published: {item.published_at:%Y-%m-%d %H:%M}" if item.published_at else ""))
        if item.error_message:
            print(f"Note: {item.error_message}")
        if jobs:
            print("Destinations:")
            for j in jobs:
                line = f"  {j.platform.title()} {j.account_label} — VIDEO: {j.status.upper()}"
                if j.platform_media_id:
                    line += f" (id {j.platform_media_id})"
                print(line)
            covers = [j for j in jobs if j.cover_status]
            if covers:
                print("Cover:")
                for j in covers:
                    print(f"  {j.platform.title()} {j.account_label} — {COVER_LABELS.get(j.cover_status, j.cover_status).upper()}")
