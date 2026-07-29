"""Public-site theme CSS service for cauldron-site-astro."""
from __future__ import annotations

import threading
from pathlib import Path


class SiteThemeService:
    """Manages the public site's active and staged theme CSS.

    CSS is persisted to two plain files under ``theme_dir``:
    - ``active.css``: the currently live stylesheet
    - ``staged.css``: a draft waiting to be promoted on next publish

    All file access is protected by a per-instance lock so concurrent
    prepare/publish calls in the same process cannot corrupt the files.
    """

    def __init__(self, theme_dir: str | Path) -> None:
        self._dir = Path(theme_dir)
        self._lock = threading.Lock()

    @property
    def _active(self) -> Path:
        return self._dir / "active.css"

    @property
    def _staged(self) -> Path:
        return self._dir / "staged.css"

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def get_active_css(self) -> str:
        """Return the current live CSS, or empty string if none."""
        with self._lock:
            if self._active.exists():
                return self._active.read_text(encoding="utf-8")
            return ""

    def stage_css(self, css_content: str) -> None:
        """Write ``css_content`` as the staged draft."""
        with self._lock:
            self._ensure_dir()
            self._staged.write_text(css_content, encoding="utf-8")

    def get_staged_css(self) -> str | None:
        """Return staged CSS, or None if nothing is staged."""
        with self._lock:
            if self._staged.exists():
                return self._staged.read_text(encoding="utf-8")
            return None

    def promote_staged(self) -> bool:
        """Move staged → active. Returns True if staged existed."""
        with self._lock:
            if not self._staged.exists():
                return False
            self._ensure_dir()
            self._staged.replace(self._active)
            return True

    def discard_staged(self) -> None:
        """Remove any staged draft without promoting it."""
        with self._lock:
            self._staged.unlink(missing_ok=True)
