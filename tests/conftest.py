import pytest


@pytest.fixture(autouse=True)
def _no_real_cloudflared(monkeypatch, tmp_path):
    """Tests never find (or start) a real cloudflared; tunnel tests opt in with their own fake."""
    monkeypatch.setenv("CLOUDFLARED_PATH", str(tmp_path / "no-cloudflared.exe"))
