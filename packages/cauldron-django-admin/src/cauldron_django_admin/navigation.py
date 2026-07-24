"""Thread-safe navigation registry for the Cauldron Admin Shell."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


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


@dataclass(frozen=True)
class AdminNavigationSection:
    key: str
    label: str
    order: int


class NavigationRegistry:
    """Thread-safe singleton navigation registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, AdminNavigationItem] = {}
        self._sections: dict[str, AdminNavigationSection] = {}

    def register_item(self, item: AdminNavigationItem) -> None:
        with self._lock:
            if item.key in self._items:
                raise ValueError(f"Navigation item with key {item.key!r} is already registered.")
            self._items[item.key] = item

    def register_section(self, section: AdminNavigationSection) -> None:
        with self._lock:
            if section.key in self._sections:
                raise ValueError(f"Navigation section with key {section.key!r} is already registered.")
            self._sections[section.key] = section

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

        # Sort deterministically by (section order, item order, label)
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

    def get_grouped_nav(self, user, request=None) -> list[dict]:
        sections = self.get_sections()
        items = self.get_items_for_user(user, request)

        # Group items by section key
        section_items: dict[str, list[AdminNavigationItem]] = {s.key: [] for s in sections}
        for item in items:
            if item.section in section_items:
                section_items[item.section].append(item)
            else:
                # Item references unknown section — include anyway
                section_items.setdefault(item.section, []).append(item)

        result = []
        for section in sections:
            group_items = section_items.get(section.key, [])
            if group_items:
                result.append({"section": section, "items": group_items})

        return result


_registry = NavigationRegistry()


def get_navigation_registry() -> NavigationRegistry:
    return _registry
