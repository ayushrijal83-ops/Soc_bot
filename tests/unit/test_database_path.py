"""A relative SQLite DATABASE_URL is anchored to the project folder, never the working directory (V2.3 Phase 4.1).

The real project database is never opened: every test points the project root at a temporary folder.
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core import publish_lock
from src.core.jobs import JobStore
from src.storage import database
from src.storage.database import Database, Post
from tests.unit.test_scheduler_callers import WRAPPER

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A temporary project root, and the process working directory set to a different folder."""
    root, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    monkeypatch.setattr(database, "PROJECT_ROOT", root)
    monkeypatch.chdir(elsewhere)
    return root, elsewhere


def opened(db):
    db.create_all()
    path = db.engine.url.database
    db.engine.dispose()
    return path


def test_project_root_is_the_one_used_by_the_publishing_lock():
    assert database.PROJECT_ROOT is publish_lock.PROJECT_ROOT
    assert publish_lock.PROJECT_ROOT == ROOT


def test_default_url_opens_the_project_database_not_the_working_directory(project, monkeypatch):
    root, elsewhere = project
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert opened(Database()) == str(root / "data" / "publisher.db")
    assert (root / "data" / "publisher.db").is_file()
    assert not (elsewhere / "data").exists()


def test_relative_url_from_env_is_project_anchored(project, monkeypatch):
    root, elsewhere = project
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/other.db")
    assert opened(Database()) == str(root / "data" / "other.db")
    assert not (elsewhere / "data").exists()


def test_relative_url_from_project_root_is_unchanged_in_effect(project, monkeypatch):
    root, _ = project
    monkeypatch.chdir(root)
    assert opened(Database("sqlite:///data/publisher.db")) == str(root / "data" / "publisher.db")


def test_absolute_paths_are_kept_exactly(project, tmp_path):
    native = tmp_path / "abs" / "native.db"
    posix = (tmp_path / "abs2" / "posix.db").as_posix()          # Windows: C:/...; POSIX: sqlite:////tmp/...
    assert opened(Database(f"sqlite:///{native}")) == str(native)
    assert opened(Database(f"sqlite:///{posix}")) == posix
    assert Path(posix).is_file() and native.is_file()


@pytest.mark.skipif(os.name != "nt", reason="drive-relative root paths exist on Windows only")
def test_windows_rooted_path_without_drive_is_not_treated_as_relative(project, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                  # same drive as tmp_path
    rooted = (tmp_path / "rooted.db").as_posix()[2:]              # "/Users/.../rooted.db"
    assert opened(Database(f"sqlite:///{rooted}")) == rooted
    assert (tmp_path / "rooted.db").is_file()


@pytest.mark.parametrize("url", ["sqlite:///:memory:", "sqlite://"])
def test_in_memory_stays_in_memory(project, url):
    db = Database(url)
    assert db.engine.url.database in (":memory:", None)
    assert db.health_check()
    assert not any(project[0].iterdir()) and not any(project[1].iterdir())


def test_sqlite_file_uri_is_unchanged(project):
    db = Database("sqlite:///file:shared?mode=memory&cache=shared&uri=true")
    assert db.engine.url.database == "file:shared"
    assert db.health_check()
    db.engine.dispose()


def test_non_sqlite_url_is_unchanged(project):
    url = "mysql+pymysql://user:secret@db.example:3306/soc_bot?charset=utf8mb4"
    db = Database(url)                                          # no connection is made
    assert db.engine.url.render_as_string(hide_password=False) == url
    assert not any(project[0].iterdir()) and not any(project[1].iterdir())


def test_foreign_keys_still_enabled_for_anchored_sqlite(project):
    db = Database("sqlite:///data/fk.db")
    with db.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    db.engine.dispose()


# --- The exact H1 scenario: `python main.py --run-due` started from a different working directory -----------

ANCHOR = "import src.storage.database as _d; _d.PROJECT_ROOT = Path(sys.argv[4])   # temporary project root\n"


def test_run_due_from_other_working_directory_uses_the_project_database(tmp_path):
    from src.accounts.manager import AccountManager
    from src.storage.tokens import TokenEncryption, generate_key

    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    relative_url = "sqlite:///data/publisher.db"
    key = generate_key()
    db = Database(f"sqlite:///{project / 'data' / 'publisher.db'}", encryption=TokenEncryption(key.encode()))
    db.create_all()
    db.migrate(verbose=False)
    account = AccountManager(db, TokenEncryption(key.encode())).create_account(
        platform="instagram", platform_account_id="1784", username="ig_user", access_token="TOKEN",
        expires_at=datetime.now(UTC) + timedelta(days=30))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v" * 2000)
    post_id = JobStore(db).create_post(str(video), "x", [(account.id, {})],
                                       scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
                                       schedule_status="scheduled", schedule_json={"history": []})

    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(WRAPPER.replace("import main\n", ANCHOR + "import main\n", 1), encoding="utf-8")
    environ = {k: v for k, v in os.environ.items() if not k.startswith(("SOC_BOT_", "DATABASE_URL", "CONTENT_ROOT"))}
    environ.update(DATABASE_URL=relative_url, ENCRYPTION_KEY=key, CONTENT_ROOT=str(tmp_path / "content"))
    out = subprocess.run([sys.executable, str(wrapper), str(ROOT), str(tmp_path / "publishing.lock"),
                          str(tmp_path / "none.env"), str(project)],
                         cwd=elsewhere, env=environ, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                         timeout=120, check=False)

    assert out.returncode == 0, out.stdout + out.stderr
    assert "Published: 1" in out.stdout, out.stdout
    with db.session() as session:
        assert session.get(Post, post_id).schedule_status == "released"
    assert {j.status for j in JobStore(db).jobs_for_post(post_id)} == {"published"}
    assert not (elsewhere / "data").exists()                     # no second, working-directory database
    assert list(elsewhere.iterdir()) == []
    db.engine.dispose()
