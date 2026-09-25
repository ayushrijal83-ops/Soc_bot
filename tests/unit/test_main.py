"""Tests for main entry point."""

import sys
import pytest
from unittest.mock import patch, MagicMock
from main import parse_args, main


class TestParseArgs:
    """Tests for argument parsing."""

    def test_parse_args_default(self):
        """Test parse_args with no arguments."""
        with patch.object(sys, 'argv', ['main.py']):
            args = parse_args()
        assert args.dry_run is False

    def test_parse_args_dry_run(self):
        """Test parse_args with --dry-run."""
        with patch.object(sys, 'argv', ['main.py', '--dry-run']):
            args = parse_args()
        assert args.dry_run is True

    def test_parse_args_help(self):
        """Test parse_args with --help."""
        with patch.object(sys, 'argv', ['main.py', '--help']):
            with pytest.raises(SystemExit):
                parse_args()

    def test_parse_args_version(self):
        """Test parse_args with --version."""
        with patch.object(sys, 'argv', ['main.py', '--version']):
            with pytest.raises(SystemExit):
                parse_args()


class TestMain:
    """Tests for main function."""

    def test_main_normal(self):
        """Test main runs menu normally."""
        with patch('main.run_menu') as mock_run:
            with patch.object(sys, 'argv', ['main.py']):
                result = main()
        assert result == 0
        mock_run.assert_called_once()

    def test_main_dry_run(self, capsys):
        """Test main with --dry-run."""
        with patch.object(sys, 'argv', ['main.py', '--dry-run']):
            result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "DRY-RUN MODE" in captured.out
        assert "not implemented yet" in captured.out

    def test_main_keyboard_interrupt(self):
        """Test main handles KeyboardInterrupt."""
        with patch('main.run_menu', side_effect=KeyboardInterrupt):
            with patch.object(sys, 'argv', ['main.py']):
                result = main()
        assert result == 0

    def test_main_exception(self, capsys):
        """Test main handles unexpected exceptions."""
        with patch('main.run_menu', side_effect=RuntimeError("Test error")):
            with patch.object(sys, 'argv', ['main.py']):
                result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "Unexpected error" in captured.out