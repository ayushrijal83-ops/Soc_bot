"""Moves content packages between lifecycle directories, strictly inside the content root."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from src.content.detector import is_safe_name
from src.content.models import STAGES


class ContentPathError(Exception):
    """A path outside the content root, or an unsafe package name."""


class ContentManager:
    """incoming/ -> publishing/ -> published/ | failed/ (| archive/). Never deletes a package."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def ensure_dirs(self) -> None:
        for stage in STAGES:
            (self.root / stage).mkdir(parents=True, exist_ok=True)

    def stage_dir(self, stage: str) -> Path:
        if stage not in STAGES:
            raise ContentPathError(f"Unknown content stage: {stage}")
        return self.root / stage

    def inside_root(self, path: Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def locate(self, name: str) -> tuple[str, Path] | None:
        """Find a package by name in any stage (most advanced stage first)."""
        self._check_name(name)
        for stage in ("publishing", "failed", "incoming", "published", "archive"):
            candidate = self.root / stage / name
            if candidate.is_dir():
                return stage, candidate
        return None

    def move(self, package_dir: Path, stage: str) -> Path:
        """Move a package folder to ``stage``. If the target exists, a timestamp suffix is added."""
        package_dir = Path(package_dir)
        self._check_name(package_dir.name)
        if not self.inside_root(package_dir) or package_dir.is_symlink() or not package_dir.is_dir():
            raise ContentPathError(f"{package_dir.name} is not a package inside the content root")
        target_dir = self.stage_dir(stage)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / package_dir.name
        if target.resolve() == package_dir.resolve():
            return package_dir
        if target.exists():
            target = target_dir / f"{package_dir.name}__{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        shutil.move(str(package_dir), str(target))
        return target

    @staticmethod
    def _check_name(name: str) -> None:
        if not is_safe_name(name):
            raise ContentPathError(f"Unsafe package name: {name!r}")
