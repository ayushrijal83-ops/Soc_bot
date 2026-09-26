"""Shared TUI building blocks: page layout with sidebar, modals, file picker."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DirectoryTree,
    Footer,
    Header,
    Label,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option


def run_classic(app, fn, *args) -> None:
    """Run a classic terminal flow while the TUI is suspended, on a plain thread (joined).

    The UI's event loop keeps running on the main thread; anything in the classic flow that starts its
    own loop (asyncio.run: OAuth, token refresh, revoke) must not run there.
    """
    import threading

    errors: list[BaseException] = []

    def target() -> None:
        try:
            fn(*args)
        except BaseException as e:  # noqa: BLE001 - re-raised on the UI thread below
            errors.append(e)

    with app.suspend():
        worker = threading.Thread(target=target, name="soc_bot-classic", daemon=True)
        worker.start()
        worker.join()
    if errors and not isinstance(errors[0], (KeyboardInterrupt, EOFError)):
        app.notify(f"Classic screen stopped: {type(errors[0]).__name__}", severity="error")


NAV = [
    ("dashboard", "D", "Dashboard"),
    ("create", "C", "Create Post"),
    ("queue", "Q", "Queue"),
    ("history", "H", "History"),
    ("accounts", "A", "Accounts"),
    ("links", "L", "Published Links"),
    ("content", "T", "Content"),
    ("settings", "S", "Settings"),
    ("exit", "X", "Exit"),
]


class Sidebar(Vertical):
    def __init__(self, current: str):
        super().__init__(id="sidebar")
        self.current = current

    def compose(self) -> ComposeResult:
        yield Static("◆ SOC_BOT", classes="brand")
        # The ACTIVE page is marked in its own text (▌ + accent), independent of the cursor, which the user
        # may move with arrows/mouse; the cursor is also put back on the active page whenever it's shown.
        options = [Option(f"[$accent]▌[/][b]{key}  {label}[/]" if mode == self.current else f" [b]{key}[/]  {label}",
                          id=mode) for mode, key, label in NAV]
        nav = OptionList(*options, id="nav")
        yield nav
        yield Static("? help · ctrl+p commands", classes="hint")

    def on_mount(self) -> None:
        self.reset_highlight()

    def reset_highlight(self) -> None:
        nav = self.query_one(OptionList)
        nav.highlighted = next(i for i, (mode, _, _) in enumerate(NAV) if mode == self.current)

    def on_descendant_blur(self) -> None:
        self.reset_highlight()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.app.navigate(event.option.id)


class Page(Screen):
    """Header + sidebar + content area + footer. Subclasses implement ``body()``."""

    MODE = ""
    TITLE_TEXT = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield Sidebar(self.MODE)
            with VerticalScroll(id="content") if self.SCROLL else Vertical(id="content"):
                yield from self.body()
        yield Footer()

    SCROLL = False

    def body(self) -> ComposeResult:  # pragma: no cover - overridden
        yield Static("")

    def on_screen_resume(self) -> None:
        self.query_one(Sidebar).reset_highlight()
        self.refresh_data()

    def refresh_data(self) -> None:
        """Reload data when the page becomes visible."""


class ConfirmModal(ModalScreen[bool]):
    BINDINGS: ClassVar[list] = [Binding("escape", "cancel", "Cancel"), Binding("y", "confirm", "Yes", show=False)]

    def __init__(self, title: str, message: str, confirm: str = "Confirm", danger: bool = False):
        super().__init__()
        self.title_text, self.message, self.confirm_label, self.danger = title, message, confirm, danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(self.title_text, classes="modal-title")
            yield Static(self.message)
            with Horizontal(classes="buttons"):
                yield Button(self.confirm_label, variant="error" if self.danger else "success", id="yes")
                yield Button("Cancel", id="no")

    def on_mount(self) -> None:
        self.query_one("#no" if self.danger else "#yes", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)


class MessageModal(ModalScreen[None]):
    """Friendly message with optional technical details (shown only on request)."""

    BINDINGS: ClassVar[list] = [Binding("escape", "close", "Close")]

    def __init__(self, title: str, message: str, details: str | None = None):
        super().__init__()
        self.title_text, self.message, self.details = title, message, details

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(self.title_text, classes="modal-title")
            yield Static(self.message)
            yield Static("", id="details", classes="muted")
            with Horizontal(classes="buttons"):
                if self.details:
                    yield Button("Technical details", id="show")
                yield Button("Close", variant="primary", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "show":
            self.query_one("#details", Static).update(f"\n[b]Technical details[/]\n{self.details}")
            event.button.disabled = True
        else:
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class HelpModal(ModalScreen[None]):
    BINDINGS: ClassVar[list] = [Binding("escape", "close", "Close"), Binding("question_mark", "close", "Close", show=False)]

    SHORTCUTS: ClassVar[list] = [
        ("D", "Dashboard"), ("C", "Create Post"), ("Q", "Queue"), ("H", "History"), ("A", "Accounts"),
        ("L", "Published Links"), ("T", "Content"), ("S", "Settings"), ("X", "Exit"),
        ("/", "Search (lists)"), ("Space", "Toggle selection"), ("Enter", "Open / confirm"),
        ("Esc", "Back / close"), ("Ctrl+P", "Command palette"), ("?", "This help"), ("Ctrl+Q", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        rows = "\n".join(f"  [b $secondary]{key:<7}[/] {label}" for key, label in self.SHORTCUTS)
        with Vertical(classes="modal"):
            yield Label("Keyboard shortcuts", classes="modal-title")
            yield Static(rows)
            yield Static("\nShortcut letters work when no text field has focus.", classes="muted")
            with Horizontal(classes="buttons"):
                yield Button("Close", variant="primary", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class MediaTree(DirectoryTree):
    """Directory tree that only lists folders and files with the wanted extensions."""

    def __init__(self, path: str, extensions: tuple[str, ...], **kwargs):
        super().__init__(path, **kwargs)
        self.extensions = extensions

    def filter_paths(self, paths):
        return [p for p in paths if not p.name.startswith(".") and (p.is_dir() or p.suffix.lower() in self.extensions)]


class FilePickerModal(ModalScreen[str | None]):
    BINDINGS: ClassVar[list] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, start: Path, extensions: tuple[str, ...]):
        super().__init__()
        self.title_text, self.start, self.extensions = title, start, extensions

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="picker"):
            yield Label(self.title_text, classes="modal-title")
            yield Static(f"[dim]{self.start}[/]  (files: {', '.join(self.extensions)})")
            yield MediaTree(str(self.start), self.extensions, id="tree")
            with Horizontal(classes="buttons"):
                yield Button("Up one folder", id="up")
                yield Button("Cancel", id="cancel")

    DEFAULT_CSS = "#picker { height: 90%; } #tree { height: 1fr; }"

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(str(event.path))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "up":
            self.start = self.start.parent
            self.query_one(MediaTree).path = str(self.start)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
