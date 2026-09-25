"""Main menu system for the CLI."""

from collections.abc import Callable

from src.cli.account_menu import run_account_menu
from src.cli.content_menu import run_content_inbox, run_history, run_settings
from src.cli.display import (
    clear_screen,
    print_header,
    print_menu,
    print_not_implemented,
)
from src.cli.prompts import prompt_menu_selection
from src.cli.publish_menu import run_create_post, run_publishing_queue


class MenuHandler:
    """Handles menu navigation and routing."""

    def __init__(self, account_manager=None, auth_manager=None, engine=None, intake=None):
        self.running = True
        self.account_manager = account_manager
        self.auth_manager = auth_manager
        self.engine = engine
        self.intake = intake
        self.menu_options = [
            "Create Post",
            "Connected Accounts",
            "Publishing Queue",
            "History",
            "Content Inbox",
            "Settings",
            "Exit",
        ]

    def show_main_menu(self) -> None:
        """Display the main menu."""
        clear_screen()
        print_menu(self.menu_options, "SOCIAL PUBLISHER v1")

    def handle_choice(self, choice: int) -> bool:
        """
        Handle a menu choice.

        Returns:
            True if should continue, False if should exit.
        """
        handlers: dict[int, Callable[[], bool]] = {
            1: self.handle_create_post,
            2: self.handle_connected_accounts,
            3: self.handle_publishing_queue,
            4: self.handle_history,
            5: self.handle_content_inbox,
            6: self.handle_settings,
            7: self.handle_exit,
        }

        handler = handlers.get(choice)
        if handler:
            return handler()
        return True

    def handle_create_post(self) -> bool:
        """Handle Create Post option."""
        if self.engine is None or self.account_manager is None:
            print_not_implemented("Create Post")
        else:
            run_create_post(self.account_manager, self.engine)
        self._pause()
        return True

    def handle_connected_accounts(self) -> bool:
        """Handle Connected Accounts option."""
        if self.account_manager is None:
            print_not_implemented("Account management (AccountManager not initialized)")
            self._pause()
            return True
        run_account_menu(self.account_manager, self.auth_manager)
        return True

    def handle_publishing_queue(self) -> bool:
        """Handle Publishing Queue option."""
        if self.engine is None:
            print_not_implemented("Publishing queue")
        else:
            run_publishing_queue(self.engine)
        self._pause()
        return True

    def handle_history(self) -> bool:
        """Handle History option."""
        if self.intake is None:
            print_not_implemented("Publishing history")
        else:
            run_history(self.intake)
        self._pause()
        return True

    def handle_content_inbox(self) -> bool:
        """Handle Content Inbox option: scan content/incoming and publish with the saved profile."""
        if self.intake is None:
            print_not_implemented("Content Inbox")
        else:
            run_content_inbox(self.intake)
        self._pause()
        return True

    def handle_settings(self) -> bool:
        """Handle Settings option (publishing profile)."""
        if self.intake is None or self.account_manager is None:
            print_not_implemented("Settings")
        else:
            run_settings(self.intake, self.account_manager)
        self._pause()
        return True

    def handle_exit(self) -> bool:
        """Handle Exit option."""
        print_header("Goodbye!")
        return False

    def _pause(self) -> None:
        """Pause and wait for user to press Enter."""
        try:
            input("\nPress Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            print()

    def run(self) -> None:
        """Run the main menu loop."""
        while self.running:
            self.show_main_menu()
            # "Exit" is already the last option; don't let the prompt add a second, dead one.
            choice = prompt_menu_selection(self.menu_options, "Select an option", allow_exit=False)
            if choice is None:
                # User pressed Ctrl+C or EOF
                self.running = False
                break
            self.running = self.handle_choice(choice)


def run_menu(account_manager=None, auth_manager=None, engine=None, intake=None) -> None:
    """Entry point for running the menu system."""
    handler = MenuHandler(account_manager=account_manager, auth_manager=auth_manager, engine=engine, intake=intake)
    handler.run()