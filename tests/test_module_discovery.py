"""Tests for entry-point discovery using independently packaged fixture modules."""

import pytest

from cauldron.modules.discovery import ENTRY_POINT_GROUP, discover_modules


@pytest.fixture(scope="module")
def discovered():
    return discover_modules()


@pytest.fixture(scope="module")
def discovered_by_slug(discovered):
    return {m.slug: m for m in discovered}


class TestEntryPointDiscovery:
    def test_discovers_fixture_alpha(self, discovered_by_slug):
        assert "cauldron.fixture.alpha" in discovered_by_slug

    def test_discovers_fixture_beta(self, discovered_by_slug):
        assert "cauldron.fixture.beta" in discovered_by_slug

    def test_all_discovered_satisfy_protocol(self, discovered):
        from cauldron.modules import CauldronModule

        for module in discovered:
            assert isinstance(module, CauldronModule), (
                f"Module {module!r} does not satisfy CauldronModule protocol"
            )

    def test_alpha_manifest_fields(self, discovered_by_slug):
        alpha = discovered_by_slug["cauldron.fixture.alpha"]
        assert alpha.manifest.slug == "cauldron.fixture.alpha"
        assert alpha.manifest.label == "Cauldron Fixture Alpha"
        assert alpha.manifest.version == "1.0.0"
        assert "test.capability.alpha" in alpha.manifest.provides

    def test_beta_manifest_fields(self, discovered_by_slug):
        beta = discovered_by_slug["cauldron.fixture.beta"]
        assert beta.manifest.slug == "cauldron.fixture.beta"
        assert beta.manifest.label == "Cauldron Fixture Beta"
        assert len(beta.manifest.requires) == 1
        assert beta.manifest.requires[0].slug == "cauldron.fixture.alpha"

    def test_entry_point_group_constant(self):
        assert ENTRY_POINT_GROUP == "cauldron.modules"

    def test_discover_unknown_group_returns_empty(self):
        result = discover_modules(entry_point_group="cauldron.nonexistent")
        assert result == []
