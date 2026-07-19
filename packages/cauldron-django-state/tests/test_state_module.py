"""Tests for the cauldron.django.state module entry point and manifest."""
import pytest
from importlib.metadata import entry_points


def test_module_is_discoverable():
    """The cauldron.django.state entry point is installed and loadable."""
    eps = entry_points(group="cauldron.modules")
    names = [ep.name for ep in eps]
    assert "cauldron.django.state" in names


def test_entry_point_name_equals_slug():
    """Entry point name must exactly match the module slug."""
    from cauldron_django_state.module import module

    eps = entry_points(group="cauldron.modules")
    state_eps = [ep for ep in eps if ep.name == "cauldron.django.state"]
    assert state_eps, "Entry point 'cauldron.django.state' not found"
    loaded = state_eps[0].load()
    assert loaded is module
    assert module.slug == "cauldron.django.state"


def test_module_slug():
    from cauldron_django_state.module import module
    assert module.slug == "cauldron.django.state"


def test_module_label():
    from cauldron_django_state.module import module
    assert module.label == "Cauldron Django State"


def test_module_provides_capabilities():
    from cauldron_django_state.module import module
    provides = set(module.manifest.provides)
    assert "django.state" in provides
    assert "django.database" in provides
    assert "django.transactions" in provides
    assert "django.migrations" in provides


def test_module_django_apps():
    from cauldron_django_state.module import module
    assert "cauldron_django_state" in module.manifest.django_apps


def test_module_discovered_by_cauldron():
    """discover_modules finds cauldron.django.state."""
    from cauldron.modules.discovery import discover_modules
    result = discover_modules()
    slugs = [m.slug for m in result.modules]
    assert "cauldron.django.state" in slugs


def test_capabilities_registered_in_resolution():
    """Resolution maps django.state capability to cauldron.django.state."""
    from cauldron.modules.discovery import discover_modules
    from cauldron.modules.resolver import resolve

    result = discover_modules()
    active = [m for m in result.modules if m.slug == "cauldron.django.state"]
    cap_map = {}
    for m in active:
        for cap in m.manifest.provides:
            cap_map.setdefault(cap, []).append(m.slug)

    resolution = resolve(active, cap_map)
    assert not resolution.has_errors
    assert "cauldron.django.state" in resolution.load_order
