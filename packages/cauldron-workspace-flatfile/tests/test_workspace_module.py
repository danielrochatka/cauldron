"""Tests for the cauldron.workspace.flatfile module entry point and manifest."""
from importlib.metadata import entry_points


def test_module_is_discoverable():
    eps = entry_points(group="cauldron.modules")
    names = [ep.name for ep in eps]
    assert "cauldron.workspace.flatfile" in names


def test_entry_point_name_equals_slug():
    from cauldron_workspace_flatfile.module import module

    eps = entry_points(group="cauldron.modules")
    matching = [ep for ep in eps if ep.name == "cauldron.workspace.flatfile"]
    assert matching
    assert matching[0].load() is module


def test_module_requires_content():
    from cauldron_workspace_flatfile.module import module
    required = [r.slug for r in module.manifest.requires]
    assert "cauldron.content" in required


def test_module_provides_capabilities():
    from cauldron_workspace_flatfile.module import module
    provides = set(module.manifest.provides)
    assert "workspace.flatfile" in provides
    assert "workspace.changesets" in provides
    assert "workspace.snapshots" in provides
    assert "workspace.preview" in provides


def test_module_django_apps():
    from cauldron_workspace_flatfile.module import module
    assert "cauldron_workspace_flatfile" in module.manifest.django_apps
