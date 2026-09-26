"""The ONE place for colors, icons and styles. Screens use these names, never raw colors."""

from textual.theme import Theme

VIOLET = "#a78bfa"
CYAN = "#22d3ee"
GREEN = "#4ade80"
AMBER = "#fbbf24"
RED = "#f87171"
BLUE = "#60a5fa"
MUTED = "#8b8fa7"

PLATFORM_COLORS = {"instagram": "#e879f9", "youtube": "#ff4d4d", "tiktok": "#2dd4bf"}
PLATFORM_NAMES = {"instagram": "Instagram", "youtube": "YouTube", "tiktok": "TikTok"}
PLATFORM_ICONS = {"instagram": "◎", "youtube": "▶", "tiktok": "♪"}

# Status: icon + word + color, so meaning never depends on color alone.
STATUS = {
    "published": ("✓", "Published", GREEN),
    "failed": ("✗", "Failed", RED),
    "pending": ("○", "Pending", MUTED),
    "uploading": ("↑", "Uploading", CYAN),
    "processing": ("→", "Processing", BLUE),
    "retrying": ("↻", "Retrying", AMBER),
    "ready": ("●", "Ready", GREEN),
    "attention": ("⚠", "Needs attention", AMBER),
    "disconnected": ("○", "Disconnected", MUTED),
    "available": ("●", "Available", GREEN),
    "not_configured": ("○", "Not configured", MUTED),
    "completed": ("✓", "Completed", GREEN),
    "completed_with_failures": ("⚠", "Completed with failures", AMBER),
    "running": ("→", "Running", BLUE),
}


def status_markup(status: str, text: str | None = None) -> str:
    icon, word, color = STATUS.get(status, ("•", status.title(), MUTED))
    return f"[{color}]{icon} {text or word}[/]"


def platform_markup(platform: str) -> str:
    return f"[{PLATFORM_COLORS.get(platform, MUTED)}]{PLATFORM_ICONS.get(platform, '•')} {PLATFORM_NAMES.get(platform, platform)}[/]"


def mb(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


SOCBOT_THEME = Theme(
    name="socbot",
    primary=VIOLET,
    secondary=CYAN,
    accent="#f472b6",
    warning=AMBER,
    error=RED,
    success=GREEN,
    foreground="#e6e6f0",
    background="#0f1020",
    surface="#171830",
    panel="#20224a",
    dark=True,
)

CSS = """
Screen { background: $background; }
#body { height: 1fr; }
#sidebar {
    width: 26; background: $surface; border-right: tall $primary 30%; padding: 1 0;
}
#sidebar > OptionList { background: $surface; border: none; height: 1fr; }
#sidebar .brand { color: $primary; text-style: bold; padding: 0 2 1 2; }
#sidebar .hint { color: $text-muted; padding: 1 2 0 2; }
.narrow #sidebar { display: none; }
#content { padding: 1 2; height: 1fr; }
.title { text-style: bold; color: $primary; margin-bottom: 1; }
.subtitle { color: $text-muted; margin-bottom: 1; }
.section { text-style: bold; color: $secondary; margin: 1 0 0 0; }
.muted { color: $text-muted; }
.row { height: auto; }
.cards { height: auto; margin-bottom: 1; }
.card {
    border: round $primary 40%; background: $surface; padding: 0 1; height: auto; width: 1fr; margin-right: 1;
}
.card.selected { border: heavy $accent; background: $panel; }
.card:focus { border: heavy $secondary; }
.card .card-title { text-style: bold; }
.stat { border: round $secondary 40%; background: $surface; width: 1fr; height: 5; margin-right: 1; content-align: center middle; }
.panel { border: round $primary 50%; background: $surface; padding: 0 1; height: auto; margin-bottom: 1; }
.panel-title { text-style: bold; color: $primary; }
.buttons { height: auto; margin-top: 1; }
.buttons Button { margin-right: 1; }
DataTable { height: 1fr; min-height: 5; }
.fill { height: 1fr; }
Input { margin-bottom: 1; }
#steps { height: 1; margin-bottom: 1; }
.error-text { color: $error; }
.ok-text { color: $success; }
ModalScreen { align: center middle; background: $background 70%; }
.modal { width: 70; max-width: 95%; height: auto; max-height: 90%; border: thick $primary; background: $surface; padding: 1 2; }
.modal-title { text-style: bold; color: $primary; margin-bottom: 1; }
.batchcard { border: round $primary 40%; background: $surface; padding: 0 1; height: auto; margin-bottom: 1; }
.batchcard:focus { border: heavy $secondary; background: $panel; }
ProgressBar { width: 1fr; }
ProgressBar Bar { width: 1fr; }
#round { height: auto; }
TabbedContent { height: 1fr; }
#switcher { height: 1fr; }
#switcher > * { height: 1fr; }
#actions { dock: bottom; height: auto; padding-top: 1; background: $background; }
#actions Button { margin-right: 1; }
#publish { text-style: bold; min-width: 18; }
#publish:focus { text-style: bold reverse; }
.nav-active { color: $accent; }
TabPane { height: 1fr; padding: 0; }
.row Input { width: 1fr; }
.row Button { width: auto; min-width: 12; margin-left: 1; }
.stat { text-align: center; }
"""
