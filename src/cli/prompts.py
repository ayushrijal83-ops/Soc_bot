"""Interactive prompts with input validation."""


from src.cli.display import confirm, print_error


def prompt_text(prompt: str, required: bool = True, default: str | None = None) -> str | None:
    """Prompt for text input with validation."""
    while True:
        try:
            suffix = f" (default: {default})" if default else ""
            response = input(f"{prompt}{suffix}: ").strip()
            if not response:
                if default is not None:
                    return default
                if not required:
                    return None
                print_error("This field is required.")
                continue
            return response
        except (EOFError, KeyboardInterrupt):
            print()
            return None


def prompt_int(prompt: str, min_val: int | None = None, max_val: int | None = None,
               default: int | None = None, required: bool = True) -> int | None:
    """Prompt for integer input with validation."""
    while True:
        try:
            suffix = f" (default: {default})" if default is not None else ""
            response = input(f"{prompt}{suffix}: ").strip()
            if not response:
                if default is not None:
                    return default
                if not required:
                    return None
                print_error("This field is required.")
                continue
            value = int(response)
            if min_val is not None and value < min_val:
                print_error(f"Value must be at least {min_val}.")
                continue
            if max_val is not None and value > max_val:
                print_error(f"Value must be at most {max_val}.")
                continue
            return value
        except ValueError:
            print_error("Please enter a valid number.")
        except (EOFError, KeyboardInterrupt):
            print()
            return None


def prompt_choice(prompt: str, choices: list[str], default: int | None = None) -> int | None:
    """Prompt for a menu choice from a list of options."""
    while True:
        try:
            for i, choice in enumerate(choices, 1):
                print(f"  {i}. {choice}")
            suffix = f" (default: {default})" if default else ""
            response = input(f"{prompt}{suffix}: ").strip()
            if not response:
                if default is not None:
                    return default
                print_error("Please select an option.")
                continue
            choice = int(response)
            if 1 <= choice <= len(choices):
                return choice
            print_error(f"Please enter a number between 1 and {len(choices)}.")
        except ValueError:
            print_error("Please enter a valid number.")
        except (EOFError, KeyboardInterrupt):
            print()
            return None


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    """Prompt for yes/no confirmation."""
    return confirm(prompt, default)


def prompt_menu_selection(options: list[str], prompt: str = "Select an option",
                          allow_exit: bool = True) -> int | None:
    """Prompt for menu selection with optional exit."""
    if allow_exit:
        print(f"  {len(options) + 1}. Exit")
    while True:
        try:
            response = input(f"{prompt}: ").strip()
            if not response:
                print_error("Please enter a number.")
                continue
            choice = int(response)
            max_choice = len(options) + (1 if allow_exit else 0)
            if 1 <= choice <= max_choice:
                return choice
            print_error(f"Please enter a number between 1 and {max_choice}.")
        except ValueError:
            print_error("Please enter a valid number.")
        except (EOFError, KeyboardInterrupt):
            print()
            return None