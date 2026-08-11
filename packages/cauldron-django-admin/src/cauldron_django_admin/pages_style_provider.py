"""PagesStyleProvider implementation backed by UIOverrideStore.

Registered by CauldronDjangoAdminConfig.ready() into
cauldron_content.pages_style when cauldron-content is installed.  Isolated
here so the import from cauldron_content only occurs inside the registration
path, not at module-load time for the whole django-admin package.
"""
from __future__ import annotations


def _get_store():
    from pathlib import Path
    from django.conf import settings
    from cauldron_django_admin.override_store import UIOverrideStore
    override_dir = getattr(settings, "CAULDRON_UI_OVERRIDES_DIR", None)
    if override_dir is None:
        base_dir = getattr(settings, "BASE_DIR", None)
        if base_dir is None:
            raise RuntimeError(
                "CAULDRON_UI_OVERRIDES_DIR and BASE_DIR are not configured."
            )
        override_dir = Path(base_dir) / "cauldron-overrides"
    return UIOverrideStore(Path(override_dir))


class UIOverrideStorePagesProvider:
    """Composes the effective public-site CSS from the pages override store.

    Lexical sort of the target file names determines the composition order so
    that CSS specificity is predictable (e.g. ``00-variables.css`` before
    ``90-site.css``).
    """

    def get_composed_css(
        self,
        *,
        proposed_target: str | None = None,
        proposed_content: str | None = None,
    ) -> str:
        try:
            store = _get_store()
            targets = store.list_files("pages")
        except Exception:
            targets = []

        # Merge proposed target into the list (add if new, replace if existing).
        if proposed_target:
            if proposed_target not in targets:
                targets = sorted(targets + [proposed_target])
            else:
                targets = list(targets)  # already sorted from list_files

        parts: list[str] = []
        for t in targets:
            if proposed_target and t == proposed_target:
                if proposed_content is not None:
                    parts.append(proposed_content)
                continue
            try:
                store = _get_store()
                parts.append(store.read_file("pages", t))
            except Exception:
                pass

        return "\n".join(p for p in parts if p)

    def commit_style(
        self,
        *,
        target: str,
        content: str,
        expected_hash: str,
        base_exists: bool,
    ) -> str:
        from cauldron_django_admin.override_store import ABSENT
        store = _get_store()
        expected = expected_hash if base_exists else ABSENT
        return store.write_file_atomic("pages", target, content, expected_hash=expected)

    def list_targets(self) -> list[str]:
        try:
            return _get_store().list_files("pages")
        except Exception:
            return []
