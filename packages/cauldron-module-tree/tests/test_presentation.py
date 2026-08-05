"""Tests for ModulePresentation dataclass and ModuleManifest presentation field."""
import pytest
from cauldron.modules import ModulePresentation, ModuleManifest


def test_default_values_are_safe():
    """All defaults are empty strings / 0."""
    p = ModulePresentation()
    assert p.title == ""
    assert p.summary == ""
    assert p.icon_svg == ""
    assert p.group == ""
    assert p.display_order == 0
    assert p.documentation_url == ""


def test_to_dict_round_trip():
    """Create with values, to_dict(), from_dict(), compare."""
    p = ModulePresentation(
        title="My Module",
        summary="Does something useful.",
        icon_svg="<svg></svg>",
        group="Core",
        display_order=5,
        documentation_url="https://example.com/docs",
    )
    d = p.to_dict()
    p2 = ModulePresentation.from_dict(d)
    assert p2.title == "My Module"
    assert p2.summary == "Does something useful."
    assert p2.icon_svg == "<svg></svg>"
    assert p2.group == "Core"
    assert p2.display_order == 5
    assert p2.documentation_url == "https://example.com/docs"


def test_display_order_must_be_non_negative():
    """Negative display_order raises ValueError."""
    with pytest.raises(ValueError, match="non-negative"):
        ModulePresentation(display_order=-1)


def test_display_order_zero_is_valid():
    """0 is valid for display_order."""
    p = ModulePresentation(display_order=0)
    assert p.display_order == 0


def test_empty_strings_are_valid():
    """All fields empty is fine."""
    p = ModulePresentation(
        title="",
        summary="",
        icon_svg="",
        group="",
        display_order=0,
        documentation_url="",
    )
    assert p.title == ""
    assert p.summary == ""


def test_manifest_accepts_presentation():
    """ModuleManifest with presentation field."""
    pres = ModulePresentation(title="Test Title", summary="A summary.", display_order=2)
    m = ModuleManifest(slug="test.mod", label="Test", presentation=pres)
    assert m.presentation.title == "Test Title"
    assert m.presentation.summary == "A summary."
    assert m.presentation.display_order == 2


def test_manifest_backward_compat_no_presentation():
    """Manifest without presentation uses default."""
    m = ModuleManifest(slug="test.mod", label="Test")
    assert isinstance(m.presentation, ModulePresentation)
    assert m.presentation.title == ""
    assert m.presentation.display_order == 0


def test_manifest_to_dict_includes_presentation():
    """to_dict() includes presentation key."""
    pres = ModulePresentation(title="Display Name", group="Extras")
    m = ModuleManifest(slug="my.mod", label="My Mod", presentation=pres)
    d = m.to_dict()
    assert "presentation" in d
    assert d["presentation"]["title"] == "Display Name"
    assert d["presentation"]["group"] == "Extras"


def test_manifest_from_dict_round_trip_with_presentation():
    """Full round trip including presentation."""
    pres = ModulePresentation(
        title="Round Trip",
        summary="Testing.",
        icon_svg="<svg><circle/></svg>",
        group="Testing",
        display_order=3,
        documentation_url="https://docs.example.com",
    )
    m = ModuleManifest(slug="rt.mod", label="RT Mod", presentation=pres)
    d = m.to_dict()
    m2 = ModuleManifest.from_dict(d)
    assert m2.presentation.title == "Round Trip"
    assert m2.presentation.summary == "Testing."
    assert m2.presentation.icon_svg == "<svg><circle/></svg>"
    assert m2.presentation.group == "Testing"
    assert m2.presentation.display_order == 3
    assert m2.presentation.documentation_url == "https://docs.example.com"


def test_manifest_from_dict_without_presentation_uses_default():
    """Missing presentation key uses default."""
    data = {"slug": "no.pres", "label": "No Pres"}
    m = ModuleManifest.from_dict(data)
    assert isinstance(m.presentation, ModulePresentation)
    assert m.presentation.title == ""
    assert m.presentation.display_order == 0
