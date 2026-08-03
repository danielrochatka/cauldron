"""Tests for entry-point discovery using independently packaged fixture modules."""

import pytest

from cauldron.modules import CauldronModule
from cauldron.modules.discovery import (
    ENTRY_POINT_GROUP,
    DiscoveredModule,
    DiscoveryResult,
    discover_modules,
)


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

    def test_result_has_records(self, result):
        assert hasattr(result, "records")
        assert isinstance(result.records, list)

    def test_no_discovery_errors_from_valid_fixtures(self, result):
        assert result.errors == [], [e.message for e in result.errors]

    def test_modules_sorted_by_slug(self, result):
        slugs = [m.slug for m in result.modules]
        assert slugs == sorted(slugs)

    def test_records_sorted_by_slug(self, result):
        slugs = [r.slug for r in result.records]
        assert slugs == sorted(slugs)

    def test_modules_compat_property_matches_records(self, result):
        module_slugs = [m.slug for m in result.modules]
        record_slugs = [r.slug for r in result.records]
        assert module_slugs == record_slugs

    def test_modules_compat_returns_module_objects(self, result):
        for m in result.modules:
            assert isinstance(m, CauldronModule)


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
        assert r.records == []


class TestDiscoveredModuleRecord:
    """Tests for the DiscoveredModule value object."""

    def test_records_are_discovered_module_instances(self, result):
        for rec in result.records:
            assert isinstance(rec, DiscoveredModule)

    def test_record_slug_matches_module_slug(self, result):
        for rec in result.records:
            assert rec.slug == rec.module.slug

    def test_record_label_matches_module_label(self, result):
        for rec in result.records:
            assert rec.label == rec.module.label

    def test_record_source_type_is_package(self, result):
        for rec in result.records:
            assert rec.source_type == "package"

    def test_record_entry_point_group_is_correct(self, result):
        for rec in result.records:
            assert rec.entry_point_group == ENTRY_POINT_GROUP

    def test_record_has_entry_point_name(self, result):
        for rec in result.records:
            assert isinstance(rec.entry_point_name, str)
            assert rec.entry_point_name  # non-empty

    def test_record_has_entry_point_value(self, result):
        for rec in result.records:
            assert isinstance(rec.entry_point_value, str)

    def test_record_manifest_is_module_manifest(self, result):
        from cauldron.modules import ModuleManifest
        for rec in result.records:
            assert isinstance(rec.manifest, ModuleManifest)
            assert rec.manifest is rec.module.manifest

    def test_record_is_frozen(self, result):
        if not result.records:
            pytest.skip("no records")
        rec = result.records[0]
        with pytest.raises((AttributeError, TypeError)):
            rec.slug = "mutated"  # type: ignore[misc]

    def test_to_dict_returns_json_safe_dict(self, result):
        import json
        for rec in result.records:
            d = rec.to_dict()
            assert isinstance(d, dict)
            json.dumps(d)  # must not raise

    def test_to_dict_has_required_keys(self, result):
        if not result.records:
            pytest.skip("no records")
        d = result.records[0].to_dict()
        expected_keys = {
            "slug", "label", "version", "source_type",
            "package_name", "package_version",
            "entry_point_group", "entry_point_name", "entry_point_value",
        }
        assert expected_keys.issubset(d.keys())

    def test_to_dict_excludes_live_objects(self, result):
        """to_dict must not include module or manifest (not JSON-serialisable)."""
        if not result.records:
            pytest.skip("no records")
        d = result.records[0].to_dict()
        assert "module" not in d
        assert "manifest" not in d


class TestDuplicateSlugHandling:
    def test_duplicate_slug_produces_structured_error(self, monkeypatch):
        """Simulate two entry points yielding the same slug."""
        from unittest.mock import patch

        from cauldron_fixture_alpha import module as alpha_module

        # Names are sorted before processing; "alpha-copy" < "alpha-orig"
        # so "alpha-copy" wins and "alpha-orig" is the duplicate.
        fake_eps = [
            type("EP", (), {"name": "alpha-orig", "load": lambda s: alpha_module, "value": "v1"})(),
            type("EP", (), {"name": "alpha-copy", "load": lambda s: alpha_module, "value": "v2"})(),
        ]

        with patch("cauldron.modules.discovery.entry_points", return_value=fake_eps):
            r = discover_modules()

        assert len(r.modules) == 1  # only the first (alphabetically) registered
        assert len(r.errors) == 1
        assert r.errors[0].kind == "duplicate_slug"
        # The error is attached to the entry point that was deduplicated
        assert r.errors[0].entry_point_name == "alpha-orig"

    def test_duplicate_error_identifies_both_endpoints(self, monkeypatch):
        """Duplicate error message must name accepted and rejected entry points."""
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        mod = BaseModule(ModuleManifest(slug="dup.slug", label="Dup"))

        fake_eps = [
            type("EP", (), {"name": "ep-first", "load": lambda s: mod, "value": "v1"})(),
            type("EP", (), {"name": "ep-second", "load": lambda s: mod, "value": "v2"})(),
        ]

        with patch("cauldron.modules.discovery.entry_points", return_value=fake_eps):
            r = discover_modules()

        assert len(r.errors) == 1
        msg = r.errors[0].message
        assert "ep-second" in msg  # rejected
        assert "ep-first" in msg   # accepted

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


class TestManifestValidation:
    """Tests for manifest validation errors during discovery."""

    def _fake_ep(self, name: str, obj: object, value: str = "mod:obj"):
        return type("EP", (), {"name": name, "load": lambda s: obj, "value": value})()

    def test_non_protocol_object_produces_manifest_validation_error(self):
        from unittest.mock import patch

        class NotAModule:
            pass

        eps = [self._fake_ep("bad.ep", NotAModule())]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert len(r.errors) == 1
        assert r.errors[0].kind == "manifest_validation"
        assert "bad.ep" in r.errors[0].entry_point_name

    def test_slug_label_mismatch_produces_manifest_validation_error(self):
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        class Mismatched(BaseModule):
            @property
            def slug(self) -> str:
                return "different.slug"

        manifest = ModuleManifest(slug="the.real.slug", label="Label")
        obj = Mismatched(manifest)

        eps = [self._fake_ep("mismatched.ep", obj)]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert any(e.kind == "manifest_validation" for e in r.errors)
        assert obj not in r.modules

    def test_bad_manifest_type_produces_manifest_validation_error(self):
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

        class BadManifestModule:
            slug = "some.slug"
            label = "Label"
            manifest = "not-a-manifest"  # type: ignore[assignment]

            def django_apps(self):
                return ()

        eps = [self._fake_ep("bad.manifest.ep", BadManifestModule())]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert any(e.kind == "manifest_validation" for e in r.errors)

    def test_valid_module_produces_no_manifest_validation_errors(self):
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        mod = BaseModule(ModuleManifest(slug="valid.mod", label="Valid"))
        eps = [self._fake_ep("valid.ep", mod, "valid_mod:module")]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert r.errors == []
        assert len(r.records) == 1
        assert r.records[0].slug == "valid.mod"

    def test_django_apps_inconsistency_produces_manifest_validation_error(self):
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        class InconsistentApps(BaseModule):
            def django_apps(self):
                return ("different_app",)

        manifest = ModuleManifest(
            slug="test.mod",
            label="Test",
            django_apps=("original_app",),
        )
        obj = InconsistentApps(manifest)

        eps = [self._fake_ep("apps.ep", obj)]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert any(e.kind == "manifest_validation" for e in r.errors)
        assert obj not in r.modules


class TestDeterministicOrdering:
    """Tests that discovery ordering is deterministic with composite sort key."""

    def test_reversed_entry_point_list_produces_same_order(self):
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        mod_a = BaseModule(ModuleManifest(slug="aaa.mod", label="A"))
        mod_b = BaseModule(ModuleManifest(slug="zzz.mod", label="Z"))

        eps_forward = [
            type("EP", (), {"name": "alpha.ep", "load": lambda s: mod_a, "value": "a:m"})(),
            type("EP", (), {"name": "beta.ep", "load": lambda s: mod_b, "value": "b:m"})(),
        ]
        eps_reversed = list(reversed(eps_forward))

        with patch("cauldron.modules.discovery.entry_points", return_value=eps_forward):
            r1 = discover_modules()

        with patch("cauldron.modules.discovery.entry_points", return_value=eps_reversed):
            r2 = discover_modules()

        assert [rec.slug for rec in r1.records] == [rec.slug for rec in r2.records]

    def test_composite_sort_key_breaks_ep_name_ties_by_dist_then_value(self):
        """When two EPs share the same name, dist_name then value breaks the tie."""
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        mod = BaseModule(ModuleManifest(slug="shared.slug", label="Shared"))

        class EP:
            def __init__(self, name, value):
                self.name = name
                self.value = value
                self.dist = None

            def load(self):
                return mod

        ep_a = EP("same.name", "aaa_module:mod")
        ep_b = EP("same.name", "zzz_module:mod")

        # ep_a should sort before ep_b due to value "aaa" < "zzz"
        # ep_a wins (accepted), ep_b gets duplicate_slug error
        with patch("cauldron.modules.discovery.entry_points", return_value=[ep_b, ep_a]):
            r = discover_modules()

        assert len(r.records) == 1
        assert len(r.errors) == 1
        assert r.errors[0].kind == "duplicate_slug"
        assert r.errors[0].entry_point_name == "same.name"
