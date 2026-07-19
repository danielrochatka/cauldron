"""Tests for the cauldron.content module entry point and manifest."""
from importlib.metadata import entry_points


def test_module_is_discoverable():
    eps = entry_points(group="cauldron.modules")
    names = [ep.name for ep in eps]
    assert "cauldron.content" in names


def test_entry_point_name_equals_slug():
    from cauldron_content.module import module

    eps = entry_points(group="cauldron.modules")
    matching = [ep for ep in eps if ep.name == "cauldron.content"]
    assert matching
    loaded = matching[0].load()
    assert loaded is module
    assert module.slug == "cauldron.content"


def test_module_label():
    from cauldron_content.module import module
    assert module.label == "Cauldron Content"


def test_module_provides_capabilities():
    from cauldron_content.module import module
    provides = set(module.manifest.provides)
    assert "content.contracts" in provides
    assert "content.registry" in provides
    assert "content.routing" in provides
    assert "content.changesets" in provides
    assert "content.validation" in provides


def test_module_django_apps():
    from cauldron_content.module import module
    assert "cauldron_content" in module.manifest.django_apps


def test_module_discovered_by_cauldron():
    from cauldron.modules.discovery import discover_modules
    result = discover_modules()
    slugs = [m.slug for m in result.modules]
    assert "cauldron.content" in slugs
