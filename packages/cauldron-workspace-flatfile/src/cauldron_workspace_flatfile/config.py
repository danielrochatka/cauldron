"""Workspace configuration container."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceConfig:
    workspace_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_root", Path(self.workspace_root).resolve())

    @property
    def change_sets_dir(self) -> Path:
        return self.workspace_root / "change-sets"

    @property
    def previews_dir(self) -> Path:
        return self.workspace_root / "previews"

    @property
    def snapshots_dir(self) -> Path:
        return self.workspace_root / "snapshots"

    @property
    def temp_dir(self) -> Path:
        return self.workspace_root / "temporary"

    @property
    def locks_dir(self) -> Path:
        return self.workspace_root / "locks"
