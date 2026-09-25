"""Account management submenu for the CLI."""

from collections.abc import Callable

from src.accounts.manager import (
    AccountError,
    AccountManager,
    AccountNotFoundError,
    DuplicateAccountError,
    InvalidStatusError,
)
from src.cli.display import (
    clear_screen,
    confirm,
    print_error,
    print_header,
    print_info,
    print_menu,
    print_success,
    print_table,
    print_warning,
)
from src.cli.prompts import (
    prompt_choice,
    prompt_int,
    prompt_menu_selection,
    prompt_text,
)


class AccountMenuHandler:
    """Handles account management submenu navigation and actions."""

    def __init__(self, account_manager: AccountManager):
        self.account_manager = account_manager
        self.running = True
        self.menu_options = [
            "List Accounts",
            "Account Details",
            "Create Development Account",
            "Update Account",
            "Disconnect Account",
            "Enable Account",
            "Back to Main Menu",
        ]

    def show_menu(self) -> None:
        """Display the account management menu."""
        clear_screen()
        print_menu(self.menu_options, "CONNECTED ACCOUNTS")

    def handle_choice(self, choice: int) -> bool:
        """
        Handle a menu choice.

        Returns:
            True if should continue, False if should go back.
        """
        handlers: dict[int, Callable[[], bool]] = {
            1: self.handle_list_accounts,
            2: self.handle_account_details,
            3: self.handle_create_development_account,
            4: self.handle_update_account,
            5: self.handle_disconnect_account,
            6: self.handle_enable_account,
            7: self.handle_back,
        }

        handler = handlers.get(choice)
        if handler:
            return handler()
        return True

    def handle_list_accounts(self) -> bool:
        """Handle List Accounts option."""
        clear_screen()
        print_header("LIST ACCOUNTS")

        # Ask for filter
        print("Filter options (press Enter to skip):")
        platform = prompt_text("Platform (instagram/tiktok/youtube)", required=False)
        status = prompt_text("Status (active/expired/revoked/disconnected)", required=False)

        try:
            accounts = self.account_manager.list_accounts(
                platform=platform if platform else None,
                status=status if status else None,
            )
        except AccountError as e:
            print_error(f"Error listing accounts: {e}")
            self._pause()
            return True

        if not accounts:
            print_info("No accounts found.")
            self._pause()
            return True

        # Display as table
        headers = ["ID", "Platform", "Username", "Display Name", "Status", "Expires At"]
        rows = []
        for acc in accounts:
            expires = acc.expires_at.strftime("%Y-%m-%d %H:%M") if acc.expires_at else "N/A"
            display_name = acc.display_name or "N/A"
            rows.append([
                str(acc.id),
                acc.platform.title(),
                acc.username,
                display_name,
                acc.status.title(),
                expires,
            ])

        print_table(headers, rows)
        print_info(f"Total: {len(accounts)} account(s)")
        self._pause()
        return True

    def handle_account_details(self) -> bool:
        """Handle Account Details option."""
        clear_screen()
        print_header("ACCOUNT DETAILS")

        account_id = prompt_int("Account ID", min_val=1)
        if account_id is None:
            return True

        try:
            account = self.account_manager.get_account(account_id)
            info = self.account_manager.get_account_display_info(account)

            print_info(f"ID: {info['id']}")
            print_info(f"Platform: {info['platform'].title()}")
            print_info(f"Platform Account ID: {info['platform_account_id']}")
            print_info(f"Username: {info['username']}")
            print_info(f"Display Name: {info['display_name'] or 'N/A'}")
            print_info(f"Status: {info['status'].title()}")
            print_info(f"Token Expires: {info['expires_at'] or 'N/A'}")
            print_info(f"Created: {info['created_at'] or 'N/A'}")
            print_info(f"Updated: {info['updated_at'] or 'N/A'}")

            self._pause()
        except AccountNotFoundError:
            print_error(f"Account with ID {account_id} not found")
            self._pause()
        except AccountError as e:
            print_error(f"Error: {e}")
            self._pause()
        return True

    def handle_create_development_account(self) -> bool:
        """Handle Create Development Account option."""
        clear_screen()
        print_header("CREATE DEVELOPMENT ACCOUNT")
        print_warning("This creates a DEVELOPMENT/TEST account with placeholder tokens.")
        print_warning("It does NOT perform real OAuth with any platform.")

        if not confirm("Continue?", default=False):
            return True

        platform = prompt_choice("Platform", ["Instagram", "TikTok", "YouTube"])
        if platform is None:
            return True
        platform_map = {1: "instagram", 2: "tiktok", 3: "youtube"}
        platform_str = platform_map[platform]

        platform_account_id = prompt_text("Platform Account ID (e.g., 123456789)", required=True)
        if platform_account_id is None:
            return True

        username = prompt_text("Username (e.g., @myaccount)", required=True)
        if username is None:
            return True

        display_name = prompt_text("Display Name (optional)", required=False)

        try:
            account = self.account_manager.create_development_account(
                platform=platform_str,
                platform_account_id=platform_account_id,
                username=username,
                display_name=display_name,
            )
            print_success("Development account created successfully!")
            print_info(f"ID: {account.id}")
            print_info(f"Platform: {account.platform.title()}")
            print_info(f"Username: {account.username}")
            print_info(f"Status: {account.status}")
            self._pause()
        except DuplicateAccountError:
            print_error("An account with this platform and platform_account_id already exists")
            self._pause()
        except AccountError as e:
            print_error(f"Error creating account: {e}")
            self._pause()
        return True

    def handle_update_account(self) -> bool:
        """Handle Update Account option."""
        clear_screen()
        print_header("UPDATE ACCOUNT")

        account_id = prompt_int("Account ID", min_val=1)
        if account_id is None:
            return True

        try:
            account = self.account_manager.get_account(account_id)
            info = self.account_manager.get_account_display_info(account)

            print_info(f"Current values for account {account_id}:")
            print_info(f"  Username: {info['username']}")
            print_info(f"  Display Name: {info['display_name'] or 'N/A'}")
            print_info(f"  Status: {info['status'].title()}")
            print()

            print("Enter new values (press Enter to keep current):")
            new_username = prompt_text("Username", required=False)
            new_display = prompt_text("Display Name", required=False)

            print("Status options: active, expired, revoked, disconnected")
            new_status = prompt_text("Status", required=False)

            updates = {}
            if new_username:
                updates["username"] = new_username
            if new_display:
                updates["display_name"] = new_display
            if new_status:
                updates["status"] = new_status

            if not updates:
                print_info("No changes specified")
                self._pause()
                return True

            if not confirm("Apply these changes?", default=True):
                return True

            self.account_manager.update_account(account_id, **updates)
            print_success("Account updated successfully!")
            self._pause()
        except AccountNotFoundError:
            print_error(f"Account with ID {account_id} not found")
            self._pause()
        except (AccountError, InvalidStatusError) as e:
            print_error(f"Error: {e}")
            self._pause()
        return True

    def handle_disconnect_account(self) -> bool:
        """Handle Disconnect Account option."""
        clear_screen()
        print_header("DISCONNECT ACCOUNT")
        print_warning("This will set the account status to 'disconnected'.")
        print_info("Historical publishing data will be preserved.")

        account_id = prompt_int("Account ID", min_val=1)
        if account_id is None:
            return True

        try:
            account = self.account_manager.get_account(account_id)
            info = self.account_manager.get_account_display_info(account)

            print_info(f"Account: {info['platform'].title()} - @{info['username']}")
            print_info(f"Current Status: {info['status'].title()}")

            if not confirm("Disconnect this account?", default=False):
                return True

            self.account_manager.disconnect_account(account_id)
            print_success("Account disconnected successfully!")
            self._pause()
        except AccountNotFoundError:
            print_error(f"Account with ID {account_id} not found")
            self._pause()
        except AccountError as e:
            print_error(f"Error: {e}")
            self._pause()
        return True

    def handle_enable_account(self) -> bool:
        """Handle Enable Account option."""
        clear_screen()
        print_header("ENABLE ACCOUNT")
        print_info("This will set the account status to 'active'.")

        account_id = prompt_int("Account ID", min_val=1)
        if account_id is None:
            return True

        try:
            account = self.account_manager.get_account(account_id)
            info = self.account_manager.get_account_display_info(account)

            print_info(f"Account: {info['platform'].title()} - @{info['username']}")
            print_info(f"Current Status: {info['status'].title()}")

            if not confirm("Enable this account?", default=True):
                return True

            self.account_manager.enable_account(account_id)
            print_success("Account enabled successfully!")
            self._pause()
        except AccountNotFoundError:
            print_error(f"Account with ID {account_id} not found")
            self._pause()
        except AccountError as e:
            print_error(f"Error: {e}")
            self._pause()
        return True

    def handle_back(self) -> bool:
        """Handle Back option."""
        return False

    def _pause(self) -> None:
        """Pause and wait for user to press Enter."""
        try:
            input("\nPress Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            print()

    def run(self) -> None:
        """Run the account management menu loop."""
        while self.running:
            self.show_menu()
            choice = prompt_menu_selection(self.menu_options, "Select an option")
            if choice is None:
                # User pressed Ctrl+C or EOF
                break
            self.running = self.handle_choice(choice)


def run_account_menu(account_manager: AccountManager) -> None:
    """Entry point for running the account management menu."""
    handler = AccountMenuHandler(account_manager)
    handler.run()