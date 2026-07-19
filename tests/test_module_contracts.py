"""Tests for ModuleRequirement, ModuleManifest, BaseModule, and CauldronModule protocol."""

import json

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

    # -- validation --

    def test_empty_slug_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModuleRequirement(slug="")

    def test_invalid_slug_raises(self):
        with pytest.raises(ValueError, match="pattern"):
            ModuleRequirement(slug="Bad-Slug")

    def test_invalid_version_specifier_raises(self):
        with pytest.raises(ValueError, match="specifier"):
            ModuleRequirement(slug="a", version="not_a_specifier!")

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError, match="kind"):
            ModuleRequirement(slug="a", kind="unknown")  # type: ignore[arg-type]

    # -- serialization --

    def test_round_trip_module(self):
        req = ModuleRequirement(slug="cauldron.dep", version=">=1.0.0", kind="module")
        assert ModuleRequirement.from_dict(req.to_dict()) == req

    def test_round_trip_capability(self):
        req = ModuleRequirement(slug="some.cap", kind="capability")
        assert ModuleRequirement.from_dict(req.to_dict()) == req

    def test_to_dict_is_json_serializable(self):
        req = ModuleRequirement(slug="a", version=">=1.0.0")
        json.dumps(req.to_dict())  # must not raise


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
            settings={"key": "value"},
            requires=(req,),
            optional=(opt,),
            provides=("some.capability",),
        )
        assert m.version == "2.1.0"
        assert m.cauldron_version == ">=0.1.0,<1.0.0"
        assert m.django_apps == ("myapp",)
        assert m.settings == {"key": "value"}
        assert m.requires == (req,)
        assert m.optional == (opt,)
        assert m.provides == ("some.capability",)

    def test_frozen(self):
        m = ModuleManifest(slug="test.module", label="Test")
        with pytest.raises(Exception):
            m.slug = "other"  # type: ignore[misc]

    # -- validation --

    def test_empty_slug_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModuleManifest(slug="", label="Test")

    def test_invalid_slug_raises(self):
        with pytest.raises(ValueError, match="pattern"):
            ModuleManifest(slug="Bad_Slug", label="Test")

    def test_empty_label_raises(self):
        with pytest.raises(ValueError, match="label"):
            ModuleManifest(slug="valid.slug", label="")

    def test_invalid_version_raises(self):
        with pytest.raises(ValueError, match="version"):
            ModuleManifest(slug="a", label="A", version="not-a-version!")

    def test_invalid_cauldron_version_raises(self):
        with pytest.raises(ValueError, match="specifier"):
            ModuleManifest(slug="a", label="A", cauldron_version="bad!specifier")

    def test_invalid_provides_entry_raises(self):
        with pytest.raises(ValueError, match="pattern"):
            ModuleManifest(slug="a", label="A", provides=("Bad-Cap",))

    def test_empty_django_app_entry_raises(self):
        with pytest.raises(ValueError, match="non-empty strings"):
            ModuleManifest(slug="a", label="A", django_apps=("",))

    def test_non_string_django_app_entry_raises(self):
        with pytest.raises(ValueError, match="non-empty strings"):
            ModuleManifest(slug="a", label="A", django_apps=(123,))  # type: ignore[arg-type]

    def test_valid_dotted_slug(self):
        m = ModuleManifest(slug="cauldron.content.core", label="Content Core")
        assert m.slug == "cauldron.content.core"

    # -- serialization --

    def test_round_trip_minimal(self):
        m = ModuleManifest(slug="a", label="A")
        assert ModuleManifest.from_dict(m.to_dict()) == m

    def test_round_trip_full(self):
        req = ModuleRequirement(slug="dep.module", version=">=1.0.0")
        opt = ModuleRequirement(slug="opt.cap", kind="capability")
        m = ModuleManifest(
            slug="test.module",
            label="Test",
            version="1.2.3",
            cauldron_version=">=0.1.0",
            django_apps=("app1",),
            settings={"k": "v"},
            requires=(req,),
            optional=(opt,),
            provides=("my.cap",),
        )
        assert ModuleManifest.from_dict(m.to_dict()) == m

    def test_to_dict_is_json_serializable(self):
        m = ModuleManifest(
            slug="a",
            label="A",
            version="1.0.0",
            provides=("my.cap",),
        )
        json.dumps(m.to_dict())  # must not raise

    def test_from_dict_applies_defaults(self):
        m = ModuleManifest.from_dict({"slug": "a", "label": "A"})
        assert m.version == "0.0.0"
        assert m.cauldron_version == ""
        assert m.django_apps == ()
        assert m.requires == ()


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

    def test_register_is_callable(self):
        from cauldron.modules import ModuleContext

        mod = self._make()
        ctx = ModuleContext(slug="test.module", config={})
        mod.register(ctx)  # must not raise

    def test_satisfies_protocol(self):
        mod = self._make()
        assert isinstance(mod, CauldronModule)


class TestModuleContext:
    def test_frozen(self):
        from cauldron.modules import ModuleContext

        ctx = ModuleContext(slug="a", config={})
        with pytest.raises(Exception):
            ctx.slug = "b"  # type: ignore[misc]

    def test_slug_and_config_accessible(self):
        from cauldron.modules import ModuleContext

        ctx = ModuleContext(slug="my.module", config={"k": "v"})
        assert ctx.slug == "my.module"
        assert ctx.config == {"k": "v"}
