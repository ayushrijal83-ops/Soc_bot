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
from src.core.publisher import PublisherEngine
from src.storage.database import get_database
from src.storage.tokens import TokenEncryption

# The project's .env, found relative to this file so it works from any working directory.
ENV_FILE = Path(__file__).resolve().parent / ".env"


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
  python main.py                 # Start interactive menu
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
    engine = PublisherEngine(db, account_manager, auth_manager)
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
        print_plan(engine.plan_post(post_id))

    # Content inbox: scan + validate + plan only. Nothing is moved, saved or sent.
    intake = ContentIntake(db, engine.account_manager, engine)
    entries = intake.scan()
    print(f"\nContent inbox ({intake.root}): {len(entries)} package(s)")
    for entry in entries:
        print_entry(entry)
    return 0


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
    args = parse_args()
    load_environment()  # before anything reads DATABASE_URL, ENCRYPTION_KEY or platform credentials

    try:
        if args.dry_run:
            return run_dry_run()
        account_manager, auth_manager, engine, intake = initialize_services()
        if args.scan:
            return run_scan(intake)
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