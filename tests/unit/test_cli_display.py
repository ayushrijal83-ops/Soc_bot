"""Tests for CLI display utilities."""

import io
import sys
from unittest.mock import patch
from src.cli.display import (
    print_header,
    print_menu,
    print_info,
    print_success,
    print_warning,
    print_error,
    print_not_implemented,
    print_table,
    clear_screen,
)


class TestDisplay:
    """Tests for display utilities."""

    def test_print_header(self, capsys):
        """Test print_header outputs correctly."""
        print_header("TEST TITLE", width=20)
        captured = capsys.readouterr()
        assert "====================" in captured.out
        assert "    TEST TITLE     " in captured.out

    def test_print_menu(self, capsys):
        """Test print_menu outputs correctly."""
        options = ["Option 1", "Option 2", "Option 3"]
        print_menu(options, "TEST MENU", width=30)
        captured = capsys.readouterr()
        assert "TEST MENU" in captured.out
        assert "1. Option 1" in captured.out
        assert "2. Option 2" in captured.out
        assert "3. Option 3" in captured.out

    def test_print_info(self, capsys):
        """Test print_info outputs correctly."""
        print_info("Test message")
        captured = capsys.readouterr()
        assert "[INFO] Test message" in captured.out

    def test_print_success(self, capsys):
        """Test print_success outputs correctly."""
        print_success("Success!")
        captured = capsys.readouterr()
        assert "[SUCCESS] Success!" in captured.out

    def test_print_warning(self, capsys):
        """Test print_warning outputs correctly."""
        print_warning("Warning!")
        captured = capsys.readouterr()
        assert "[WARNING] Warning!" in captured.out

    def test_print_error(self, capsys):
        """Test print_error outputs correctly."""
        print_error("Error!")
        captured = capsys.readouterr()
        assert "[ERROR] Error!" in captured.out

    def test_print_not_implemented(self, capsys):
        """Test print_not_implemented outputs correctly."""
        print_not_implemented("Test Feature")
        captured = capsys.readouterr()
        assert "Test Feature is not implemented yet" in captured.out

    def test_print_table(self, capsys):
        """Test print_table outputs correctly."""
        headers = ["Name", "Value"]
        rows = [["A", "1"], ["B", "2"]]
        print_table(headers, rows)
        captured = capsys.readouterr()
        assert "Name | Value" in captured.out
        assert "A    | 1" in captured.out
        assert "B    | 2" in captured.out

    def test_print_table_empty(self, capsys):
        """Test print_table with empty rows."""
        headers = ["Name", "Value"]
        rows = []
        print_table(headers, rows)
        captured = capsys.readouterr()
        assert "No data to display" in captured.out

    def test_clear_screen(self, capsys):
        """Test clear_screen doesn't crash."""
        with patch('os.system') as mock_system:
            clear_screen()
            mock_system.assert_called_once()