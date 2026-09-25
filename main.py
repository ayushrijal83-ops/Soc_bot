#!/usr/bin/env python3
"""Social Publisher (SOC_BOT) - Main entry point."""

import argparse
import sys

from src.accounts.manager import AccountManager
from src.auth.manager import create_auth_manager
from src.cli.menu import run_menu
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
  python main.py --dry-run       # Show dry-run info (placeholder)
        """
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be published without making API calls (not yet implemented)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="SOC_BOT v1.0.0",
    )
    return parser.parse_args()


def initialize_services():
    """Initialize database, encryption, account manager, and auth manager."""
    db = get_database()
    encryption = TokenEncryption()
    account_manager = AccountManager(db, encryption)
    auth_manager = create_auth_manager(db, encryption, account_manager)
    return account_manager, auth_manager


def main() -> int:
    """Main entry point."""
    args = parse_args()

    if args.dry_run:
        print("DRY-RUN MODE")
        print("============")
        print("Dry-run mode is not implemented yet — coming in a later phase.")
        return 0

    try:
        account_manager, auth_manager = initialize_services()
        run_menu(account_manager=account_manager, auth_manager=auth_manager)
        return 0
    except KeyboardInterrupt:
        print("\n\nInterrupted. Goodbye!")
        return 0
    except Exception as e:  # noqa: BLE001 - top-level CLI boundary
        print(f"\n[ERROR] Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())