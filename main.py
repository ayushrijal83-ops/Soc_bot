#!/usr/bin/env python3
"""Social Publisher (SOC_BOT) - Main entry point."""

import argparse
import sys

from src.accounts.manager import AccountManager
from src.auth.manager import create_auth_manager
from src.cli.menu import run_menu
from src.cli.publish_menu import print_plan
from src.core.publisher import PublisherEngine
from src.storage.database import get_database
from src.storage.tokens import TokenEncryption


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Social Publisher (SOC_BOT) - Terminal-based multi-platform video publisher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                 # Start interactive menu
  python main.py --help          # Show this help
  python main.py --dry-run       # Preview unpublished posts (no API calls)
        """
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and show the plan for unpublished posts. Never contacts a platform, uploads, publishes or refreshes tokens.",
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
    return account_manager, auth_manager, engine


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
        return 0
    for post_id in post_ids:
        print(f"\nPost #{post_id}")
        print_plan(engine.plan_post(post_id))
    return 0


def main() -> int:
    """Main entry point."""
    args = parse_args()

    try:
        if args.dry_run:
            return run_dry_run()
        account_manager, auth_manager, engine = initialize_services()
        run_menu(account_manager=account_manager, auth_manager=auth_manager, engine=engine)
        return 0
    except KeyboardInterrupt:
        print("\n\nInterrupted. Goodbye!")
        return 0
    except Exception as e:  # noqa: BLE001 - top-level CLI boundary
        print(f"\n[ERROR] Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())