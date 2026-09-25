"""Display utilities for CLI formatting."""



def clear_screen() -> None:
    """Clear the terminal screen."""
    import os
    os.system('cls' if os.name == 'nt' else 'clear')


def print_header(title: str, width: int = 50) -> None:
    """Print a centered header with border."""
    border = "=" * width
    centered = title.center(width)
    print(border)
    print(centered)
    print(border)


def print_menu(options: list[str], title: str = "MENU", width: int = 50) -> None:
    """Print a numbered menu with border."""
    print_header(title, width)
    for i, option in enumerate(options, 1):
        print(f"  {i}. {option}")
    print("=" * width)


def print_info(message: str) -> None:
    """Print an info message."""
    print(f"[INFO] {message}")


def print_success(message: str) -> None:
    """Print a success message."""
    print(f"[SUCCESS] {message}")


def print_warning(message: str) -> None:
    """Print a warning message."""
    print(f"[WARNING] {message}")


def print_error(message: str) -> None:
    """Print an error message."""
    print(f"[ERROR] {message}")


def print_not_implemented(feature: str) -> None:
    """Print a standard 'not implemented' message."""
    print_warning(f"{feature} is not implemented yet — coming in a later phase.")


def print_table(headers: list[str], rows: list[list[str]], widths: list[int] | None = None) -> None:
    """Print a formatted table."""
    if not rows:
        print_info("No data to display.")
        return

    if widths is None:
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], len(str(cell)))

    # Header
    header_row = " | ".join(h.ljust(w) for h, w in zip(headers, widths))
    separator = "-+-".join("-" * w for w in widths)
    print(header_row)
    print(separator)

    # Rows
    for row in rows:
        row_str = " | ".join(str(cell).ljust(w) for cell, w in zip(row, widths))
        print(row_str)


def confirm(prompt: str, default: bool = False) -> bool:
    """Ask for yes/no confirmation."""
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        try:
            response = input(f"{prompt}{suffix}: ").strip().lower()
            if not response:
                return default
            if response in ('y', 'yes'):
                return True
            if response in ('n', 'no'):
                return False
            print_error("Please enter 'y' or 'n'.")
        except (EOFError, KeyboardInterrupt):
            print()
            return False


def pause(message: str = "Press Enter to continue...") -> None:
    """Pause and wait for user input."""
    try:
        input(message)
    except (EOFError, KeyboardInterrupt):
        print()