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
                raise ValueError(
                    f"Navigation section {section.key!r} is already registered."
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
            if item.key in self._items:
                raise ValueError(
                    f"Navigation item {item.key!r} is already registered."
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

        Each item in the result is an object supporting both attribute access
        (item.key, item.label, item.url, item.is_active) and dict-style access.
        """
        import types

        sections = self.get_sections()
        items = self.get_items_for_user(user, request)

        current_path = request.path if request is not None else ""

        section_items: dict[str, list] = {s.key: [] for s in sections}
        for item in items:
            url = self.resolve_url(item)
            badge = item.badge_count
            if item.badge_provider is not None:
                try:
                    badge = item.badge_provider()
                except Exception:
                    badge = 0
            is_active = bool(
                item.url_prefix and current_path.startswith(item.url_prefix)
            )
            # Use SimpleNamespace so tests can access .key, .label, etc. as attributes
            # and the sidebar template can use {{ item.url }}, {{ item.is_active }}
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
