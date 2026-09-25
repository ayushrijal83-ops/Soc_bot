"""Minimal CLI for Phase 4: create a post, preview the plan, publish, and inspect the queue."""

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
from src.cli.prompts import prompt_choice, prompt_int, prompt_text
from src.core.jobs import JobError
from src.core.publisher import JobResult, PlanItem, PublisherEngine
from src.core.validation import validate_video_file
from src.platforms.tiktok.publisher import PRIVACY_LEVELS
from src.platforms.youtube.publisher import PRIVACY_STATUSES


def run_create_post(account_manager: AccountManager, engine: PublisherEngine) -> None:
    clear_screen()
    print_header("CREATE POST")

    video_path = prompt_text("Video file path")
    if not video_path:
        return
    check = validate_video_file(video_path.strip().strip('"'))
    for warning in check.warnings:
        print_warning(warning)
    if not check.ok:
        for error in check.errors:
            print_error(error)
        return
    video_path = check.media.path

    caption = prompt_text("Caption", required=False, default="") or ""

    accounts = account_manager.get_active_accounts()
    if not accounts:
        print_error("No active connected accounts. Connect one under Connected Accounts first.")
        return
    print_info("Destinations:")
    for i, account in enumerate(accounts, 1):
        print(f"  {i}. {account.platform:<10} {account.display_name or account.username}")
    selection = prompt_text("Accounts to publish to (comma-separated numbers)")
    if not selection:
        return
    try:
        picked = sorted({int(x) for x in selection.split(",") if x.strip()})
        chosen = [accounts[i - 1] for i in picked if 1 <= i <= len(accounts)]
    except ValueError:
        chosen = []
    if not chosen:
        print_error("No valid accounts selected.")
        return

    destinations = []
    for account in chosen:
        label = f"{account.platform} / {account.display_name or account.username}"
        options = _ask_options(account.platform, label, caption, check.media.size_bytes)
        if options is None:
            return
        if engine.store.already_published(video_path, account.id) and not confirm(
            f"This video was already published to {label}. Publish it again?", default=False
        ):
            continue
        destinations.append((account.id, options))
    if not destinations:
        print_info("Nothing to publish.")
        return

    plan = engine.plan_destinations(video_path, caption, destinations)
    print_plan(plan)  # validation only: no network, nothing saved yet
    if not all(item.ready for item in plan):
        print_error("Fix the problems above before publishing.")
        return
    if not confirm("Publish now?", default=False):
        print_info("Cancelled. Nothing was published.")
        return

    try:
        post_id = engine.store.create_post(video_path, caption, destinations)
    except JobError as e:
        print_error(str(e))
        return
    print_info(f"Publishing post #{post_id}...")
    result = engine.publish_post(post_id, on_update=_print_update)
    _print_summary(result.jobs)


def _ask_options(platform: str, label: str, caption: str, size: int | None = None) -> dict | None:
    print_info(f"Options for {label}:")
    if platform == "instagram":
        # No URL prompt: the local file is delivered through temporary media storage automatically.
        from src.media_storage import delivery_provider, public_host_name

        host = public_host_name(size)
        if delivery_provider(size) == "cloudflare_tunnel":
            print_warning("  Instagram: the video stays on this computer. After you confirm, Soc_bot opens a temporary "
                          "Cloudflare Quick Tunnel (random public HTTPS URL for this one file) only while Instagram "
                          "fetches it, then closes it. Quick Tunnels are a Cloudflare testing service (no uptime "
                          "guarantee).")
        elif host:
            print_warning(f"  Instagram: the video will be temporarily uploaded to {host}, a PUBLIC third-party file "
                          "host. Anyone with the generated URL may be able to download it until the file expires or "
                          "Soc_bot deletes it after publishing.")
        else:
            print_info("  Instagram: the local video is delivered automatically (temporary private storage).")
        return {}
    if platform == "tiktok":
        print_info("  TikTok requires you to choose the privacy level. Unaudited apps can only use SELF_ONLY.")
        choice = prompt_choice("  Privacy level", list(PRIVACY_LEVELS))
        return None if choice is None else {"privacy_level": PRIVACY_LEVELS[choice - 1]}
    if platform == "youtube":
        default_title = (caption.splitlines()[0] if caption else "")[:100] or None
        title = prompt_text("  YouTube title", default=default_title)
        if title is None:
            return None
        choice = prompt_choice("  Privacy", list(PRIVACY_STATUSES), default=1)
        if choice is None:
            return None
        made_for_kids = confirm("  Is this video made for kids?", default=False)
        return {"title": title, "privacy_status": PRIVACY_STATUSES[choice - 1], "made_for_kids": made_for_kids}
    return {}


def print_plan(plan: list[PlanItem]) -> None:
    print_header("PUBLISHING PLAN")
    rows = []
    for item in plan:
        detail = "; ".join(item.errors) if item.errors else ("; ".join(item.notes) or "ok")
        rows.append([item.platform, item.account_label, "READY" if item.ready else "BLOCKED", detail])
    print_table(["Platform", "Account", "Check", "Details"], rows)


def _print_update(update: JobResult) -> None:
    line = f"  {update.platform:<10} {update.account_label:<20} {update.status.upper()}"
    if update.error:
        line += f"  ({update.error})"
    print(line)


def _print_summary(jobs: list[JobResult]) -> None:
    print_header("RESULT")
    for job in jobs:
        _print_update(job)
    published = sum(j.status == "published" for j in jobs)
    failed = sum(j.status == "failed" for j in jobs)
    processing = len(jobs) - published - failed
    print_success(f"{published} published")
    if processing:
        print_warning(f"{processing} still processing: check again from Publishing Queue")
    if failed:
        print_error(f"{failed} failed")


def run_publishing_queue(engine: PublisherEngine) -> None:
    clear_screen()
    print_header("PUBLISHING QUEUE")
    jobs = engine.store.recent_jobs()
    if not jobs:
        print_info("No publishing jobs yet.")
        return
    print_table(
        ["Job", "Post", "Platform", "Account", "Status", "Detail"],
        [[str(j.id), str(j.post_id), j.platform, j.account_label, j.status.upper(),
          (j.error_message or j.platform_media_id or "")[:60]] for j in jobs],
    )
    choice = prompt_choice("Action", ["Continue open jobs (pending/processing)", "Retry a failed job", "Back"], default=3)
    if choice == 1:
        for result in engine.resume_open_jobs(on_update=_print_update):
            _print_summary(result.jobs)
    elif choice == 2:
        job_id = prompt_int("Failed job ID")
        if job_id is None:
            return
        try:
            _print_summary([engine.retry_job(job_id, on_update=_print_update)])
        except JobError as e:
            print_error(str(e))
