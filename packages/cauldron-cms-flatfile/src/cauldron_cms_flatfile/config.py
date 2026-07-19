"""Configuration for the flat-file CMS provider."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FlatFileCMSConfig:
    site_root: Path
    content_root: str = "content"
    schema_root: str = "schemas"

    def __post_init__(self) -> None:
        object.__setattr__(self, "site_root", Path(self.site_root).resolve())
        # Validate that content_root and schema_root are inside site_root
        self._resolve_path(self.content_root)
        self._resolve_path(self.schema_root)

    def _resolve_path(self, rel: str) -> Path:
        if Path(rel).is_absolute():
            raise ValueError(f"Path must be relative, got: {rel!r}")
        resolved = (self.site_root / rel).resolve()
        try:
            resolved.relative_to(self.site_root)
        except ValueError as exc:
            raise ValueError(
                f"Path {rel!r} escapes site_root {self.site_root}"
            ) from exc
        return resolved

    @property
    def content_dir(self) -> Path:
        return (self.site_root / self.content_root).resolve()

    @property
    def schema_dir(self) -> Path:
        return (self.site_root / self.schema_root).resolve()
