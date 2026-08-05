"""Tests for ModulePresentation and its integration into ModuleManifest."""
import pytest

from cauldron.modules import BaseModule, ModuleManifest, ModulePresentation


class TestModulePresentation:
    def test_default_values_are_safe(self):
        p = ModulePresentation()
        assert p.title == ""
        assert p.summary == ""
        assert p.icon_svg == ""
        assert p.group == ""
        assert p.display_order == 0
        assert p.documentation_url == ""

    def test_all_fields_accepted(self):
        p = ModulePresentation(
            title="My Module",
            summary="Does things",
            icon_svg="<svg/>",
            group="content",
            display_order=10,
            documentation_url="https://example.com/docs",
        )
        assert p.title == "My Module"
        assert p.summary == "Does things"
        assert p.icon_svg == "<svg/>"
        assert p.group == "content"
        assert p.display_order == 10
        assert p.documentation_url == "https://example.com/docs"

    def test_display_order_zero_is_valid(self):
        p = ModulePresentation(display_order=0)
        assert p.display_order == 0

    def test_display_order_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            ModulePresentation(display_order=-1)

    def test_display_order_large_positive_is_valid(self):
        p = ModulePresentation(display_order=9999)
        assert p.display_order == 9999

    def test_frozen_immutable(self):
        p = ModulePresentation(title="A")
        with pytest.raises((AttributeError, TypeError)):
            p.title = "B"  # type: ignore[misc]

    def test_to_dict_keys(self):
        p = ModulePresentation(title="T", summary="S", group="g", display_order=5)
        d = p.to_dict()
        assert set(d.keys()) == {
            "title", "summary", "icon_svg", "group", "display_order", "documentation_url"
        }

    def test_to_dict_round_trip(self):
        p = ModulePresentation(
            title="X", summary="Y", icon_svg="<svg/>",
            group="test", display_order=3, documentation_url="https://x.com",
        )
        assert ModulePresentation.from_dict(p.to_dict()) == p

    def test_from_dict_empty_uses_defaults(self):
        p = ModulePresentation.from_dict({})
        assert p == ModulePresentation()

    def test_from_dict_partial_uses_defaults_for_missing(self):
        p = ModulePresentation.from_dict({"title": "Hello"})
        assert p.title == "Hello"
        assert p.summary == ""
        assert p.display_order == 0

    def test_equality_value_semantics(self):
        a = ModulePresentation(title="X")
        b = ModulePresentation(title="X")
        assert a == b

    def test_inequality(self):
        a = ModulePresentation(title="X")
        b = ModulePresentation(title="Y")
        assert a != b


class TestModuleManifestPresentation:
    def test_manifest_default_presentation(self):
        m = ModuleManifest(slug="a", label="A")
        assert isinstance(m.presentation, ModulePresentation)
        assert m.presentation.title == ""

    def test_manifest_accepts_presentation(self):
        p = ModulePresentation(title="Custom", group="test")
        m = ModuleManifest(slug="a", label="A", presentation=p)
        assert m.presentation.title == "Custom"
        assert m.presentation.group == "test"

    def test_manifest_rejects_non_presentation(self):
        with pytest.raises((ValueError, TypeError)):
            ModuleManifest(slug="a", label="A", presentation="not a presentation")  # type: ignore[arg-type]

    def test_manifest_to_dict_includes_presentation(self):
        p = ModulePresentation(title="T", summary="S")
        m = ModuleManifest(slug="a", label="A", presentation=p)
        d = m.to_dict()
        assert "presentation" in d
        assert d["presentation"]["title"] == "T"
        assert d["presentation"]["summary"] == "S"

    def test_manifest_from_dict_with_presentation(self):
        p = ModulePresentation(title="T", group="g", display_order=2)
        m = ModuleManifest(slug="a", label="A", presentation=p)
        m2 = ModuleManifest.from_dict(m.to_dict())
        assert m2.presentation == p

    def test_manifest_from_dict_without_presentation_uses_default(self):
        d = {"slug": "a", "label": "A"}
        m = ModuleManifest.from_dict(d)
        assert m.presentation == ModulePresentation()

    def test_manifest_presentation_survives_full_round_trip(self):
        p = ModulePresentation(
            title="Full Round Trip",
            summary="comprehensive test",
            icon_svg="<svg xmlns='http://www.w3.org/2000/svg'><circle r='5'/></svg>",
            group="core",
            display_order=42,
            documentation_url="https://docs.example.com/module",
        )
        m = ModuleManifest(slug="a.b", label="A B", presentation=p)
        m2 = ModuleManifest.from_dict(m.to_dict())
        assert m2.presentation == p

    def test_existing_manifests_remain_valid_without_presentation(self):
        """All existing manifest fields work without specifying presentation."""
        from cauldron.modules import ModuleRequirement
        m = ModuleManifest(
            slug="legacy.module",
            label="Legacy",
            version="2.0.0",
            requires=(ModuleRequirement(slug="other.module"),),
            provides=("some.cap",),
        )
        assert m.presentation == ModulePresentation()
        d = m.to_dict()
        assert d["presentation"] == ModulePresentation().to_dict()

    def test_base_module_manifest_has_presentation(self):
        p = ModulePresentation(title="Base", group="test")
        manifest = ModuleManifest(slug="a", label="A", presentation=p)
        mod = BaseModule(manifest)
        assert mod.manifest.presentation.title == "Base"
