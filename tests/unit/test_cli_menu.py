"""Tests for CLI menu system."""

from unittest.mock import MagicMock, patch

from src.cli.menu import MenuHandler, run_menu


class TestMenuHandler:
    """Tests for MenuHandler class."""

    def test_init(self):
        """Test MenuHandler initialization."""
        handler = MenuHandler()
        assert handler.running is True
        assert len(handler.menu_options) == 6
        assert handler.menu_options == [
            "Create Post",
            "Connected Accounts",
            "Publishing Queue",
            "History",
            "Settings",
            "Exit",
        ]

    def test_show_main_menu(self, capsys):
        """Test show_main_menu displays correctly."""
        handler = MenuHandler()
        handler.show_main_menu()
        captured = capsys.readouterr()
        assert "SOCIAL PUBLISHER v1" in captured.out
        assert "1. Create Post" in captured.out
        assert "6. Exit" in captured.out

    def test_handle_choice_create_post(self):
        """Test handle_choice for Create Post."""
        handler = MenuHandler()
        with patch('src.cli.menu.print_not_implemented') as mock_print, patch('src.cli.menu.MenuHandler._pause'):
            result = handler.handle_choice(1)
        assert result is True
        mock_print.assert_called_once_with("Create Post")

    def test_handle_choice_connected_accounts(self):
        """Test handle_choice for Connected Accounts."""
        handler = MenuHandler()
        with patch('src.cli.menu.print_not_implemented') as mock_print, patch('src.cli.menu.MenuHandler._pause'):
            result = handler.handle_choice(2)
        assert result is True
        # AccountManager not initialized, should show appropriate message
        mock_print.assert_called_once_with("Account management (AccountManager not initialized)")

    def test_handle_choice_publishing_queue(self):
        """Test handle_choice for Publishing Queue."""
        handler = MenuHandler()
        with patch('src.cli.menu.print_not_implemented') as mock_print, patch('src.cli.menu.MenuHandler._pause'):
            result = handler.handle_choice(3)
        assert result is True
        mock_print.assert_called_once_with("Publishing queue")

    def test_handle_choice_history(self):
        """Test handle_choice for History."""
        handler = MenuHandler()
        with patch('src.cli.menu.print_not_implemented') as mock_print, patch('src.cli.menu.MenuHandler._pause'):
            result = handler.handle_choice(4)
        assert result is True
        mock_print.assert_called_once_with("Publishing history")

    def test_handle_choice_settings(self):
        """Test handle_choice for Settings."""
        handler = MenuHandler()
        with patch('src.cli.menu.print_not_implemented') as mock_print, patch('src.cli.menu.MenuHandler._pause'):
            result = handler.handle_choice(5)
        assert result is True
        mock_print.assert_called_once_with("Settings")

    def test_handle_choice_exit(self):
        """Test handle_choice for Exit."""
        handler = MenuHandler()
        with patch('src.cli.menu.print_header') as mock_print:
            result = handler.handle_choice(6)
        assert result is False
        mock_print.assert_called_once_with("Goodbye!")

    def test_handle_choice_invalid(self):
        """Test handle_choice with invalid option."""
        handler = MenuHandler()
        result = handler.handle_choice(99)
        assert result is True  # Should continue

    def test_run_exits_on_exit_choice(self):
        """Test run loop exits on exit choice."""
        handler = MenuHandler()
        # Simulate user selecting Exit (6) then pressing Enter
        with patch('src.cli.menu.prompt_menu_selection', side_effect=[6]), patch('src.cli.menu.MenuHandler._pause'):
            handler.run()
        assert handler.running is False

    def test_run_continues_on_other_choices(self):
        """Test run loop continues on other choices."""
        handler = MenuHandler()
        # Simulate user selecting Create Post (1) then Exit (6)
        with patch('src.cli.menu.prompt_menu_selection', side_effect=[1, 6]), patch('src.cli.menu.MenuHandler._pause'):
            handler.run()
        assert handler.running is False

    def test_run_handles_none_choice(self):
        """Test run handles None from prompt (Ctrl+C)."""
        handler = MenuHandler()
        with patch('src.cli.menu.prompt_menu_selection', return_value=None):
            handler.run()
        assert handler.running is False


class TestRunMenu:
    """Tests for run_menu function."""

    def test_run_menu_creates_handler_and_runs(self):
        """Test run_menu creates handler and calls run."""
        with patch('src.cli.menu.MenuHandler') as mock_handler_class:
            mock_handler = MagicMock()
            mock_handler_class.return_value = mock_handler
            run_menu()
            mock_handler_class.assert_called_once()
            mock_handler.run.assert_called_once()