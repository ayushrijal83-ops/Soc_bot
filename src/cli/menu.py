"""Main menu system for the CLI."""

from collections.abc import Callable

from src.cli.display import (
    clear_screen,
    print_header,
    print_menu,
    print_not_implemented,
)
from src.cli.prompts import prompt_menu_selection


class MenuHandler:
    """Handles menu navigation and routing."""

    def __init__(self):
        self.running = True
        self.menu_options = [
            "Create Post",
            "Connected Accounts",
            "Publishing Queue",
            "History",
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
            5: self.handle_settings,
            6: self.handle_exit,
        }

        handler = handlers.get(choice)
        if handler:
            return handler()
        return True

    def handle_create_post(self) -> bool:
        """Handle Create Post option."""
        print_not_implemented("Create Post")
        self._pause()
        return True

    def handle_connected_accounts(self) -> bool:
        """Handle Connected Accounts option."""
        print_not_implemented("Account management")
        self._pause()
        return True

    def handle_publishing_queue(self) -> bool:
        """Handle Publishing Queue option."""
        print_not_implemented("Publishing queue")
        self._pause()
        return True

    def handle_history(self) -> bool:
        """Handle History option."""
        print_not_implemented("Publishing history")
        self._pause()
        return True

    def handle_settings(self) -> bool:
        """Handle Settings option."""
        print_not_implemented("Settings")
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
            choice = prompt_menu_selection(self.menu_options, "Select an option")
            if choice is None:
                # User pressed Ctrl+C or EOF
                self.running = False
                break
            self.running = self.handle_choice(choice)


def run_menu() -> None:
    """Entry point for running the menu system."""
    handler = MenuHandler()
    handler.run()