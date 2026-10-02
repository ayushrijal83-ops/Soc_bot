import pytest


@pytest.fixture(autouse=True)
def _no_real_cloudflared(monkeypatch, tmp_path):
    """Tests never find (or start) a real cloudflared; tunnel tests opt in with their own fake."""
    monkeypatch.setenv("CLOUDFLARED_PATH", str(tmp_path / "no-cloudflared.exe"))


@pytest.fixture(autouse=True)
def _private_publish_lock(monkeypatch, tmp_path):
    """Each test gets its own publishing lock file: never the project's data/publishing.lock, so a running
    Soc_bot window can't make tests busy (and tests never touch the real lock)."""
    from src.core import publish_lock

    monkeypatch.setattr(publish_lock, "LOCK_PATH", tmp_path / "publishing.lock")
