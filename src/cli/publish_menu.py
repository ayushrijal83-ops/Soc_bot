"""Minimal CLI for Phase 4: create a post, preview the plan, publish, and inspect the queue."""

from pathlib import Path

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
from src.platforms.base import redact
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

    cover = _ask_cover()
    if cover is False:
        return

    caption = prompt_text("Caption", required=False, default="") or ""

    accounts = account_manager.get_active_accounts()
    if not accounts:
        print_error("No active connected accounts. Connect one under Connected Accounts first.")
        return
    chosen = select_accounts(accounts)
    if not chosen:
        print_error("No valid accounts selected.")
        return

    if any(a.platform == "instagram" for a in chosen):
        _instagram_notice(check.media.size_bytes, bool(cover))
    destinations, labels = [], {}
    for account in chosen:
        label = f"{account.platform} / {account.display_name or account.username}"
        options = _ask_options(account.platform, label, caption, check.media.size_bytes)
        if options is None:
            return
        adapter = engine.publishers.get(account.platform)
        if cover and adapter is not None and adapter.cover_plan(cover)[0] == "upload":
            options["cover_path"] = cover  # the SAME local file for every account; never copied
        destinations.append((account.id, options))
        labels[account.id] = label

    # Duplicate rule: same video + same account needs an explicit yes (asked once for all of them).
    repeats = [d for d in destinations if engine.store.already_published(video_path, d[0])]
    if repeats:
        print_warning("This video was already published to: " + ", ".join(labels[d[0]] for d in repeats))
        if not confirm(f"Publish it again to these {len(repeats)} account(s)?", default=False):
            destinations = [d for d in destinations if d not in repeats]
    if not destinations:
        print_info("Nothing to publish.")
        return

    plan = engine.plan_destinations(video_path, caption, destinations)
    print_plan(plan)  # validation only: no network, nothing saved yet
    valid = [d for d, item in zip(destinations, plan) if item.ready]
    print_batch_summary(check.media.size_bytes, video_path, cover, destinations, plan, engine)
    if not valid:
        print_error("Fix the problems above before publishing.")
        return
    if len(valid) < len(destinations):
        question = f"Publish to the {len(valid)} valid account(s)? ({len(destinations) - len(valid)} blocked, not published)"
    else:
        question = "Publish now?"
    if not confirm(question, default=False):
        print_info("Cancelled. Nothing was published.")
        return

    try:
        post_id = engine.store.create_post(video_path, caption, valid, auto_retry=True)
    except JobError as e:
        print_error(str(e))
        return
    for job in engine.store.jobs_for_post(post_id):
        if job.options.get("cover_path"):
            engine.store.set_cover_status(job.id, "pending")
    print_info(f"Publishing post #{post_id}...")
    result = engine.publish_post(post_id, on_update=batch_progress(engine, post_id))
    _print_summary(result.jobs, engine)


def _ask_cover() -> str | None | bool:
    """Optional cover image path. None = no cover, False = cancelled (bad path)."""
    answer = prompt_text("Cover image (optional, used for every selected account; Enter for none)", required=False)
    if not answer:
        return None
    path = Path(answer.strip().strip('"'))
    if not path.is_file():
        print_error(f"Cover image not found: {path.name}")
        return False
    return str(path.resolve())


def select_accounts(accounts: list) -> list:
    """Multi-select: A = all, N = none, numbers/ranges toggle (1,3,5-8), Enter = continue."""
    selected: set[int] = set()
    count = len(accounts)
    while True:
        print_header("SELECT ACCOUNTS")
        for i, account in enumerate(accounts, 1):
            mark = "x" if i in selected else " "
            print(f"  [{mark}] {i:>3}. {account.platform:<10} {account.display_name or account.username}")
        print(f"Selected: {len(selected)} account(s)")
        try:
            answer = input("A = all, N = none, numbers to toggle (e.g. 1,3,5-8), Enter = continue: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return []
        if not answer:
            return [accounts[i - 1] for i in sorted(selected)]
        if answer == "a":
            selected = set(range(1, count + 1))
        elif answer == "n":
            selected.clear()
        else:
            picked = _parse_numbers(answer, count)
            if picked is None:
                print_error(f"Enter A, N, or numbers between 1 and {count}.")
            else:
                selected ^= picked  # toggle; a set, so an account can never be selected twice


def _parse_numbers(text: str, count: int) -> set[int] | None:
    picked: set[int] = set()
    for token in text.replace(" ", ",").split(","):
        if not token:
            continue
        first, _, last = token.partition("-")
        try:
            lo, hi = int(first), int(last or first)
        except ValueError:
            return None
        if not 1 <= lo <= hi <= count:
            return None
        picked.update(range(lo, hi + 1))
    return picked or None


def print_batch_summary(size: int, video_path: str, cover: str | None, destinations, plan, engine) -> None:
    """One summary for the whole batch (shown once, before the single confirmation). No network."""
    from src.core.publisher import max_concurrent_publishes
    from src.media_storage import delivery_provider
    from src.media_storage.router import fmt_mb

    ready = [item for item in plan if item.ready]
    instagram = [item for item in ready if item.platform == "instagram"]
    print_header("CREATE POST")
    print(f"Video:        {Path(video_path).name} ({fmt_mb(size)})")
    print(f"Cover:        {Path(cover).name + ' (same cover for all selected accounts that support covers)' if cover else 'none'}")
    platforms = sorted({item.platform for item in plan})
    print(f"Platforms:    {', '.join(platforms)}")
    print(f"Accounts:     {len(plan)} selected, {len(ready)} valid, {len(plan) - len(ready)} invalid")
    for item in plan:
        if not item.ready:
            print(f"  BLOCKED {item.platform} / {item.account_label}: {'; '.join(item.errors)}")
    if instagram:
        limit = engine.max_concurrent or max_concurrent_publishes()
        provider = delivery_provider(size, bool(cover)) or "none available"
        names = {"cloudflare_tunnel": "Cloudflare Quick Tunnel", "tempfile": "TempFile.org", "s3": "S3", "0x0": "0x0.st"}
        cover_size = Path(cover).stat().st_size if cover else 0
        active = min(limit, len(instagram))
        print(f"Instagram:    {len(instagram)} job(s), at most {limit} at a time "
              f"(initial active: {active}, pending: {len(instagram) - active}; a freed slot starts the next job)")
        print(f"Media:        AUTO -> {names.get(provider, provider)}; ONE shared temporary video{' + cover' if cover else ''} "
              "URL for the whole batch, only while the batch runs")
        if provider == "cloudflare_tunnel":
            print("Tunnel:       1 shared Cloudflare Quick Tunnel for this batch (not one per account)")
            print("Storage:      none (the files stay on this computer; nothing is uploaded permanently)")
        print(f"Retry:        failed Instagram jobs are retried automatically ONCE after the initial round "
              f"(after {PublisherEngine._retry_delay():g} s, same tunnel)")
        print(f"Transfer:     ~{fmt_mb((size + cover_size) * len(instagram))} outbound for Instagram "
              "(each account fetches its own copy)")


def _instagram_notice(size: int, cover: bool) -> None:
    """How Instagram gets the media. Printed once per batch, not once per account."""
    from src.media_storage import delivery_provider, public_host_name

    host = public_host_name(size)
    if delivery_provider(size, cover) == "cloudflare_tunnel":
        print_warning("Instagram: the video stays on this computer. After you confirm, Soc_bot opens ONE temporary "
                      "Cloudflare Quick Tunnel for the whole batch (random public HTTPS URLs for this video and "
                      "cover only) and closes it when every account is done. Quick Tunnels are a Cloudflare testing "
                      "service (no uptime guarantee).")
    elif host:
        print_warning(f"Instagram: the video will be temporarily uploaded to {host}, a PUBLIC third-party file "
                      "host. Anyone with the generated URL may be able to download it until the file expires or "
                      "Soc_bot deletes it after publishing.")
    else:
        print_info("Instagram: the local video is delivered automatically (temporary private storage).")


def _ask_options(platform: str, label: str, caption: str, size: int | None = None) -> dict | None:
    if platform != "instagram":
        print_info(f"Options for {label}:")
    if platform == "instagram":
        # No URL prompt and no per-account questions: media delivery is automatic (see _instagram_notice).
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


def batch_progress(engine: PublisherEngine, post_id: int):
    """Per-job lines plus the batch counts after each change. Never prints URLs."""
    statuses = {job.id: job.status for job in engine.store.jobs_for_post(post_id)}

    def update(result: JobResult) -> None:
        _print_update(result)
        statuses[result.job_id] = result.status
        values = list(statuses.values())
        running = sum(v in ("uploading", "processing") for v in values)
        print(f"    [Total {len(values)} | Running {running} | Retrying {values.count('retrying')} | "
              f"Pending {values.count('pending')} | Published {values.count('published')} | "
              f"Failed {values.count('failed')}]")

    return update


def _print_update(update: JobResult) -> None:
    line = f"  {update.platform:<10} {update.account_label:<20} {update.status.upper()}"
    if update.error:
        line += f"  ({update.error})"
    print(line)


def _print_summary(jobs: list[JobResult], engine: PublisherEngine | None = None) -> None:
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
    links = getattr(engine, "links", None)
    if links is None:
        return
    for platform in sorted({j.platform for j in jobs}):
        done = [j for j in jobs if j.platform == platform and j.status == "published"]
        if not done:
            continue
        saved_ids = {r.get("provider_id") for r in links.records(platform)}
        saved = sum(j.platform_media_id in saved_ids for j in done)
        title = platform.title() if platform != "youtube" else "YouTube"
        print(f"\n{title}: {len(done)} published, {sum(j.platform == platform and j.status == 'failed' for j in jobs)} failed")
        print(f"  Permanent links saved: {saved}")
        if saved < len(done):
            reason = ("TikTok returns no public post URL" if platform == "tiktok"
                      else "link not available yet: run Published Links -> Import from publish history")
            print_warning(f"  {len(done) - saved} without a saved link ({reason})")
        print(f"  Link file:          {links.path(platform)}")
        print(f"  Plain-text links:   {links.text_path(platform)}")


def run_publishing_queue(engine: PublisherEngine) -> None:
    clear_screen()
    print_header("PUBLISHING QUEUE")
    jobs = engine.store.recent_jobs(limit=500)
    if not jobs:
        print_info("No publishing jobs yet.")
        return
    print_batches(engine, jobs)
    choice = prompt_choice("Action", ["Continue open jobs (pending/processing)", "Retry a failed job", "Back"], default=3)
    if choice == 1:
        for result in engine.resume_open_jobs(on_update=_print_update):
            _print_summary(result.jobs, engine)
    elif choice == 2:
        job_id = prompt_int("Failed job ID")
        if job_id is None:
            return
        try:
            _print_summary([engine.retry_job(job_id, on_update=_print_update)], engine)
        except JobError as e:
            print_error(str(e))



MARKS = {"published": "✓", "failed": "✗", "pending": "·", "retrying": "↻", "uploading": "→", "processing": "→"}


def print_batches(engine: PublisherEngine, jobs: list) -> None:
    """Jobs grouped by post (a post = one video + caption fanned out to many accounts). Never prints URLs."""
    from src.core.publisher import batch_status

    by_post: dict[int, list] = {}
    for job in jobs:
        by_post.setdefault(job.post_id, []).append(job)
    for post_id in sorted(by_post, reverse=True)[:10]:
        post_jobs = sorted(by_post[post_id], key=lambda j: j.id)
        try:
            _, video = engine.store.get_post(post_id)
            name = video.filename
        except JobError:
            name = "?"
        covers = {Path(j.options["cover_path"]).name for j in post_jobs if j.options.get("cover_path")}
        statuses = [j.status for j in post_jobs]
        running = sum(s in ("uploading", "processing", "retrying") for s in statuses)
        print(f"\nBATCH #{post_id}  {batch_status(statuses).upper()}")
        print(f"  Video: {name}   Cover: {', '.join(sorted(covers)) or 'none'}   Accounts: {len(post_jobs)}")
        print(f"  Published: {statuses.count('published')}   Running: {running}   "
              f"Pending: {statuses.count('pending')}   Failed: {statuses.count('failed')}")
        for j in post_jobs:
            line = f"  {MARKS.get(j.status, ' ')} job {j.id:<5} {j.platform:<10} {j.account_label:<24} {j.status.upper()}"
            if j.status == "failed" and j.error_message:
                line += f"  ({redact(j.error_message)[:80]})"
            print(line)
