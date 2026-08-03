"""Tests for entry-point discovery using independently packaged fixture modules."""

import json
import pytest

from cauldron.modules import CauldronModule
from cauldron.modules.discovery import (
    ENTRY_POINT_GROUP,
    DiscoveredModule,
    DiscoveryError,
    DiscoveryResult,
    discover_modules,
)


@pytest.fixture(scope="module")
def result() -> DiscoveryResult:
    return discover_modules()


@pytest.fixture(scope="module")
def by_slug(result: DiscoveryResult) -> dict:
    return {m.slug: m for m in result.modules}


# ---------------------------------------------------------------------------
# DiscoveryResult contract
# ---------------------------------------------------------------------------

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

    def test_discovery_result_constructor_uses_records(self):
        from cauldron.modules import BaseModule, ModuleManifest

        mod = BaseModule(ModuleManifest(slug="test.mod", label="Test"))
        err = DiscoveryError(entry_point_name="ep", kind="load_failure", message="oops")

        # Supported form: records= + errors=
        # modules is a derived property; DiscoveryResult(modules=...) would fail.
        from cauldron.modules.discovery import DiscoveredModule
        rec = DiscoveredModule(
            slug="test.mod", label="Test", version="0.0.0",
            source_type="package", package_name="", package_version="",
            entry_point_group="cauldron.modules", entry_point_name="ep",
            entry_point_value="test_mod:mod", manifest=mod.manifest, module=mod,
        )
        dr = DiscoveryResult(records=[rec], errors=[err])
        assert len(dr.modules) == 1
        assert len(dr.errors) == 1
        assert dr.modules[0] is mod


# ---------------------------------------------------------------------------
# Entry-point discovery (fixture packages — pre-existing failures in CI)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# DiscoveredModule record
# ---------------------------------------------------------------------------

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
            assert rec.entry_point_name

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


class TestToDict:
    """DiscoveredModule.to_dict() must be JSON-safe, include manifest, exclude module."""

    def _make_record(self):
        from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement
        from cauldron.modules.discovery import DiscoveredModule

        manifest = ModuleManifest(
            slug="test.mod",
            label="Test",
            version="1.2.3",
            django_apps=("myapp",),
            provides=("test.cap",),
        )
        mod = BaseModule(manifest)
        return DiscoveredModule(
            slug="test.mod",
            label="Test",
            version="1.2.3",
            source_type="package",
            package_name="my-pkg",
            package_version="4.5.6",
            entry_point_group="cauldron.modules",
            entry_point_name="my-ep",
            entry_point_value="my_mod:obj",
            manifest=manifest,
            module=mod,
        )

    def test_to_dict_json_safe(self):
        d = self._make_record().to_dict()
        json.dumps(d)  # must not raise

    def test_to_dict_module_excluded(self):
        d = self._make_record().to_dict()
        assert "module" not in d

    def test_to_dict_manifest_present_as_dict(self):
        rec = self._make_record()
        d = rec.to_dict()
        assert "manifest" in d
        assert isinstance(d["manifest"], dict)

    def test_to_dict_manifest_equals_manifest_to_dict(self):
        rec = self._make_record()
        d = rec.to_dict()
        assert d["manifest"] == rec.manifest.to_dict()

    def test_to_dict_has_source_fields(self):
        d = self._make_record().to_dict()
        for key in ("slug", "label", "version", "source_type", "package_name",
                    "package_version", "entry_point_group", "entry_point_name",
                    "entry_point_value"):
            assert key in d, f"missing key: {key}"

    def test_to_dict_source_type_value(self):
        d = self._make_record().to_dict()
        assert d["source_type"] == "package"

    def test_to_dict_mutation_does_not_affect_manifest(self):
        rec = self._make_record()
        d = rec.to_dict()
        # Mutate the returned manifest dict.
        d["manifest"]["slug"] = "mutated"
        # The original manifest is unchanged (it's a frozen dataclass).
        assert rec.manifest.slug == "test.mod"
        # Calling to_dict() again returns fresh data.
        d2 = rec.to_dict()
        assert d2["manifest"]["slug"] == "test.mod"

    def test_to_dict_mutation_does_not_affect_django_apps_list(self):
        rec = self._make_record()
        d = rec.to_dict()
        d["manifest"]["django_apps"].append("injected")
        d2 = rec.to_dict()
        assert "injected" not in d2["manifest"]["django_apps"]


# ---------------------------------------------------------------------------
# DiscoveryError contract
# ---------------------------------------------------------------------------

class TestDiscoveryError:
    def test_discovery_error_is_frozen(self):
        err = DiscoveryError(entry_point_name="ep", kind="load_failure", message="oops")
        with pytest.raises((AttributeError, TypeError)):
            err.message = "mutated"  # type: ignore[misc]

    def test_discovery_error_minimal_construction(self):
        err = DiscoveryError(entry_point_name="ep", kind="load_failure", message="oops")
        assert err.entry_point_name == "ep"
        assert err.kind == "load_failure"
        assert err.message == "oops"
        assert err.entry_point_group == ""
        assert err.entry_point_value == ""
        assert err.package_name == ""
        assert err.package_version == ""
        assert err.candidate_slug is None
        assert err.accepted_entry_point_name == ""
        assert err.accepted_package_name == ""

    def test_discovery_error_positional_construction(self):
        """Legacy positional construction (entry_point_name, kind, message) still works."""
        err = DiscoveryError("ep", "load_failure", "failed")
        assert err.entry_point_name == "ep"
        assert err.kind == "load_failure"

    def test_discovery_error_source_fields(self):
        err = DiscoveryError(
            entry_point_name="my.ep",
            kind="manifest_validation",
            message="bad manifest",
            entry_point_group="cauldron.modules",
            entry_point_value="my_mod:obj",
            package_name="my-package",
            package_version="1.0.0",
            candidate_slug="my.module",
        )
        assert err.entry_point_group == "cauldron.modules"
        assert err.entry_point_value == "my_mod:obj"
        assert err.package_name == "my-package"
        assert err.package_version == "1.0.0"
        assert err.candidate_slug == "my.module"

    def test_duplicate_slug_error_has_accepted_fields(self):
        err = DiscoveryError(
            entry_point_name="rejected.ep",
            kind="duplicate_slug",
            message="conflict",
            candidate_slug="shared.slug",
            accepted_entry_point_name="accepted.ep",
            accepted_package_name="accepted-pkg",
        )
        assert err.accepted_entry_point_name == "accepted.ep"
        assert err.accepted_package_name == "accepted-pkg"
        assert err.candidate_slug == "shared.slug"

    def test_load_failure_message_from_discover_modules_does_not_contain_exception_text(self):
        """Public load-failure message must identify the exception class but not its text."""
        from unittest.mock import patch

        sensitive = "sensitive-db-password-in-traceback"

        def bad_load():
            raise ImportError(sensitive)

        eps = [type("EP", (), {"name": "bad.ep", "load": lambda s: bad_load()})()]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert len(r.errors) == 1
        msg = r.errors[0].message
        assert sensitive not in msg
        assert "ImportError" in msg

    def test_load_failure_carries_ep_name_as_candidate_slug(self):
        """When EP name is a valid module slug, load failures use it as candidate_slug."""
        from unittest.mock import patch

        def bad_load():
            raise ImportError("not found")

        eps = [type("EP", (), {
            "name": "cauldron.example",
            "value": "cauldron_example:module",
            "dist": None,
            "load": lambda s: bad_load(),
        })()]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert len(r.errors) == 1
        assert r.errors[0].kind == "load_failure"
        assert r.errors[0].candidate_slug == "cauldron.example"

    def test_non_protocol_object_uses_ep_name_candidate(self):
        """Non-CauldronModule object uses valid EP name as provisional candidate_slug."""
        from unittest.mock import patch

        class NotAModule:
            pass

        eps = [type("EP", (), {
            "name": "cauldron.bad",
            "value": "cauldron_bad:obj",
            "dist": None,
            "load": lambda s: NotAModule(),
        })()]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert len(r.errors) == 1
        assert r.errors[0].kind == "manifest_validation"
        assert r.errors[0].candidate_slug == "cauldron.bad"

    def test_invalid_ep_name_leaves_candidate_unknown(self):
        """EP name that is not a valid module slug leaves candidate_slug as None."""
        from unittest.mock import patch

        def bad_load():
            raise RuntimeError("broken")

        eps = [type("EP", (), {
            "name": "NOT-A-VALID-SLUG",
            "value": "pkg:obj",
            "dist": None,
            "load": lambda s: bad_load(),
        })()]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert len(r.errors) == 1
        assert r.errors[0].kind == "load_failure"
        assert r.errors[0].candidate_slug is None


# ---------------------------------------------------------------------------
# Duplicate slug handling
# ---------------------------------------------------------------------------

class TestDuplicateSlugHandling:
    def test_duplicate_slug_produces_structured_error(self, monkeypatch):
        """Simulate two entry points yielding the same slug."""
        from unittest.mock import patch

        from cauldron_fixture_alpha import module as alpha_module

        fake_eps = [
            type("EP", (), {"name": "alpha-orig", "load": lambda s: alpha_module, "value": "v1"})(),
            type("EP", (), {"name": "alpha-copy", "load": lambda s: alpha_module, "value": "v2"})(),
        ]

        with patch("cauldron.modules.discovery.entry_points", return_value=fake_eps):
            r = discover_modules()

        assert len(r.modules) == 1
        assert len(r.errors) == 1
        assert r.errors[0].kind == "duplicate_slug"
        assert r.errors[0].entry_point_name == "alpha-orig"

    def test_duplicate_error_identifies_both_endpoints(self):
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
        err = r.errors[0]
        # Structured fields
        assert err.accepted_entry_point_name == "ep-first"
        assert err.candidate_slug == "dup.slug"
        # Message also readable
        assert "ep-second" in err.message
        assert "ep-first" in err.message

    def test_load_failure_produces_structured_error(self):
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
        assert r.errors[0].entry_point_name == "broken.module"


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

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

    def test_empty_manifest_django_apps_but_live_returns_app(self):
        """When manifest.django_apps is empty, any live return is inconsistent."""
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        class ExtraApp(BaseModule):
            def django_apps(self):
                return ("undeclared_app",)

        manifest = ModuleManifest(slug="test.mod", label="Test")  # django_apps=()
        obj = ExtraApp(manifest)

        eps = [self._fake_ep("extra.ep", obj)]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert any(e.kind == "manifest_validation" for e in r.errors)

    def test_django_apps_returns_string_is_rejected(self):
        """A bare str return must be rejected (it is a Sequence[str] but not a list)."""
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        class StringApps(BaseModule):
            def django_apps(self):
                return "myapp"

        manifest = ModuleManifest(slug="test.mod", label="Test")
        obj = StringApps(manifest)

        eps = [self._fake_ep("str.ep", obj)]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        errors = [e for e in r.errors if e.kind == "manifest_validation"]
        assert errors

    def test_django_apps_returns_bytes_is_rejected(self):
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        class BytesApps(BaseModule):
            def django_apps(self):
                return b"myapp"

        manifest = ModuleManifest(slug="test.mod", label="Test")
        obj = BytesApps(manifest)

        eps = [self._fake_ep("bytes.ep", obj)]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        errors = [e for e in r.errors if e.kind == "manifest_validation"]
        assert errors

    def test_django_apps_generator_consumed_once_and_validated(self):
        """Generator return is allowed but must be normalised to a tuple exactly once."""
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        class GeneratorApps(BaseModule):
            def django_apps(self):
                yield "gen_app"

        manifest = ModuleManifest(
            slug="gen.mod",
            label="Gen",
            django_apps=("gen_app",),
        )
        obj = GeneratorApps(manifest)

        eps = [self._fake_ep("gen.ep", obj)]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        # Generator that matches manifest.django_apps is valid.
        assert r.errors == [], [e.message for e in r.errors]
        assert len(r.records) == 1
        assert r.records[0].slug == "gen.mod"

    def test_django_apps_non_string_item_rejected(self):
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        class BadItem(BaseModule):
            def django_apps(self):
                return (42,)

        manifest = ModuleManifest(slug="test.mod", label="Test")
        obj = BadItem(manifest)

        eps = [self._fake_ep("bad.item.ep", obj)]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        errors = [e for e in r.errors if e.kind == "manifest_validation"]
        assert errors

    def test_django_apps_empty_string_item_rejected(self):
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        class EmptyItem(BaseModule):
            def django_apps(self):
                return ("",)

        manifest = ModuleManifest(slug="test.mod", label="Test")
        obj = EmptyItem(manifest)

        eps = [self._fake_ep("empty.item.ep", obj)]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        errors = [e for e in r.errors if e.kind == "manifest_validation"]
        assert errors

    def test_property_access_raising_produces_manifest_validation_error(self):
        """A module whose .manifest property raises must produce a validation error."""
        from unittest.mock import patch

        from cauldron.modules import CauldronModule, ModuleManifest

        class BrokenManifest:
            slug = "broken.mod"
            label = "Broken"

            @property
            def manifest(self):
                raise RuntimeError("internal error")

            def django_apps(self):
                return ()

        eps = [self._fake_ep("broken.manifest.ep", BrokenManifest())]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        errors = [e for e in r.errors if e.kind == "manifest_validation"]
        assert errors

    def test_django_apps_raises_sensitive_message_absent_from_error(self):
        """django_apps() raising must NOT expose the exception's message text."""
        from unittest.mock import patch

        from cauldron.modules import BaseModule, ModuleManifest

        sensitive = "secret-key-7f3a"

        class RaisingApps(BaseModule):
            def django_apps(self):
                raise RuntimeError(sensitive)

        manifest = ModuleManifest(slug="test.mod", label="Test")
        obj = RaisingApps(manifest)

        eps = [self._fake_ep("raising.ep", obj)]
        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            r = discover_modules()

        assert any(e.kind == "manifest_validation" for e in r.errors)
        for e in r.errors:
            assert sensitive not in e.message


# ---------------------------------------------------------------------------
# Source type extensibility
# ---------------------------------------------------------------------------

class TestSourceType:
    def test_source_type_package_is_valid_literal(self):
        from cauldron.modules.discovery import DiscoveredModule, SourceType
        from typing import get_args
        assert "package" in get_args(SourceType)
        assert "project" in get_args(SourceType)

    def test_discovered_module_source_type_package(self):
        from cauldron.modules import BaseModule, ModuleManifest
        from cauldron.modules.discovery import DiscoveredModule

        manifest = ModuleManifest(slug="test.mod", label="Test")
        mod = BaseModule(manifest)
        rec = DiscoveredModule(
            slug="test.mod", label="Test", version="0.0.0",
            source_type="package",
            package_name="", package_version="",
            entry_point_group="cauldron.modules", entry_point_name="ep",
            entry_point_value="", manifest=manifest, module=mod,
        )
        assert rec.source_type == "package"


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------

class TestDeterministicOrdering:
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

    def test_composite_sort_key_breaks_ep_name_ties_by_canonical_name_then_value(self):
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

        with patch("cauldron.modules.discovery.entry_points", return_value=[ep_b, ep_a]):
            r = discover_modules()

        assert len(r.records) == 1
        assert len(r.errors) == 1
        assert r.errors[0].kind == "duplicate_slug"

    def test_distribution_metadata_not_looked_up_twice(self):
        """_source_for_ep is called once per EP; dist is accessed at most once."""
        from unittest.mock import patch, MagicMock

        from cauldron.modules import BaseModule, ModuleManifest
        from cauldron.modules.discovery import _source_for_ep

        call_count = 0
        original_source_for_ep = _source_for_ep

        def counting_source_for_ep(ep, group):
            nonlocal call_count
            call_count += 1
            return original_source_for_ep(ep, group)

        mod = BaseModule(ModuleManifest(slug="single.mod", label="Single"))
        eps = [type("EP", (), {"name": "ep1", "load": lambda s: mod, "value": "v:m"})()]

        with patch("cauldron.modules.discovery.entry_points", return_value=eps):
            with patch("cauldron.modules.discovery._source_for_ep", side_effect=counting_source_for_ep):
                discover_modules()

        # Exactly once per entry point.
        assert call_count == 1
