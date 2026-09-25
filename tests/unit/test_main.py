"""Tests for main entry point."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from main import load_environment, main, parse_args

# Set ENCRYPTION_KEY for tests that need it
os.environ["ENCRYPTION_KEY"] = "nZEJx1hxthoUa6wzoWYOVg0rNAsmhidhd9uASEPii5s="


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Never touch the developer's data/publisher.db or real .env from tests."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'main.db'}")
    monkeypatch.setattr("main.ENV_FILE", tmp_path / "no-such.env")
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path / "content"))


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
        with patch.object(sys, 'argv', ['main.py', '--help']), pytest.raises(SystemExit):
            parse_args()

    def test_parse_args_version(self):
        """Test parse_args with --version."""
        with patch.object(sys, 'argv', ['main.py', '--version']), pytest.raises(SystemExit):
            parse_args()


class TestMain:
    """Tests for main function."""

    def test_main_normal(self):
        """Test main runs menu normally."""
        with patch('main.run_menu') as mock_run, patch.object(sys, 'argv', ['main.py']):
            result = main()
        assert result == 0
        mock_run.assert_called_once()

    def test_main_dry_run(self, capsys, tmp_path, monkeypatch):
        """--dry-run with nothing queued reports that and contacts no platform."""
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'dry.db'}")
        with patch.object(sys, 'argv', ['main.py', '--dry-run']),                 patch('httpx.Client.request', side_effect=AssertionError("network")):
            result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "DRY-RUN MODE" in captured.out
        assert "never contacts social platforms" in captured.out
        assert "No unpublished posts" in captured.out

    def test_main_dry_run_shows_plan_for_pending_post(self, capsys, tmp_path, monkeypatch):
        """--dry-run validates saved posts and prints the plan; jobs stay pending, no HTTP."""
        from src.accounts.manager import AccountManager
        from src.core.jobs import JobStore
        from src.storage.database import Database
        from src.storage.tokens import TokenEncryption

        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'dry.db'}")
        db = Database(f"sqlite:///{tmp_path / 'dry.db'}", encryption=TokenEncryption())
        db.init()
        account = AccountManager(db, db.encryption).create_account(
            platform="tiktok", platform_account_id="o1", username="tt", access_token="SECRET_TOKEN")
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x" * 10)
        post_id = JobStore(db).create_post(str(video), "cap", [(account.id, {"privacy_level": "SELF_ONLY"})])
        capsys.readouterr()

        with patch.object(sys, 'argv', ['main.py', '--dry-run']),                 patch('httpx.Client.request', side_effect=AssertionError("network")):
            assert main() == 0
        out = capsys.readouterr().out
        assert f"Post #{post_id}" in out and "tiktok" in out and "READY" in out
        assert "SECRET_TOKEN" not in out
        assert JobStore(db).jobs_for_post(post_id)[0].status == "pending"
        db.engine.dispose()

    def test_main_passes_services_to_menu(self):
        """Regression: main called run_menu(auth_manager=...) which run_menu did not accept."""
        with patch('main.run_menu', autospec=True) as mock_run, patch.object(sys, 'argv', ['main.py']):
            assert main() == 0
        kwargs = mock_run.call_args.kwargs
        assert kwargs["auth_manager"] is not None and kwargs["engine"] is not None

    def test_main_keyboard_interrupt(self):
        """Test main handles KeyboardInterrupt."""
        with patch('main.run_menu', side_effect=KeyboardInterrupt), patch.object(sys, 'argv', ['main.py']):
            result = main()
        assert result == 0

    def test_main_exception(self, capsys):
        """Test main handles unexpected exceptions."""
        with patch('main.run_menu', side_effect=RuntimeError("Test error")), patch.object(sys, 'argv', ['main.py']):
            result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "Unexpected error" in captured.out

ENV_VARS = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REDIRECT_URI",
            "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET")


@pytest.fixture
def clean_env(monkeypatch):
    """Remove platform variables for the test and restore them afterwards."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def write_env(path, **values):
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


class TestLoadEnvironment:
    """.env loading (fake values only; the real .env is never read by tests)."""

    def test_loads_values_from_env_file(self, tmp_path, clean_env):
        env = write_env(tmp_path / ".env", YOUTUBE_CLIENT_ID="fake-id.apps.googleusercontent.com",
                        YOUTUBE_CLIENT_SECRET="fake-secret")
        assert load_environment(env) is True
        assert os.environ["YOUTUBE_CLIENT_ID"] == "fake-id.apps.googleusercontent.com"
        assert os.environ["YOUTUBE_CLIENT_SECRET"] == "fake-secret"

    def test_real_environment_takes_priority(self, tmp_path, clean_env):
        clean_env.setenv("YOUTUBE_CLIENT_ID", "from-real-environment")
        env = write_env(tmp_path / ".env", YOUTUBE_CLIENT_ID="from-dotenv", YOUTUBE_CLIENT_SECRET="s")
        load_environment(env)
        assert os.environ["YOUTUBE_CLIENT_ID"] == "from-real-environment"
        assert os.environ["YOUTUBE_CLIENT_SECRET"] == "s"

    def test_missing_file_is_not_an_error(self, tmp_path, clean_env):
        assert load_environment(tmp_path / "absent.env") is False
        assert "YOUTUBE_CLIENT_ID" not in os.environ

    def test_default_path_is_project_root_env(self, monkeypatch):
        import main as main_module

        monkeypatch.undo()  # drop the autouse redirect to inspect the real default (nothing is loaded)

        assert main_module.ENV_FILE.name == ".env"
        assert main_module.ENV_FILE.parent == Path(main_module.__file__).resolve().parent

    def test_youtube_configured_from_env_file(self, tmp_path, clean_env):
        """Credentials in .env reach create_auth_manager; only YouTube is configured."""
        from src.auth.manager import create_auth_manager

        load_environment(write_env(tmp_path / ".env", YOUTUBE_CLIENT_ID="fake-id", YOUTUBE_CLIENT_SECRET="fake-secret"))
        auth = create_auth_manager(None, None, None)
        assert auth.is_configured("youtube")
        assert auth.get_configured_platforms() == ["youtube"]
        assert auth.callback_port == 0 or "OAUTH_CALLBACK_PORT" in os.environ

    def test_main_loads_env_before_services_start(self, tmp_path, clean_env, monkeypatch, capsys):
        env = write_env(tmp_path / ".env", YOUTUBE_CLIENT_ID="fake-id", YOUTUBE_CLIENT_SECRET="fake-secret-value")
        monkeypatch.setattr("main.ENV_FILE", env)
        seen = {}

        def fake_services():
            seen["id"] = os.environ.get("YOUTUBE_CLIENT_ID")
            raise KeyboardInterrupt

        with patch("main.initialize_services", side_effect=fake_services), patch.object(sys, "argv", ["main.py"]):
            assert main() == 0
        assert seen["id"] == "fake-id"
        out = capsys.readouterr()
        assert "fake-secret-value" not in out.out + out.err
