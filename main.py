#!/usr/bin/env python3
"""Social Publisher (SOC_BOT) - Main entry point."""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.accounts.manager import AccountManager
from src.auth.manager import create_auth_manager
from src.cli.content_menu import (
    print_entry,
    print_inbox,
    print_job_update,
    print_result,
)
from src.cli.menu import run_menu
from src.cli.publish_menu import print_plan
from src.content.intake import ContentIntake
from src.core.published_links import PublishedLinks
from src.core.publisher import PublisherEngine
from src.storage.database import get_database
from src.storage.tokens import TokenEncryption

# The project's .env, found relative to this file so it works from any working directory.
ENV_FILE = Path(__file__).resolve().parent / ".env"


def configure_file_logging() -> Path:
    """TUI mode: the screen belongs to the UI, so Soc_bot's log lines go to logs/soc_bot.log instead."""
    import logging

    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    path = log_dir / "soc_bot.log"
    logger = logging.getLogger("soc_bot")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return path


def tui_available() -> bool:
    """A real interactive terminal and an importable Textual; otherwise the classic menu is used."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import textual  # noqa: F401
    except ImportError:
        return False
    return True


def run_tui(account_manager, auth_manager, engine, intake) -> None:
    from src.services import build_services
    from src.tui.app import run_tui as start

    configure_file_logging()
    start(build_services(account_manager, auth_manager, engine, intake, ENV_FILE))


def configure_logging() -> None:
    """Show Soc_bot's own progress messages as "[INFO] ..." (third-party loggers stay quiet)."""
    import logging

    logger = logging.getLogger("soc_bot")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


def load_environment(env_file: Path | None = None) -> bool:
    """Load .env into os.environ. Variables already set in the real environment win.

    Returns True if the file existed. Values are never printed or logged.
    """
    return load_dotenv(env_file or ENV_FILE, override=False)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Social Publisher (SOC_BOT) - Terminal-based multi-platform video publisher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                 # Start the terminal UI (TUI)
  python main.py --plain         # Start the classic text menu instead
  python main.py --help          # Show this help
  python main.py --dry-run       # Preview unpublished posts and content packages (no API calls)
  python main.py --scan          # Scan content/incoming; AUTO profiles publish, VERIFY only lists
        """
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and show the plan for unpublished posts and content packages. Never contacts a platform, uploads, publishes, moves packages or refreshes tokens.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan the content inbox. With an AUTO profile, publish ready packages; with VERIFY, only list them.",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Use the classic text menu instead of the full-screen terminal UI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="SOC_BOT v1.0.0",
    )
    return parser.parse_args()


def open_database():
    """Open the database and bring its schema up to date (local only)."""
    db = get_database()
    db.create_all()
    db.migrate(verbose=False)
    return db


def initialize_services():
    """Initialize database, encryption, account manager, auth manager and publishing engine."""
    db = open_database()
    encryption = TokenEncryption()
    account_manager = AccountManager(db, encryption)
    auth_manager = create_auth_manager(db, encryption, account_manager)
    links = PublishedLinks()
    links.ensure_files()  # content/published_links/{youtube,instagram,tiktok}.json
    engine = PublisherEngine(db, account_manager, auth_manager, links=links)
    intake = ContentIntake(db, account_manager, engine)  # creates content/ folders if missing
    return account_manager, auth_manager, engine, intake


def run_dry_run() -> int:
    """Show the publishing plan for every unpublished post. No provider contact, no token refresh."""
    print("DRY-RUN MODE")
    print("============")
    print("Dry-run never contacts social platforms: nothing is uploaded, published or refreshed.")
    db = open_database()
    engine = PublisherEngine(db, AccountManager(db, db.encryption))
    post_ids = engine.store.open_post_ids()
    if not post_ids:
        print("No unpublished posts. Nothing would be published.")
    for post_id in post_ids:
        print(f"\nPost #{post_id}")
        plan = engine.plan_post(post_id)
        print_plan(plan)
        print_batch_dry_run(engine, post_id, plan)

    # Content inbox: scan + validate + plan only. Nothing is moved, saved or sent.
    intake = ContentIntake(db, engine.account_manager, engine)
    entries = intake.scan()
    print(f"\nContent inbox ({intake.root}): {len(entries)} package(s)")
    for entry in entries:
        print_entry(entry)
    return 0


def print_batch_dry_run(engine, post_id: int, plan) -> None:
    """Instagram batch facts for dry-run (no tunnel, no server, no network, nothing created)."""
    from src.core.publisher import max_concurrent_publishes
    from src.media_storage import delivery_provider

    jobs = [j for j in engine.store.jobs_for_post(post_id) if j.platform == "instagram"
            and j.status not in ("published", "failed")]
    if not jobs:
        return
    _, video = engine.store.get_post(post_id)
    covers = sorted({Path(j.options["cover_path"]).name for j in jobs if j.options.get("cover_path")})
    provider = delivery_provider(video.size_bytes, bool(covers)) or "none available"
    print(f"Instagram batch: accounts {len(jobs)}, jobs {len(jobs)}, concurrency {max_concurrent_publishes()}, "
          f"shared tunnels {1 if provider == 'cloudflare_tunnel' else 0}, provider {provider}, "
          f"cover {', '.join(covers) or 'none'}, automatic retry: "
          f"{'1 final retry round' if engine.store.post_auto_retry(post_id) else 'off (older post)'}")


def run_scan(intake) -> int:
    """Non-interactive inbox scan. AUTO profile: publish ready packages. VERIFY: list only."""
    entries = intake.scan()
    if entries:
        print_inbox(entries)
    else:
        print("No content packages found.")
    profile = intake.profiles.load()
    if profile is None or profile.mode != "auto":
        print("Profile mode is VERIFY (or no profile): nothing published. Use Content Inbox to review and confirm.")
        return 0
    for result in intake.publish_ready(on_update=print_job_update):
        print_result(result)
    return 0


def main() -> int:
    """Main entry point."""
    for stream in (sys.stdout, sys.stderr):  # never crash on ✓/emoji when the console codepage lacks them
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = parse_args()
    load_environment()  # before anything reads DATABASE_URL, ENCRYPTION_KEY or platform credentials
    configure_logging()

    try:
        if args.dry_run:
            return run_dry_run()
        account_manager, auth_manager, engine, intake = initialize_services()
        if args.scan:
            return run_scan(intake)
        if not args.plain and tui_available():
            run_tui(account_manager, auth_manager, engine, intake)
            return 0
        if not args.plain:
            print("[INFO] Full-screen UI not available here (no interactive terminal); using the classic menu.")
        run_menu(account_manager=account_manager, auth_manager=auth_manager, engine=engine, intake=intake)
        return 0
    except KeyboardInterrupt:
        print("\n\nInterrupted. Goodbye!")
        return 0
    except Exception as e:  # noqa: BLE001 - top-level CLI boundary
        print(f"\n[ERROR] Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())