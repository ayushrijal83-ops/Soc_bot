"""Process-level publishing lock: at most one publisher at a time, across Soc_bot processes/windows.

An exclusive, NON-BLOCKING OS lock on ``<project>/data/publishing.lock`` (Windows: ``msvcrt.locking`` with
``LK_NBLCK``; elsewhere: ``fcntl.flock`` with ``LOCK_EX | LOCK_NB``). Busy means ``PublishLockBusy`` at once:
no waiting, polling, retrying, stealing or stale-file cleanup. The OS drops the lock when the process dies,
so a crashed process never blocks the next one. The file itself carries no data (no PID, user or token);
its existence means nothing.

Policy inside one process: the lock belongs to the THREAD that acquired it and is re-entrant for that
thread (only depth 0 -> 1 takes the OS lock and only 1 -> 0 releases it). Another thread of the same
process gets ``PublishLockBusy`` too, so e.g. a scheduler thread and a manual publish in one window can't
run at the same time. Releasing from a thread that doesn't own the lock, or releasing more often than
acquiring, raises ``RuntimeError`` and never unlocks anything.

This is a publishing concurrency boundary only; scheduling state stays protected by the database's
conditional updates.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = PROJECT_ROOT / "data" / "publishing.lock"   # the only production location (never user-supplied)
BUSY_MESSAGE = "Another Soc_bot window is publishing. Please try again later."  # for service/CLI callers


class PublishLockBusy(Exception):
    """Another process (or another thread of this process) is publishing right now."""


class _State:
    """Shared per lock file within this process: one OS handle, owner thread and depth."""

    def __init__(self) -> None:
        self.mutex = threading.Lock()   # guards owner/depth/fd only; never held while publishing
        self.owner: int | None = None
        self.depth = 0
        self.fd: int | None = None


_STATES: dict[Path, _State] = {}
_STATES_MUTEX = threading.Lock()


def _state_for(path: Path) -> _State:
    with _STATES_MUTEX:
        return _STATES.setdefault(path, _State())


def _os_lock(fd: int) -> bool:
    """Exclusive non-blocking lock on byte 0 of ``fd``. False if someone else holds it."""
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # raises OSError at once if locked (no retry loop)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _os_unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


class PublishLock:
    """Handle on the process-wide lock for ``path``. Use ``with lock:`` or acquire()/release()."""

    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()
        self._state = _state_for(self.path)

    def acquire(self) -> None:
        state, me = self._state, threading.get_ident()
        with state.mutex:
            if state.owner == me:
                state.depth += 1
                return
            if state.owner is not None:
                raise PublishLockBusy(f"{self.path} is held by another thread of this process")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            if not _os_lock(fd):
                os.close(fd)
                raise PublishLockBusy(f"{self.path} is held by another process")
            state.fd, state.owner, state.depth = fd, me, 1

    def release(self) -> None:
        state = self._state
        with state.mutex:
            if state.owner != threading.get_ident() or state.depth == 0:
                raise RuntimeError("publish lock released by a thread that does not hold it")
            state.depth -= 1
            if state.depth:
                return
            fd, state.fd, state.owner = state.fd, None, None
            try:
                _os_unlock(fd)
            finally:
                os.close(fd)

    @property
    def held(self) -> bool:
        """True if the calling thread holds the lock."""
        return self._state.owner == threading.get_ident()

    def __enter__(self) -> PublishLock:  # noqa: PYI034 - typing.Self needs Python 3.11; project runs 3.10
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()  # always; the original exception (if any) propagates


def default_lock() -> PublishLock:
    """The production lock (``<project>/data/publishing.lock``), independent of the working directory."""
    return PublishLock(LOCK_PATH)
