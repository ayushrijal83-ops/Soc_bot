"""`.env.example` must only advertise settings the application actually reads."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_every_env_example_setting_is_read_by_the_code():
    names = set(re.findall(r"^#? ?([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.MULTILINE))
    code = "\n".join(p.read_text(encoding="utf-8") for p in [ROOT / "main.py", *(ROOT / "src").rglob("*.py")])
    assert names, "no settings parsed from .env.example"
    unread = sorted(name for name in names if f'"{name}"' not in code and f"'{name}'" not in code)
    assert unread == [], f".env.example lists settings no code reads: {unread}"


def test_removed_dead_settings_stay_removed():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("LOG_LEVEL", "DRY_RUN", "MAX_CONCURRENT_UPLOADS", "MAX_VIDEO_SIZE_"):
        assert name not in text
