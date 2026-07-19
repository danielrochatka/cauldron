"""Tests for entry-point discovery using independently packaged fixture modules."""

import pytest

from cauldron.modules import CauldronModule
from cauldron.modules.discovery import ENTRY_POINT_GROUP, DiscoveryResult, discover_modules


@pytest.fixture(scope="module")
def result() -> DiscoveryResult:
    return discover_modules()


@pytest.fixture(scope="module")
def by_slug(result: DiscoveryResult) -> dict:
    return {m.slug: m for m in result.modules}


class TestDiscoveryResult:
    def test_returns_discovery_result(self, result):
        assert isinstance(result, DiscoveryResult)

    def test_result_has_modules_and_errors(self, result):
        assert hasattr(result, "modules")
        assert hasattr(result, "errors")

    def test_no_discovery_errors_from_valid_fixtures(self, result):
        assert result.errors == [], [e.message for e in result.errors]

    def test_modules_sorted_by_slug(self, result):
        slugs = [m.slug for m in result.modules]
        assert slugs == sorted(slugs)


class TestEntryPointDiscovery:
    def test_discovers_fixture_alpha(self, by_slug):
        assert "cauldron.fixture.alpha" in by_slug

    def test_discovers_fixture_beta(self, by_slug):
        assert "cauldron.fixture.beta" in by_slug

    def test_all_modules_satisfy_protocol(self, result):
        for module in result.modules:
            assert isinstance(module, CauldronModule), (
                f"{module!r} does not satisfy CauldronModule protocol"
            )

    def test_alpha_manifest_fields(self, by_slug):
        alpha = by_slug["cauldron.fixture.alpha"]
        assert alpha.manifest.slug == "cauldron.fixture.alpha"
        assert alpha.manifest.label == "Cauldron Fixture Alpha"
        assert alpha.manifest.version == "1.0.0"
        assert "test.capability.alpha" in alpha.manifest.provides

    def test_beta_manifest_fields(self, by_slug):
        beta = by_slug["cauldron.fixture.beta"]
        assert beta.manifest.slug == "cauldron.fixture.beta"
        assert beta.manifest.label == "Cauldron Fixture Beta"
        assert len(beta.manifest.requires) == 1
        assert beta.manifest.requires[0].slug == "cauldron.fixture.alpha"

    def test_entry_point_group_constant(self):
        assert ENTRY_POINT_GROUP == "cauldron.modules"

    def test_unknown_group_returns_empty_result(self):
        r = discover_modules(entry_point_group="cauldron.nonexistent")
        assert r.modules == []
        assert r.errors == []


class TestDuplicateSlugHandling:
    def test_duplicate_slug_produces_structured_error(self, monkeypatch):
        """Simulate two entry points yielding the same slug."""
        from unittest.mock import patch

        from cauldron_fixture_alpha import module as alpha_module

        # Names are sorted before processing; "alpha-copy" < "alpha-orig"
        # so "alpha-copy" wins and "alpha-orig" is the duplicate.
        fake_eps = [
            type("EP", (), {"name": "alpha-orig", "load": lambda s: alpha_module})(),
            type("EP", (), {"name": "alpha-copy", "load": lambda s: alpha_module})(),
        ]

        with patch("cauldron.modules.discovery.entry_points", return_value=fake_eps):
            r = discover_modules()

        assert len(r.modules) == 1  # only the first (alphabetically) registered
        assert len(r.errors) == 1
        assert r.errors[0].kind == "duplicate_slug"
        # The error is attached to the entry point that was deduplicated
        assert r.errors[0].entry_point_name == "alpha-orig"

    def test_load_failure_produces_structured_error(self, monkeypatch):
        """Simulate an entry point that raises on load."""
        def bad_load():
            raise ImportError("missing dep")

        fake_eps = [
            type("EP", (), {"name": "broken.module", "load": lambda s: bad_load()})(),
        ]

        from unittest.mock import patch
        with patch("cauldron.modules.discovery.entry_points", return_value=fake_eps):
            r = discover_modules()

        assert r.modules == []
        assert len(r.errors) == 1
        assert r.errors[0].kind == "load_failure"
        assert "broken.module" == r.errors[0].entry_point_name
