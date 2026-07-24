"""Thread-safe navigation registry for the Cauldron Admin Shell."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Callable

_KEY_RE = re.compile(r"^[a-zA-Z0-9._\-]{1,128}$")
_PERM_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")


def _validate_key(key: str, kind: str) -> None:
    if not _KEY_RE.match(key):
        raise ValueError(f"{kind} key {key!r} must match [a-zA-Z0-9._-]{{1,128}}.")


def _validate_label(label: str, kind: str) -> None:
    if not label or len(label) > 256:
        raise ValueError(f"{kind} label must be 1–256 characters.")


def _validate_permission(permission: str) -> None:
    if permission and not _PERM_RE.match(permission):
        raise ValueError(
            f"Permission {permission!r} must be 'app_label.codename' or empty string."
        )


def _validate_order(order: int, kind: str) -> None:
    if not isinstance(order, int):
        raise TypeError(f"{kind} order must be an int.")


@dataclass(frozen=True)
class AdminNavigationItem:
    key: str
    label: str
    url_name: str
    section: str
    order: int
    permission: str
    url_prefix: str = ""
    description: str = ""
    badge_count: int = 0
    badge_provider: Callable[[], int] | None = None
    # When True, ``is_active`` compares the current request path to
    # ``url_prefix`` exactly (rather than a startswith prefix match).  This
    # is needed for the Dashboard item which shares its prefix with every
    # nested cauldron page.
    url_prefix_exact: bool = False


@dataclass(frozen=True)
class AdminNavigationSection:
    key: str
    label: str
    order: int


@dataclass(frozen=True)
class AdminDashboardCard:
    key: str
    label: str
    description: str
    url: str
    section: str
    order: int


class NavigationRegistry:
    """Thread-safe singleton navigation registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, AdminNavigationItem] = {}
        self._sections: dict[str, AdminNavigationSection] = {}

    def register_section(self, section: AdminNavigationSection) -> None:
        _validate_key(section.key, "Section")
        _validate_label(section.label, "Section")
        _validate_order(section.order, "Section")
        with self._lock:
            existing = self._sections.get(section.key)
            if existing is not None:
                if existing == section:
                    # Exact re-registration is idempotent (e.g. Django
                    # autoreload re-executing AppConfig.ready()).
                    return
                raise ValueError(
                    f"Navigation section {section.key!r} is already registered "
                    "with different attributes."
                )
            self._sections[section.key] = section

    def register_item(self, item: AdminNavigationItem) -> None:
        _validate_key(item.key, "Item")
        _validate_label(item.label, "Item")
        _validate_permission(item.permission)
        _validate_order(item.order, "Item")
        if len(item.description) > 256:
            raise ValueError("Item description must be ≤256 characters.")
        if not isinstance(item.badge_count, int) or item.badge_count < 0:
            raise ValueError("badge_count must be a non-negative int.")
        if item.badge_provider is not None and not callable(item.badge_provider):
            raise ValueError("badge_provider must be callable or None.")
        with self._lock:
            if item.section not in self._sections:
                raise ValueError(
                    f"Navigation item {item.key!r} references unknown section {item.section!r}. "
                    "Register the section first."
                )
            existing = self._items.get(item.key)
            if existing is not None:
                if existing == item:
                    # Exact re-registration is idempotent.
                    return
                raise ValueError(
                    f"Navigation item {item.key!r} is already registered "
                    "with different attributes."
                )
            self._items[item.key] = item

    def get_items_for_user(self, user, request=None) -> list[AdminNavigationItem]:
        with self._lock:
            items = list(self._items.values())
        result = []
        for item in items:
            if item.permission:
                if user is None:
                    continue
                try:
                    if not user.has_perm(item.permission):
                        continue
                except Exception:
                    continue
            result.append(item)
        section_order_map = self._get_section_order_map()
        result.sort(key=lambda i: (section_order_map.get(i.section, 9999), i.order, i.label))
        return result

    def _get_section_order_map(self) -> dict[str, int]:
        with self._lock:
            return {key: sec.order for key, sec in self._sections.items()}

    def get_sections(self) -> list[AdminNavigationSection]:
        with self._lock:
            sections = list(self._sections.values())
        sections.sort(key=lambda s: (s.order, s.label))
        return sections

    def resolve_url(self, item: AdminNavigationItem) -> str:
        """Reverse item.url_name safely. Returns '#' on NoReverseMatch."""
        from django.urls import reverse, NoReverseMatch
        try:
            return reverse(item.url_name)
        except NoReverseMatch:
            return "#"

    def get_grouped_nav(self, user, request=None) -> list[dict]:
        """Return grouped navigation with resolved URLs and is_active flags.

        Exactly one item across the whole navigation is marked active for a
        given request. Selection priority:

        1. Item whose ``url_name`` matches the currently resolved URL name
           (e.g. ``"cauldron:modules"``). This is the most precise signal
           and wins over any prefix-based match.
        2. Item with ``url_prefix_exact=True`` whose ``url_prefix`` matches
           the request path exactly (optionally normalised with a trailing
           slash).
        3. Item with the longest matching ``url_prefix`` — the most specific
           prefix wins, so ``/cauldron/modules/foo/`` picks the modules
           entry, not the dashboard entry rooted at ``/cauldron/``.

        Each returned item is a ``SimpleNamespace`` so tests can access
        ``.key`` / ``.label`` and templates can use ``{{ item.url }}`` etc.
        """
        import types
        from django.urls import resolve, Resolver404

        sections = self.get_sections()
        items = self.get_items_for_user(user, request)

        current_path = request.path if request is not None else ""

        # Resolve the current URL name (e.g. "cauldron:modules") so we can
        # compare it against each item's declared ``url_name``.
        current_url_name = ""
        if current_path:
            try:
                match = resolve(current_path)
                current_url_name = ":".join(
                    p for p in (match.namespace, match.url_name) if p
                ) or (match.url_name or "")
            except Resolver404:
                current_url_name = ""
            except Exception:
                # Any other resolver failure (misconfiguration during tests,
                # etc.) is not fatal — fall through to prefix matching.
                current_url_name = ""

        # Bucket candidates by priority.
        exact_name_candidates: list[AdminNavigationItem] = []
        exact_path_candidates: list[AdminNavigationItem] = []
        prefix_candidates: list[tuple[int, AdminNavigationItem]] = []

        for item in items:
            if (
                item.url_name
                and current_url_name
                and item.url_name == current_url_name
            ):
                exact_name_candidates.append(item)
                continue
            if item.url_prefix_exact and item.url_prefix:
                normalised = current_path.rstrip("/") + "/"
                if (
                    current_path == item.url_prefix
                    or normalised == item.url_prefix
                ):
                    exact_path_candidates.append(item)
                continue
            if item.url_prefix and current_path.startswith(item.url_prefix):
                prefix_candidates.append((len(item.url_prefix), item))

        active_key: str | None = None
        if exact_name_candidates:
            active_key = exact_name_candidates[0].key
        elif exact_path_candidates:
            active_key = exact_path_candidates[0].key
        elif prefix_candidates:
            # Longest url_prefix wins; break ties deterministically by key.
            best = max(
                prefix_candidates,
                key=lambda pair: (pair[0], pair[1].key),
            )
            active_key = best[1].key

        section_items: dict[str, list] = {s.key: [] for s in sections}
        for item in items:
            url = self.resolve_url(item)
            badge = item.badge_count
            if item.badge_provider is not None:
                try:
                    badge = item.badge_provider()
                except Exception:
                    badge = 0
            is_active = (item.key == active_key)
            entry = types.SimpleNamespace(
                key=item.key,
                label=item.label,
                url=url,
                url_name=item.url_name,
                description=item.description,
                badge_count=badge,
                is_active=is_active,
                permission=item.permission,
                url_prefix=item.url_prefix,
                url_prefix_exact=item.url_prefix_exact,
            )
            if item.section in section_items:
                section_items[item.section].append(entry)
            else:
                section_items.setdefault(item.section, []).append(entry)

        result = []
        for section in sections:
            group_items = section_items.get(section.key, [])
            if group_items:
                result.append({"section": section, "items": group_items})
        return result

    def get_dashboard_cards(self, user, request=None) -> list[AdminDashboardCard]:
        """Return AdminDashboardCard list for the dashboard."""
        cards = []
        for item in self.get_items_for_user(user, request):
            url = self.resolve_url(item)
            cards.append(AdminDashboardCard(
                key=item.key,
                label=item.label,
                description=item.description,
                url=url,
                section=item.section,
                order=item.order,
            ))
        return cards

    def clear(self) -> None:
        """Clear all registrations. For use in tests only."""
        with self._lock:
            self._items.clear()
            self._sections.clear()


_registry = NavigationRegistry()


def get_navigation_registry() -> NavigationRegistry:
    return _registry
