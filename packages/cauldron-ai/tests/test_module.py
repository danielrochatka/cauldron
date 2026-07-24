"""Verify the cauldron-ai module manifest exposes the expected capabilities."""
from cauldron_ai.module import module


def test_module_provides_expected_capabilities():
    expected = {"ai.model.contracts", "ai.model.providers", "ai.toolcalling"}
    assert set(module.manifest.provides) == expected


def test_module_slug_and_apps():
    assert module.slug == "cauldron.ai"
    assert tuple(module.manifest.django_apps) == ()
