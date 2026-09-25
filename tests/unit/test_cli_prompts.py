"""Tests for CLI prompts."""

import pytest
from unittest.mock import patch
from src.cli.prompts import (
    prompt_text,
    prompt_int,
    prompt_choice,
    prompt_yes_no,
    prompt_menu_selection,
)


class TestPrompts:
    """Tests for prompt functions."""

    def test_prompt_text_required(self):
        """Test prompt_text with required input."""
        with patch('builtins.input', side_effect=['', 'valid input']):
            result = prompt_text("Enter text", required=True)
        assert result == "valid input"

    def test_prompt_text_not_required(self):
        """Test prompt_text with optional input."""
        with patch('builtins.input', return_value=''):
            result = prompt_text("Enter text", required=False)
        assert result is None

    def test_prompt_text_with_default(self):
        """Test prompt_text with default value."""
        with patch('builtins.input', return_value=''):
            result = prompt_text("Enter text", default="default_value")
        assert result == "default_value"

    def test_prompt_text_eof(self):
        """Test prompt_text handles EOF."""
        with patch('builtins.input', side_effect=EOFError):
            result = prompt_text("Enter text", required=False)
        assert result is None

    def test_prompt_int_valid(self):
        """Test prompt_int with valid input."""
        with patch('builtins.input', return_value='42'):
            result = prompt_int("Enter number")
        assert result == 42

    def test_prompt_int_with_min_max(self):
        """Test prompt_int with min/max validation."""
        with patch('builtins.input', side_effect=['0', '150', '50']):
            result = prompt_int("Enter number", min_val=1, max_val=100)
        assert result == 50

    def test_prompt_int_invalid_then_valid(self):
        """Test prompt_int rejects non-numeric then accepts valid."""
        with patch('builtins.input', side_effect=['abc', '10']):
            result = prompt_int("Enter number")
        assert result == 10

    def test_prompt_int_with_default(self):
        """Test prompt_int with default value."""
        with patch('builtins.input', return_value=''):
            result = prompt_int("Enter number", default=7)
        assert result == 7

    def test_prompt_int_eof(self):
        """Test prompt_int handles EOF."""
        with patch('builtins.input', side_effect=EOFError):
            result = prompt_int("Enter number", required=False)
        assert result is None

    def test_prompt_choice_valid(self):
        """Test prompt_choice with valid selection."""
        with patch('builtins.input', return_value='2'):
            result = prompt_choice("Choose", ["A", "B", "C"])
        assert result == 2

    def test_prompt_choice_invalid_then_valid(self):
        """Test prompt_choice rejects out of range then accepts valid."""
        with patch('builtins.input', side_effect=['5', '1']):
            result = prompt_choice("Choose", ["A", "B"])
        assert result == 1

    def test_prompt_choice_with_default(self):
        """Test prompt_choice with default."""
        with patch('builtins.input', return_value=''):
            result = prompt_choice("Choose", ["A", "B"], default=2)
        assert result == 2

    def test_prompt_yes_no_yes(self):
        """Test prompt_yes_no with yes."""
        with patch('builtins.input', return_value='y'):
            result = prompt_yes_no("Confirm?")
        assert result is True

    def test_prompt_yes_no_no(self):
        """Test prompt_yes_no with no."""
        with patch('builtins.input', return_value='n'):
            result = prompt_yes_no("Confirm?")
        assert result is False

    def test_prompt_yes_no_default_yes(self):
        """Test prompt_yes_no with default yes."""
        with patch('builtins.input', return_value=''):
            result = prompt_yes_no("Confirm?", default=True)
        assert result is True

    def test_prompt_yes_no_default_no(self):
        """Test prompt_yes_no with default no."""
        with patch('builtins.input', return_value=''):
            result = prompt_yes_no("Confirm?", default=False)
        assert result is False

    def test_prompt_menu_selection_valid(self):
        """Test prompt_menu_selection with valid input."""
        with patch('builtins.input', return_value='1'):
            result = prompt_menu_selection(["Option 1", "Option 2"])
        assert result == 1

    def test_prompt_menu_selection_exit_option(self):
        """Test prompt_menu_selection with exit option."""
        with patch('builtins.input', return_value='3'):
            result = prompt_menu_selection(["Option 1", "Option 2"], allow_exit=True)
        assert result == 3

    def test_prompt_menu_selection_no_exit(self):
        """Test prompt_menu_selection without exit option."""
        with patch('builtins.input', side_effect=['3', '2']):
            result = prompt_menu_selection(["Option 1", "Option 2"], allow_exit=False)
        assert result == 2

    def test_prompt_menu_selection_invalid_then_valid(self):
        """Test prompt_menu_selection rejects invalid then accepts."""
        with patch('builtins.input', side_effect=['0', 'abc', '1']):
            result = prompt_menu_selection(["Option 1", "Option 2"])
        assert result == 1

    def test_prompt_menu_selection_eof(self):
        """Test prompt_menu_selection handles EOF."""
        with patch('builtins.input', side_effect=EOFError):
            result = prompt_menu_selection(["Option 1"])
        assert result is None

    def test_prompt_menu_selection_keyboard_interrupt(self):
        """Test prompt_menu_selection handles Ctrl+C."""
        with patch('builtins.input', side_effect=KeyboardInterrupt):
            result = prompt_menu_selection(["Option 1"])
        assert result is None