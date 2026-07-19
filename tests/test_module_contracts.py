"""Tests for ModuleManifest, ModuleRequirement, BaseModule, and CauldronModule protocol."""

import pytest

from cauldron.modules import (
    BaseModule,
    CauldronModule,
    ModuleManifest,
    ModuleRequirement,
)


class TestModuleRequirement:
    def test_defaults(self):
        req = ModuleRequirement(slug="some.module")
        assert req.slug == "some.module"
        assert req.version == ""
        assert req.kind == "module"

    def test_capability_kind(self):
        req = ModuleRequirement(slug="some.capability", kind="capability")
        assert req.kind == "capability"

    def test_version_constraint(self):
        req = ModuleRequirement(slug="some.module", version=">=1.0.0,<2.0.0")
        assert req.version == ">=1.0.0,<2.0.0"

    def test_frozen(self):
        req = ModuleRequirement(slug="some.module")
        with pytest.raises(Exception):
            req.slug = "other"  # type: ignore[misc]


class TestModuleManifest:
    def test_minimal(self):
        m = ModuleManifest(slug="test.module", label="Test Module")
        assert m.slug == "test.module"
        assert m.label == "Test Module"
        assert m.version == "0.0.0"
        assert m.cauldron_version == ""
        assert m.django_apps == ()
        assert m.settings == {}
        assert m.requires == ()
        assert m.optional == ()
        assert m.provides == ()

    def test_full(self):
        req = ModuleRequirement(slug="dep.module")
        opt = ModuleRequirement(slug="opt.capability", kind="capability")
        m = ModuleManifest(
            slug="test.module",
            label="Test",
            version="2.1.0",
            cauldron_version=">=0.1.0,<1.0.0",
            django_apps=("myapp",),
            settings={"KEY": "value"},
            requires=(req,),
            optional=(opt,),
            provides=("some.capability",),
        )
        assert m.version == "2.1.0"
        assert m.cauldron_version == ">=0.1.0,<1.0.0"
        assert m.django_apps == ("myapp",)
        assert m.settings == {"KEY": "value"}
        assert m.requires == (req,)
        assert m.optional == (opt,)
        assert m.provides == ("some.capability",)

    def test_frozen(self):
        m = ModuleManifest(slug="test.module", label="Test")
        with pytest.raises(Exception):
            m.slug = "other"  # type: ignore[misc]


class TestBaseModule:
    def _make(self, **kwargs) -> BaseModule:
        manifest = ModuleManifest(slug="test.module", label="Test Module", **kwargs)
        return BaseModule(manifest)

    def test_slug_and_label_from_manifest(self):
        mod = self._make()
        assert mod.slug == "test.module"
        assert mod.label == "Test Module"

    def test_manifest_accessible(self):
        mod = self._make(version="1.2.3")
        assert mod.manifest.version == "1.2.3"

    def test_django_apps_delegates_to_manifest(self):
        mod = self._make(django_apps=("myapp", "otherapp"))
        assert list(mod.django_apps()) == ["myapp", "otherapp"]

    def test_django_apps_empty_by_default(self):
        mod = self._make()
        assert list(mod.django_apps()) == []

    def test_on_ready_is_callable(self):
        mod = self._make()
        mod.on_ready()  # must not raise

    def test_satisfies_protocol(self):
        mod = self._make()
        assert isinstance(mod, CauldronModule)
