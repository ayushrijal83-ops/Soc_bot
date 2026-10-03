# ruff: noqa: F811 - fixtures imported from other modules
"""V2.3 release hardening: durable scheduler log lines (K1) and the Ctrl+C exit status of --run-due (K3)."""

import logging
import sys
from datetime import timedelta
from unittest.mock import patch

import main
from src.services.due_scheduler import MISSED_GRACE, DueScheduler
from tests.unit.test_due_scheduler import S, calls, make, status  # noqa: F401
from tests.unit.test_publishing_engine import env  # noqa: F401
from tests.unit.test_tui import services  # noqa: F401


def scheduler_lines(caplog):
    return [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "soc_bot.scheduler"]


def test_pass_logs_start_release_publish_missed_and_summary(services, calls, caplog):
    due, overdue = make(services, S), make(services, S - MISSED_GRACE - timedelta(minutes=5))
    with caplog.at_level(logging.INFO, logger="soc_bot.scheduler"):
        DueScheduler(services.publishing).run_due(S)
    text = "\n".join(message for _, message in scheduler_lines(caplog))
    assert "Scheduler pass started" in text
    assert f"post #{due} (scheduled 2026-10-02 12:00 UTC) released" in text
    assert f"post #{due} (scheduled 2026-10-02 12:00 UTC) published" in text
    missed = f"Scheduled post #{overdue} (scheduled 2026-10-02 10:55 UTC) missed its window; not published automatically."
    assert ("WARNING", missed) in scheduler_lines(caplog)
    assert "Scheduler pass finished: released 1, published 1, failed 0, missed 1, skipped 0, errors 0." in text


def test_publishing_error_is_logged_redacted(services, monkeypatch, caplog):
    post_id = make(services)

    def boom(*_a, **_k):
        raise RuntimeError("provider said access_token=SECRET123 and Bearer abcdefghijklmnop")

    monkeypatch.setattr(services.publishing, "publish_batch", boom)
    with caplog.at_level(logging.INFO, logger="soc_bot.scheduler"):
        DueScheduler(services.publishing).run_due(S)
    errors = [m for level, m in scheduler_lines(caplog) if level == "ERROR"]
    assert len(errors) == 1 and f"post #{post_id}" in errors[0] and "RuntimeError" in errors[0]
    assert "SECRET123" not in caplog.text and "abcdefghijklmnop" not in caplog.text
    assert "[REDACTED]" in errors[0]


def test_failed_batch_is_logged_as_warning(services, monkeypatch, caplog):
    post_id = make(services)
    monkeypatch.setattr(services.publishing, "publish_batch", lambda *_a, **_k: type("V", (), {"status": "failed"})())
    with caplog.at_level(logging.INFO, logger="soc_bot.scheduler"):
        DueScheduler(services.publishing).run_due(S)
    assert ("WARNING", f"Scheduled post #{post_id} (scheduled 2026-10-02 12:00 UTC) finished with problems (failed).") \
        in scheduler_lines(caplog)


def test_busy_pass_is_logged(services, calls, caplog):
    make(services)
    scheduler = DueScheduler(services.publishing)
    with patch.object(scheduler.lock, "acquire", side_effect=main_busy()), \
            caplog.at_level(logging.INFO, logger="soc_bot.scheduler"):
        assert scheduler.run_due(S).busy
    assert any("Scheduler pass skipped" in m for _, m in scheduler_lines(caplog))


def main_busy():
    from src.core.publish_lock import PublishLockBusy
    return PublishLockBusy("busy")


# --- Ctrl+C -------------------------------------------------------------------------------------

def test_ctrl_c_during_pass_releases_lock_and_keeps_post_released(services, monkeypatch, caplog):
    post_id = make(services)
    scheduler = DueScheduler(services.publishing)

    def interrupted(*_a, **_k):
        assert scheduler.lock.held
        raise KeyboardInterrupt

    monkeypatch.setattr(services.publishing, "publish_batch", interrupted)
    with caplog.at_level(logging.INFO, logger="soc_bot.scheduler"):
        try:
            scheduler.run_due(S)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("KeyboardInterrupt must propagate")
    assert not scheduler.lock.held
    assert status(services, post_id) == "released"           # never put back, never double-released
    assert {j.status for j in services.engine.store.jobs_for_post(post_id)} == {"pending"}  # resumable
    assert any("interrupted" in m for _, m in scheduler_lines(caplog))


def test_run_due_ctrl_c_exits_130():
    with patch.object(sys, "argv", ["main.py", "--run-due"]), \
            patch("main.initialize_services", return_value=(None, None, None, None)), \
            patch("main.run_due", side_effect=KeyboardInterrupt):
        assert main.main() == main.RUN_DUE_INTERRUPTED == 130


def test_ctrl_c_in_menu_still_exits_0():
    with patch.object(sys, "argv", ["main.py", "--plain"]), \
            patch("main.initialize_services", return_value=(None, None, None, None)), \
            patch("main.run_menu", side_effect=KeyboardInterrupt):
        assert main.main() == 0


def test_run_due_unexpected_error_still_exits_1():
    with patch.object(sys, "argv", ["main.py", "--run-due"]), \
            patch("main.initialize_services", return_value=(None, None, None, None)), \
            patch("main.run_due", side_effect=RuntimeError("x")):
        assert main.main() == 1


def test_help_documents_exit_130(capsys):
    with patch.object(sys, "argv", ["main.py", "--help"]):
        try:
            main.parse_args()
        except SystemExit:
            pass
    assert "130 = interrupted with Ctrl+C" in capsys.readouterr().out


# --- --run-due file log ---------------------------------------------------------------------------

def test_run_due_file_logging_keeps_console_and_adds_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")
    logger = logging.getLogger("soc_bot")
    saved = (list(logger.handlers), logger.level, logger.propagate)
    console = logging.StreamHandler(sys.stdout)
    logger.handlers[:] = [console]
    try:
        path = main.configure_file_logging(replace=False)
        main.configure_file_logging(replace=False)  # twice: still one file handler
        files = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert console in logger.handlers and len(files) == 1
        logging.getLogger("soc_bot.scheduler").info("hello file")
        files[0].flush()
        assert path == tmp_path / "logs" / "soc_bot.log" and "hello file" in path.read_text(encoding="utf-8")
    finally:
        for h in logger.handlers:
            if h is not console:
                h.close()
        logger.handlers[:], logger.level, logger.propagate = saved
