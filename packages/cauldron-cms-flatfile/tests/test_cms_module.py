"""Tests for the cauldron.cms.flatfile module manifest and discovery."""
from importlib.metadata import entry_points


def test_module_is_discoverable():
    eps = entry_points(group="cauldron.modules")
    names = [ep.name for ep in eps]
    assert "cauldron.cms.flatfile" in names


def test_entry_point_loads_module():
    from cauldron_cms_flatfile.module import module

    eps = entry_points(group="cauldron.modules")
    matching = [ep for ep in eps if ep.name == "cauldron.cms.flatfile"]
    assert matching
    assert matching[0].load() is module


def test_module_requires_content():
    from cauldron_cms_flatfile.module import module
    slugs = [r.slug for r in module.manifest.requires]
    assert "cauldron.content" in slugs


def test_module_optional_workspace():
    from cauldron_cms_flatfile.module import module
    slugs = [r.slug for r in module.manifest.optional]
    assert "cauldron.workspace.flatfile" in slugs


def test_module_provides():
    from cauldron_cms_flatfile.module import module
    provides = set(module.manifest.provides)
    assert "content.provider.flatfile" in provides
    assert "content.storage.flatfile" in provides
    assert "content.schemas.jsonschema" in provides
    assert "content.markdown" in provides
    assert "content.publishing.flatfile" in provides


def test_module_django_apps():
    from cauldron_cms_flatfile.module import module
    assert "cauldron_cms_flatfile" in module.manifest.django_apps
