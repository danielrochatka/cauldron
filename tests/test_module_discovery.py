"""Tests for entry-point discovery using independently packaged fixture modules."""

import importlib
import json
import sys

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
        from cauldron.modules import BaseModule, ModuleManifest
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


# ---------------------------------------------------------------------------
# Project-folder discovery (#34)
# ---------------------------------------------------------------------------

def _write_project_module(
    root,
    *,
    dir_name: str,
    slug: str,
    label: str = "Test Module",
):
    """Write a minimal valid project module under root/dir_name/."""
    pkg = root / dir_name
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text(
        f"from cauldron.modules import BaseModule, ModuleManifest\n"
        f"module = BaseModule(ModuleManifest(slug={slug!r}, label={label!r}))\n"
    )
    return pkg


class TestProjectModuleDiscovery:
    """Tests for project-folder module discovery (CAULDRON_PROJECT_MODULE_ROOT)."""

    @pytest.fixture(autouse=True)
    def _clean_import_state(self):
        """Restore sys.path and evict any modules imported during the test."""
        original_path = list(sys.path)
        original_modules = set(sys.modules.keys())
        yield
        sys.path[:] = original_path
        for key in list(sys.modules.keys()):
            if key not in original_modules:
                del sys.modules[key]

    def test_no_project_root_returns_no_project_records(self):
        r = discover_modules(project_module_root=None)
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert project_recs == []

    def test_nonexistent_root_produces_project_path_error(self, tmp_path):
        r = discover_modules(project_module_root=tmp_path / "no_such_dir")
        assert any(e.kind == "project_path" for e in r.errors)
        assert r.records == [] or all(rec.source_type != "project" for rec in r.records)

    def test_file_not_dir_produces_project_path_error(self, tmp_path):
        f = tmp_path / "notadir.txt"
        f.write_text("x")
        r = discover_modules(project_module_root=f)
        assert any(e.kind == "project_path" for e in r.errors)

    def test_valid_project_module_discovered(self, tmp_path):
        _write_project_module(tmp_path, dir_name="mymod", slug="mymod.pkg", label="My Mod")
        r = discover_modules(project_module_root=tmp_path)
        pkg_errors = [e for e in r.errors if e.kind != "project_path"]
        assert pkg_errors == [], [e.message for e in pkg_errors]
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert len(project_recs) == 1
        assert project_recs[0].slug == "mymod.pkg"

    def test_project_module_source_type_is_project(self, tmp_path):
        _write_project_module(tmp_path, dir_name="mymod", slug="mymod.pkg")
        r = discover_modules(project_module_root=tmp_path)
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert project_recs
        assert all(rec.source_type == "project" for rec in project_recs)

    def test_project_module_project_path_is_dir_name(self, tmp_path):
        _write_project_module(tmp_path, dir_name="mymod", slug="mymod.pkg")
        r = discover_modules(project_module_root=tmp_path)
        rec = next(rec for rec in r.records if rec.slug == "mymod.pkg")
        assert rec.project_path == "mymod"
        assert not rec.project_path.startswith("/")

    def test_project_module_package_fields_empty(self, tmp_path):
        _write_project_module(tmp_path, dir_name="mymod", slug="mymod.pkg")
        r = discover_modules(project_module_root=tmp_path)
        rec = next(rec for rec in r.records if rec.source_type == "project")
        assert rec.package_name == ""
        assert rec.package_version == ""
        assert rec.entry_point_group == ""

    def test_project_module_to_dict_includes_project_path(self, tmp_path):
        _write_project_module(tmp_path, dir_name="mymod", slug="mymod.pkg")
        r = discover_modules(project_module_root=tmp_path)
        rec = next(rec for rec in r.records if rec.slug == "mymod.pkg")
        d = rec.to_dict()
        assert "project_path" in d
        assert d["project_path"] == "mymod"

    def test_package_module_project_path_is_empty(self, tmp_path):
        """Package-source DiscoveredModules have project_path == ''."""
        r = discover_modules(project_module_root=tmp_path)
        for rec in r.records:
            if rec.source_type == "package":
                assert rec.project_path == ""

    def test_discovery_skips_non_directories(self, tmp_path):
        (tmp_path / "notapackage.py").write_text("x = 1")
        r = discover_modules(project_module_root=tmp_path)
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert project_recs == []

    def test_missing_init_produces_project_path_error(self, tmp_path):
        (tmp_path / "nomod").mkdir()
        r = discover_modules(project_module_root=tmp_path)
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1

    def test_ignored_hidden_dir_silently_skipped(self, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "__init__.py").write_text("module = object()")
        r = discover_modules(project_module_root=tmp_path)
        assert not any(e.kind == "project_path" for e in r.errors)

    def test_ignored_dunder_dir_silently_skipped(self, tmp_path):
        dunder = tmp_path / "__cache__"
        dunder.mkdir()
        (dunder / "__init__.py").write_text("module = object()")
        r = discover_modules(project_module_root=tmp_path)
        assert not any(e.kind == "project_path" for e in r.errors)

    def test_ignored_build_dir_silently_skipped(self, tmp_path):
        build = tmp_path / "build"
        build.mkdir()
        (build / "__init__.py").write_text("module = object()")
        r = discover_modules(project_module_root=tmp_path)
        assert not any(e.kind == "project_path" for e in r.errors)

    def test_ignored_dist_dir_silently_skipped(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "__init__.py").write_text("module = object()")
        r = discover_modules(project_module_root=tmp_path)
        assert not any(e.kind == "project_path" for e in r.errors)

    def test_ignored_egg_info_dir_silently_skipped(self, tmp_path):
        egg = tmp_path / "mymod.egg-info"
        egg.mkdir()
        r = discover_modules(project_module_root=tmp_path)
        assert not any(e.kind == "project_path" for e in r.errors)

    def test_invalid_identifier_produces_project_path_error(self, tmp_path):
        bad = tmp_path / "my-module"
        bad.mkdir()
        (bad / "__init__.py").write_text("x = 1")
        r = discover_modules(project_module_root=tmp_path)
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1
        assert "my-module" in errors[0].message

    def test_string_path_accepted(self, tmp_path):
        _write_project_module(tmp_path, dir_name="mymod", slug="mymod.pkg")
        r = discover_modules(project_module_root=str(tmp_path))
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert len(project_recs) == 1

    def test_path_object_accepted(self, tmp_path):
        _write_project_module(tmp_path, dir_name="mymod", slug="mymod.pkg")
        r = discover_modules(project_module_root=tmp_path)  # Path object
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert len(project_recs) == 1

    def test_pathlike_object_accepted(self, tmp_path):
        import os

        class FakePath(os.PathLike):
            def __fspath__(self): return str(tmp_path)

        _write_project_module(tmp_path, dir_name="mymod", slug="mymod.pkg")
        r = discover_modules(project_module_root=FakePath())
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert len(project_recs) == 1

    def test_integer_produces_project_path_error(self):
        r = discover_modules(project_module_root=42)
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1

    def test_bool_produces_project_path_error(self):
        r = discover_modules(project_module_root=True)
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1

    def test_empty_string_produces_project_path_error(self):
        r = discover_modules(project_module_root="")
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1

    def test_whitespace_only_string_produces_project_path_error(self):
        r = discover_modules(project_module_root="   ")
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1

    def test_empty_string_does_not_resolve_to_cwd(self):
        """An empty string must error, not silently resolve to Path('.') = cwd."""
        r = discover_modules(project_module_root="")
        assert not any(rec.source_type == "project" for rec in r.records)
        assert any(e.kind == "project_path" for e in r.errors)

    def test_candidate_symlink_escape_produces_error(self, tmp_path):
        external = tmp_path / "external_real"
        external.mkdir()
        (external / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='ext.mod', label='Ext'))\n"
        )
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        link = modules_root / "ext_link"
        link.symlink_to(external)  # symlink to dir outside modules_root
        r = discover_modules(project_module_root=modules_root)
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1

    def test_init_symlink_escape_produces_error(self, tmp_path):
        # __init__.py is a symlink pointing outside the project root
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        external_init = tmp_path / "evil_init.py"
        external_init.write_text("module = object()")
        pkg = modules_root / "mymod"
        pkg.mkdir()
        (pkg / "__init__.py").symlink_to(external_init)  # escapes modules_root
        r = discover_modules(project_module_root=modules_root)
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1

    def test_safe_internal_symlink_is_accepted(self, tmp_path):
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        real_pkg = modules_root / "real_mymod"
        real_pkg.mkdir()
        (real_pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='safe.mod', label='Safe'))\n"
        )
        # A symlink INSIDE the modules_root pointing to another dir inside
        link_pkg = modules_root / "alias_mymod"
        link_pkg.symlink_to(real_pkg)
        r = discover_modules(project_module_root=modules_root)
        # Both the real dir and symlink may succeed if they both pass path checks
        # At minimum, no project_path errors for the valid internal symlink
        project_path_errors = [e for e in r.errors if e.kind == "project_path"]
        # Internal symlink is safe — should not produce project_path errors
        assert all("real_mymod" not in e.message and "alias_mymod" not in e.message
                   for e in project_path_errors)

    def test_absolute_path_absent_from_error_messages(self, tmp_path):
        external = tmp_path / "outside"
        external.mkdir()
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        link = modules_root / "link"
        link.symlink_to(external)
        (link / "__init__.py").write_text("x = 1")  # will error since link escapes
        r = discover_modules(project_module_root=modules_root)
        for e in r.errors:
            assert str(tmp_path) not in e.message, (
                f"Absolute path leaked into error message: {e.message!r}"
            )

    def test_existing_sys_modules_package_outside_root_produces_error(self, tmp_path):
        """If a top-level name is already in sys.modules from elsewhere, error."""
        import types
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        pkg = modules_root / "outsidepkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='out.pkg', label='Out'))\n"
        )
        # Inject a fake module into sys.modules as if imported from elsewhere
        fake_mod = types.ModuleType("outsidepkg")
        fake_mod.__file__ = str(tmp_path / "other_location" / "outsidepkg" / "__init__.py")
        sys.modules["outsidepkg"] = fake_mod
        try:
            r = discover_modules(project_module_root=modules_root)
            errors = [e for e in r.errors if e.kind == "project_path"]
            assert len(errors) == 1
        finally:
            sys.modules.pop("outsidepkg", None)

    def test_callable_factory_module_is_accepted(self, tmp_path):
        """If module attribute is a callable factory, calling it should work."""
        pkg = tmp_path / "factmod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "def module():\n"
            "    return BaseModule(ModuleManifest(slug='fact.mod', label='Factory'))\n"
        )
        r = discover_modules(project_module_root=tmp_path)
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert len(project_recs) == 1
        assert project_recs[0].slug == "fact.mod"

    def test_module_attr_access_raising_produces_load_failure(self, tmp_path):
        """If module attribute access raises, produce load_failure error."""
        pkg = tmp_path / "raisemod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "class _Raising:\n"
            "    def __getattr__(self, name):\n"
            "        raise RuntimeError('access denied')\n"
            "\n"
            "import sys\n"
            "sys.modules[__name__].__class__ = type(sys)  # module with __getattr__\n"
            "# Override module-level getattr\n"
            "def __getattr__(name):\n"
            "    if name == 'module':\n"
            "        raise RuntimeError('attribute access denied')\n"
            "    raise AttributeError(name)\n"
        )
        r = discover_modules(project_module_root=tmp_path)
        errors = [e for e in r.errors if e.kind == "load_failure"]
        assert len(errors) == 1

    def test_repeated_discovery_is_idempotent(self, tmp_path):
        _write_project_module(tmp_path, dir_name="idempmod", slug="idemp.mod")
        r1 = discover_modules(project_module_root=tmp_path)
        r2 = discover_modules(project_module_root=tmp_path)
        slugs1 = [rec.slug for rec in r1.records if rec.source_type == "project"]
        slugs2 = [rec.slug for rec in r2.records if rec.source_type == "project"]
        assert slugs1 == slugs2

    def test_import_error_produces_load_failure(self, tmp_path):
        pkg = tmp_path / "broken"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("raise ImportError('broken on purpose')")
        r = discover_modules(project_module_root=tmp_path)
        errors = [e for e in r.errors if e.kind == "load_failure"]
        assert len(errors) == 1
        assert "modules/broken" in errors[0].entry_point_name

    def test_missing_module_attr_produces_load_failure(self, tmp_path):
        pkg = tmp_path / "nopkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("x = 1  # no 'module' attribute")
        r = discover_modules(project_module_root=tmp_path)
        errors = [e for e in r.errors if e.kind == "load_failure"]
        assert len(errors) == 1

    def test_invalid_manifest_produces_validation_error(self, tmp_path):
        pkg = tmp_path / "badmod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("module = object()  # not CauldronModule")
        r = discover_modules(project_module_root=tmp_path)
        errors = [e for e in r.errors if e.kind == "manifest_validation"]
        assert len(errors) == 1

    def test_duplicate_slug_between_project_modules_produces_error(self, tmp_path):
        for dir_name in ("mod_a", "mod_b"):
            _write_project_module(
                tmp_path, dir_name=dir_name, slug="shared.slug", label="Shared",
            )
        r = discover_modules(project_module_root=tmp_path)
        dup_errors = [e for e in r.errors if e.kind == "duplicate_slug"]
        assert len(dup_errors) == 1
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert len(project_recs) == 1

    def test_project_module_wins_slug_race_over_package_module(self, tmp_path):
        """When a project and package module share a slug, the project wins.

        The EP uses a different top-level import name than the project directory
        to avoid triggering import-name collision detection (Section 1).
        Slug-race detection is independent of import-name collision detection.
        """
        from unittest.mock import patch
        from cauldron.modules import BaseModule, ModuleManifest

        # Project directory is "pkgmod"; EP import name is "pkgmod_installed"
        # (a different top-level name) so no import-name collision is triggered.
        _write_project_module(tmp_path, dir_name="pkgmod", slug="shared.slug", label="Project")
        pkg_mod = BaseModule(ModuleManifest(slug="shared.slug", label="Package"))
        fake_ep = type("EP", (), {
            "name": "shared.slug",
            "value": "pkgmod_installed:module",
            "dist": None,
            "load": lambda s: pkg_mod,
        })()
        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep]):
            r = discover_modules(project_module_root=tmp_path)

        assert len([rec for rec in r.records if rec.slug == "shared.slug"]) == 1
        winner = next(rec for rec in r.records if rec.slug == "shared.slug")
        assert winner.source_type == "project"

        dup_errors = [e for e in r.errors if e.kind == "duplicate_slug"]
        assert len(dup_errors) == 1

    def test_sys_path_restored_after_discovery(self, tmp_path):
        """sys.path must be identical before and after discover_modules()."""
        from unittest.mock import patch
        _write_project_module(tmp_path, dir_name="pathmod", slug="path.mod")

        path_before = list(sys.path)
        with patch("cauldron.modules.discovery.entry_points", return_value=[]):
            discover_modules(project_module_root=tmp_path)

        assert sys.path == path_before, (
            f"sys.path changed after discovery.\n"
            f"Before: {path_before}\nAfter: {sys.path}"
        )

    def test_sys_path_restored_after_discovery_with_ep(self, tmp_path):
        """sys.path restoration holds even when EPs are present."""
        from unittest.mock import patch
        from cauldron.modules import BaseModule, ModuleManifest

        _write_project_module(tmp_path, dir_name="pathmod2", slug="path.mod2")
        ep_obj = BaseModule(ModuleManifest(slug="ep.path", label="EP Path"))
        fake_ep = type("EP", (), {
            "name": "ep.path",
            "value": "ep_path_pkg:module",
            "dist": None,
            "load": lambda s: ep_obj,
        })()

        path_before = list(sys.path)
        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep]):
            discover_modules(project_module_root=tmp_path)

        assert sys.path == path_before, (
            f"sys.path changed.\nBefore: {path_before}\nAfter: {sys.path}"
        )

    def test_combined_records_sorted_by_slug(self, tmp_path):
        _write_project_module(tmp_path, dir_name="zzz_proj", slug="zzz.project")
        r = discover_modules(project_module_root=tmp_path)
        slugs = [rec.slug for rec in r.records]
        assert slugs == sorted(slugs)

    def test_multiple_project_modules_discovered(self, tmp_path):
        _write_project_module(tmp_path, dir_name="alpha_m", slug="alpha.proj")
        _write_project_module(tmp_path, dir_name="beta_m", slug="beta.proj")
        r = discover_modules(project_module_root=tmp_path)
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        slugs = {rec.slug for rec in project_recs}
        assert slugs == {"alpha.proj", "beta.proj"}

    def test_load_failure_message_hides_exception_text(self, tmp_path):
        """Import failure message must not expose raw exception text."""
        sensitive = "super-secret-credential-xyz"
        pkg = tmp_path / "leaky"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            f"raise ImportError({sensitive!r})"
        )
        r = discover_modules(project_module_root=tmp_path)
        errors = [e for e in r.errors if e.kind == "load_failure"]
        assert errors
        for e in errors:
            assert sensitive not in e.message

    def test_project_path_error_has_empty_entry_point_name(self, tmp_path):
        """Path-level errors have empty entry_point_name (no specific module failed)."""
        r = discover_modules(project_module_root=tmp_path / "missing")
        path_errors = [e for e in r.errors if e.kind == "project_path"]
        assert path_errors
        assert all(e.entry_point_name == "" for e in path_errors)

    # --- Section 1: Tree validation tests ---

    def test_nested_file_symlink_escape_produces_error(self, tmp_path):
        """A file symlink inside the candidate that escapes the root must be rejected."""
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        external_file = tmp_path / "evil.py"
        external_file.write_text("x = 1")
        pkg = modules_root / "mymod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='my.mod', label='M'))\n"
        )
        subpkg = pkg / "subpkg"
        subpkg.mkdir()
        (subpkg / "evil_link.py").symlink_to(external_file)  # escapes modules_root
        r = discover_modules(project_module_root=modules_root)
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert project_recs == []  # must not have been imported

    def test_nested_dir_symlink_escape_produces_error(self, tmp_path):
        """A directory symlink inside the candidate that escapes the root must be rejected."""
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        external_dir = tmp_path / "evil_dir"
        external_dir.mkdir()
        pkg = modules_root / "mymod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='my.mod', label='M'))\n"
        )
        (pkg / "bad_link").symlink_to(external_dir)  # dir symlink escaping root
        r = discover_modules(project_module_root=modules_root)
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert project_recs == []

    def test_nested_broken_symlink_produces_error(self, tmp_path):
        """A broken symlink inside the candidate must be rejected."""
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        pkg = modules_root / "mymod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='my.mod', label='M'))\n"
        )
        (pkg / "broken_link.py").symlink_to(tmp_path / "nonexistent")
        r = discover_modules(project_module_root=modules_root)
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert project_recs == []

    def test_nested_safe_file_symlink_is_accepted(self, tmp_path):
        """A file symlink resolving inside the root is safe."""
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        real_file = modules_root / "shared_helper.py"
        real_file.write_text("HELPER = 1")
        pkg = modules_root / "mymod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='my.mod', label='M'))\n"
        )
        (pkg / "helper_link.py").symlink_to(real_file)  # resolves inside root
        r = discover_modules(project_module_root=modules_root)
        path_errors = [e for e in r.errors if e.kind == "project_path"]
        assert path_errors == []

    def test_nested_safe_dir_symlink_is_accepted(self, tmp_path):
        """A directory symlink resolving inside the root is safe."""
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        # Place the real subdir inside the package so it's under the candidate
        pkg = modules_root / "mymod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='my.mod', label='M'))\n"
        )
        real_subdir = pkg / "real_subdir"
        real_subdir.mkdir()
        (real_subdir / "util.py").write_text("UTIL = 1")
        (pkg / "util_link").symlink_to(real_subdir)  # dir symlink inside root
        r = discover_modules(project_module_root=modules_root)
        path_errors = [e for e in r.errors if e.kind == "project_path"]
        assert path_errors == []

    def test_escaping_candidate_is_never_imported(self, tmp_path):
        """A candidate with an unsafe tree must never cause an import."""
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        external = tmp_path / "external.py"
        external.write_text("LEAKED = True")
        pkg = modules_root / "smuggler"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='smuggler.mod', label='S'))\n"
        )
        (pkg / "payload.py").symlink_to(external)
        r = discover_modules(project_module_root=modules_root)
        # Must not have been imported
        assert "smuggler" not in sys.modules
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert project_recs == []

    def test_init_py_that_is_directory_produces_error(self, tmp_path):
        """A directory named __init__.py must produce a project_path error."""
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        pkg = modules_root / "weirdmod"
        pkg.mkdir()
        init_as_dir = pkg / "__init__.py"
        init_as_dir.mkdir()  # create __init__.py as a directory, not a file
        r = discover_modules(project_module_root=modules_root)
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(errors) == 1

    def test_tree_validation_messages_contain_no_absolute_paths(self, tmp_path):
        """Error messages from tree validation must not contain absolute paths."""
        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        external = tmp_path / "external.py"
        external.write_text("x = 1")
        pkg = modules_root / "leaky"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("x=1")
        (pkg / "leak.py").symlink_to(external)
        r = discover_modules(project_module_root=modules_root)
        for e in r.errors:
            assert str(tmp_path) not in e.message, f"Absolute path in: {e.message!r}"

    # --- Section 2: PathLike normalization tests ---

    def test_pathlike_returning_empty_string_produces_error(self):
        import os
        class EmptyPathLike(os.PathLike):
            def __fspath__(self): return ""
        r = discover_modules(project_module_root=EmptyPathLike())
        assert any(e.kind == "project_path" for e in r.errors)

    def test_pathlike_returning_whitespace_produces_error(self):
        import os
        class WhitespacePathLike(os.PathLike):
            def __fspath__(self): return "   "
        r = discover_modules(project_module_root=WhitespacePathLike())
        assert any(e.kind == "project_path" for e in r.errors)

    def test_pathlike_returning_bytes_produces_error(self):
        import os
        class BytesPathLike(os.PathLike):
            def __fspath__(self): return b"/some/path"
        r = discover_modules(project_module_root=BytesPathLike())
        assert any(e.kind == "project_path" for e in r.errors)

    def test_pathlike_fspath_raising_produces_error(self):
        import os
        class RaisingPathLike(os.PathLike):
            def __fspath__(self): raise RuntimeError("fspath failed")
        r = discover_modules(project_module_root=RaisingPathLike())
        assert any(e.kind == "project_path" for e in r.errors)

    # --- Section 3: sys.path safety tests ---

    def test_malformed_sys_path_entry_does_not_break_discovery(self, tmp_path):
        from unittest.mock import patch
        _write_project_module(tmp_path, dir_name="mymod", slug="mymod.pkg")
        sys.path.insert(1, 42)  # malformed non-string entry
        # Patch entry_points to avoid Python's own metadata crashing on int in sys.path
        with patch("cauldron.modules.discovery.entry_points", return_value=[]):
            r = discover_modules(project_module_root=tmp_path)
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert len(project_recs) == 1
        # Malformed entry preserved (not removed unless it happens to match)
        assert 42 in sys.path

    # --- Section 4: sys.modules cleanup tests ---

    def test_failed_import_cleaned_from_sys_modules(self, tmp_path):
        """After a failed import, the package must not linger in sys.modules."""
        pkg = tmp_path / "badmod"
        pkg.mkdir()
        sub = pkg / "sub"
        sub.mkdir()
        (sub / "__init__.py").write_text("x = 1")
        (pkg / "__init__.py").write_text(
            "from . import sub\n"
            "raise ImportError('deliberate failure')\n"
        )
        r = discover_modules(project_module_root=tmp_path)
        errors = [e for e in r.errors if e.kind == "load_failure"]
        assert len(errors) == 1
        # Package and submodules must be gone
        assert "badmod" not in sys.modules
        assert "badmod.sub" not in sys.modules

    def test_corrected_import_succeeds_after_cleanup(self, tmp_path):
        """After cleaning a failed import, a corrected second pass must succeed."""
        pkg = tmp_path / "fixablemod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("raise ImportError('broken')")
        # First pass — fails
        r1 = discover_modules(project_module_root=tmp_path)
        assert any(e.kind == "load_failure" for e in r1.errors)
        assert "fixablemod" not in sys.modules
        # Fix the module
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='fix.mod', label='Fixed'))\n"
        )
        importlib.invalidate_caches()
        # Second pass — must succeed
        r2 = discover_modules(project_module_root=tmp_path)
        project_recs = [rec for rec in r2.records if rec.source_type == "project"]
        assert len(project_recs) == 1
        assert project_recs[0].slug == "fix.mod"

    # --- Section 1: Import-name collision tests ---

    def test_import_name_collision_with_ep_rejects_project_candidate(self, tmp_path):
        """Project candidate with same top-level import name as EP is rejected.

        The installed EP is authoritative; the project directory is rejected with
        a project_path error. The EP record has source_type='package'.
        """
        from unittest.mock import patch
        from cauldron.modules import BaseModule, ModuleManifest

        # Create project directory "shared_name" under project root
        _write_project_module(tmp_path, dir_name="shared_name", slug="pkg.from.installed")
        # EP has value "shared_name:module" — top-level import name is "shared_name"
        ep_obj = BaseModule(ModuleManifest(slug="ep.module", label="EP Module"))
        fake_ep = type("EP", (), {
            "name": "ep.module",
            "value": "shared_name:module",
            "dist": None,
            "load": lambda s: ep_obj,
        })()
        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep]):
            r = discover_modules(project_module_root=tmp_path)

        # Project candidate must be rejected (collision with EP import name)
        project_path_errors = [e for e in r.errors if e.kind == "project_path"]
        assert len(project_path_errors) >= 1
        assert any("shared_name" in e.message for e in project_path_errors)

        # No project record for "pkg.from.installed"
        project_recs = [rec for rec in r.records if rec.source_type == "project"]
        assert not any(rec.slug == "pkg.from.installed" for rec in project_recs)

        # EP record loaded successfully with source_type="package"
        pkg_recs = [rec for rec in r.records if rec.source_type == "package"]
        assert any(rec.slug == "ep.module" for rec in pkg_recs)

    def test_ep_not_shadowed_real_imports(self, tmp_path):
        """EP loads from installed root, not project root, even when both exist.

        Uses real importlib.import_module() in the EP's load() method.
        Uses two separate filesystem roots with the same package name but
        different SENTINEL values to verify which one was actually loaded.
        """
        from unittest.mock import patch
        import importlib.util

        pkg_name = "cauldron_real_shadow_test_pkg"

        # Create installed-root package (sentinel = "installed")
        installed_root = tmp_path / "installed"
        installed_root.mkdir()
        inst_pkg = installed_root / pkg_name
        inst_pkg.mkdir()
        (inst_pkg / "__init__.py").write_text(
            f'SENTINEL = "installed"\n'
            f'from cauldron.modules import BaseModule, ModuleManifest\n'
            f'module = BaseModule(ModuleManifest(slug="ep.real", label="EP Real"))\n'
        )

        # Create project-root package (sentinel = "project")
        project_root = tmp_path / "project"
        project_root.mkdir()
        proj_pkg = project_root / pkg_name
        proj_pkg.mkdir()
        (proj_pkg / "__init__.py").write_text(
            f'SENTINEL = "project"\n'
            f'from cauldron.modules import BaseModule, ModuleManifest\n'
            f'module = BaseModule(ModuleManifest(slug="proj.real", label="Proj Real"))\n'
        )

        # Add installed_root to sys.path so the EP's real import can find it
        sys.path.insert(0, str(installed_root))

        # EP with real importlib.import_module() in load()
        def ep_load(self=None):
            import importlib as _il
            return _il.import_module(pkg_name).module

        fake_ep = type("EP", (), {
            "name": "ep.real",
            "value": f"{pkg_name}:module",
            "dist": None,
            "load": ep_load,
        })()

        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep]):
            r = discover_modules(project_module_root=project_root)

        # Project candidate rejected: same import name as EP
        proj_path_errors = [e for e in r.errors if e.kind == "project_path"]
        assert any(pkg_name in e.message for e in proj_path_errors)

        # No project record
        assert not any(rec.source_type == "project" for rec in r.records)

        # EP record: source_type="package", slug="ep.real"
        pkg_recs = [rec for rec in r.records if rec.source_type == "package"]
        assert any(rec.slug == "ep.real" for rec in pkg_recs)

        # The loaded module came from installed_root (sentinel = "installed")
        loaded_mod = sys.modules.get(pkg_name)
        if loaded_mod is not None:
            assert getattr(loaded_mod, "SENTINEL", None) == "installed", (
                f"Expected installed sentinel but got: {getattr(loaded_mod, 'SENTINEL', None)}"
            )

    def test_ep_not_shadowed_when_project_already_cached(self, tmp_path):
        """Even when project-origin module is in sys.modules, EP loads installed version.

        Simulates the case where a previous pass or other code imported the project
        version of the package. The EP loading phase must temporarily evict the
        project-origin cache entry.
        """
        from unittest.mock import patch

        pkg_name = "cauldron_cached_shadow_test_pkg"

        installed_root = tmp_path / "installed"
        installed_root.mkdir()
        inst_pkg = installed_root / pkg_name
        inst_pkg.mkdir()
        (inst_pkg / "__init__.py").write_text(
            f'SENTINEL = "installed"\n'
            f'from cauldron.modules import BaseModule, ModuleManifest\n'
            f'module = BaseModule(ModuleManifest(slug="ep.cached", label="EP Cached"))\n'
        )

        project_root = tmp_path / "project"
        project_root.mkdir()
        proj_pkg = project_root / pkg_name
        proj_pkg.mkdir()
        (proj_pkg / "__init__.py").write_text(
            f'SENTINEL = "project"\n'
            f'from cauldron.modules import BaseModule, ModuleManifest\n'
            f'module = BaseModule(ModuleManifest(slug="proj.cached", label="Proj Cached"))\n'
        )

        sys.path.insert(0, str(installed_root))

        # Pre-import the project version to simulate cached state
        sys.path.insert(0, str(project_root))
        try:
            proj_mod = importlib.import_module(pkg_name)
            assert proj_mod.SENTINEL == "project"
        finally:
            sys.path.remove(str(project_root))

        def ep_load(self=None):
            import importlib as _il
            return _il.import_module(pkg_name).module

        fake_ep = type("EP", (), {
            "name": "ep.cached",
            "value": f"{pkg_name}:module",
            "dist": None,
            "load": ep_load,
        })()

        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep]):
            r = discover_modules(project_module_root=project_root)

        # Project candidate rejected (same import name as EP)
        assert any(e.kind == "project_path" and pkg_name in e.message for e in r.errors)

        # EP loads successfully with installed sentinel
        pkg_recs = [rec for rec in r.records if rec.slug == "ep.cached"]
        assert len(pkg_recs) == 1
        assert pkg_recs[0].source_type == "package"

        loaded_mod = sys.modules.get(pkg_name)
        if loaded_mod is not None:
            assert getattr(loaded_mod, "SENTINEL", None) == "installed"

    def test_failed_ep_restores_project_cache(self, tmp_path):
        """On EP load failure, the previously cached project-origin module is restored."""
        from unittest.mock import patch

        pkg_name = "cauldron_restore_test_pkg"

        project_root = tmp_path / "project"
        project_root.mkdir()
        proj_pkg = project_root / pkg_name
        proj_pkg.mkdir()
        (proj_pkg / "__init__.py").write_text(
            f'SENTINEL = "project"\n'
            f'from cauldron.modules import BaseModule, ModuleManifest\n'
            f'module = BaseModule(ModuleManifest(slug="proj.restore", label="Proj Restore"))\n'
        )

        # Pre-import the project version
        sys.path.insert(0, str(project_root))
        try:
            proj_mod = importlib.import_module(pkg_name)
        finally:
            sys.path.remove(str(project_root))

        # EP with same import name that fails to load
        def bad_ep_load(self=None):
            raise ImportError("intentional failure")

        fake_ep = type("EP", (), {
            "name": "ep.fail",
            "value": f"{pkg_name}:module",
            "dist": None,
            "load": bad_ep_load,
        })()

        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep]):
            r = discover_modules(project_module_root=project_root)

        # EP failed
        assert any(e.kind == "load_failure" for e in r.errors)

        # Project candidate rejected (import-name collision)
        assert any(e.kind == "project_path" and pkg_name in e.message for e in r.errors)

        # The cached project-origin module was restored after EP failure
        assert sys.modules.get(pkg_name) is proj_mod, (
            "Project-origin cached module should be restored after EP failure"
        )

    def test_two_ep_duplicates_with_project_winner(self, tmp_path):
        """Two EPs share a slug that is also claimed by a project module.

        Both EP duplicate errors must name the project record as the accepted source.
        """
        from unittest.mock import patch
        from cauldron.modules import BaseModule, ModuleManifest

        # Project module with slug "shared.slug"
        _write_project_module(tmp_path, dir_name="projmod", slug="shared.slug")

        # Two EPs with same slug, different import names (no import-name collision)
        ep_a = BaseModule(ModuleManifest(slug="shared.slug", label="EP A"))
        ep_b = BaseModule(ModuleManifest(slug="shared.slug", label="EP B"))

        fake_ep_a = type("EP", (), {
            "name": "shared.slug",
            "value": "ep_pkg_a:module",  # different import name from "projmod"
            "dist": None,
            "load": lambda s: ep_a,
        })()
        fake_ep_b = type("EP", (), {
            "name": "shared.slug",
            "value": "ep_pkg_b:module",  # different import name from "projmod"
            "dist": None,
            "load": lambda s: ep_b,
        })()

        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep_a, fake_ep_b]):
            r = discover_modules(project_module_root=tmp_path)

        # Project record wins
        assert len(r.records) == 1
        assert r.records[0].source_type == "project"
        assert r.records[0].slug == "shared.slug"

        # Both EPs get duplicate errors naming the project record
        dup_errors = [e for e in r.errors if e.kind == "duplicate_slug"]
        assert len(dup_errors) == 2
        for err in dup_errors:
            # accepted_entry_point_name should be "modules/projmod" (the project EP name)
            assert err.accepted_entry_point_name == "modules/projmod", (
                f"Expected accepted_entry_point_name='modules/projmod', got {err.accepted_entry_point_name!r}"
            )
            assert err.accepted_package_name == ""  # project modules have no package name

    def test_real_symlink_loop_produces_project_path_error(self, tmp_path):
        """A directory-level symlink loop (if the platform supports it) produces a project_path error."""
        import os as _os

        modules_root = tmp_path / "mods"
        modules_root.mkdir()
        pkg = modules_root / "loopmod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='loop.mod', label='L'))\n"
        )

        # Attempt to create a real self-referential directory symlink
        loop_link = pkg / "loop_back"
        try:
            _os.symlink(str(pkg), str(loop_link))
        except OSError:
            pytest.skip("Cannot create directory symlink on this platform")

        # With followlinks=False in os.walk, the loop_link is listed as a directory.
        # _validate_candidate_tree checks it: resolve() on a symlink pointing to pkg
        # will succeed (no actual infinite loop), but all dir symlinks are removed from traversal.
        # The test verifies the module still loads (no error for safe internal loop).
        r = discover_modules(project_module_root=modules_root)
        # No project_path error for the internal symlink (it's safe)
        assert any(rec.slug == "loop.mod" for rec in r.records), (
            f"Expected loop.mod to load. Errors: {r.errors}"
        )

        # Now create an escaping symlink
        escaping_link = pkg / "escape_link"
        try:
            _os.symlink(str(tmp_path.parent), str(escaping_link))
        except OSError:
            pytest.skip("Cannot create escaping symlink")

        # Clear the previous import state
        for key in list(sys.modules.keys()):
            if key == "loopmod" or key.startswith("loopmod."):
                del sys.modules[key]

        r2 = discover_modules(project_module_root=modules_root)
        errors = [e for e in r2.errors if e.kind == "project_path"]
        assert errors, f"Expected project_path error for escaping symlink"
        assert not any(rec.slug == "loop.mod" for rec in r2.records)

    # --- Section 2: _resolve_safely and os.walk error callback tests ---

    def test_symlink_loop_nested_dir_produces_project_path_error(self, tmp_path):
        """A nested directory symlink loop that makes _resolve_safely return None produces an error.

        Uses patch to simulate a symlink (via os.path.islink) and an unresolvable
        path (via _resolve_safely returning None) without creating actual filesystem loops.
        """
        from unittest.mock import patch
        from cauldron.modules.discovery import _resolve_safely as original_resolve
        import os as _os

        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        pkg = modules_root / "loopmod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='loop.mod', label='L'))\n"
        )
        subdir = pkg / "subdir"
        subdir.mkdir()
        # Create a symlink inside subdir that points to itself (os-level symlink)
        loop_link = pkg / "loop_link"
        try:
            _os.symlink(str(pkg), str(loop_link))
        except OSError:
            pytest.skip("Cannot create symlink on this platform")

        def resolve_returning_none(p):
            # Simulate the loop_link being unresolvable
            if "loop_link" in str(p):
                return None
            return original_resolve(p)

        with patch("cauldron.modules.discovery._resolve_safely", side_effect=resolve_returning_none):
            r = discover_modules(project_module_root=modules_root)

        # The loop_link symlink should produce a project_path error
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert errors
        assert not any(rec.source_type == "project" for rec in r.records)

    def test_symlink_loop_nested_file_produces_project_path_error(self, tmp_path):
        """A nested file symlink that makes _resolve_safely return None produces an error."""
        from unittest.mock import patch
        from cauldron.modules.discovery import _resolve_safely as original_resolve
        import os as _os

        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        pkg = modules_root / "loopmod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='loop.mod', label='L'))\n"
        )
        # Create a file symlink that points to itself (self-referential)
        loop_file = pkg / "loop.py"
        try:
            _os.symlink(str(loop_file), str(loop_file))
        except OSError:
            # Can't create self-referential symlink; create any symlink and patch
            real_file = tmp_path / "real.py"
            real_file.write_text("x = 1")
            _os.symlink(str(real_file), str(loop_file))

        def resolve_returning_none(p):
            if "loop.py" in str(p) or "loopfile" in str(p):
                return None
            return original_resolve(p)

        with patch("cauldron.modules.discovery._resolve_safely", side_effect=resolve_returning_none):
            r = discover_modules(project_module_root=modules_root)

        # Tree validation should catch the unresolvable file symlink
        errors = [e for e in r.errors if e.kind == "project_path"]
        assert errors  # at least one project_path error produced
        assert not any(rec.source_type == "project" for rec in r.records)

    def test_project_root_itself_is_symlink_loop(self, tmp_path):
        """Project root that _resolve_safely cannot resolve → project_path error."""
        from unittest.mock import patch
        from cauldron.modules.discovery import _resolve_safely as original_resolve

        modules_root = tmp_path / "modules"
        modules_root.mkdir()

        def resolve_returning_none_for_root(p):
            if str(p) == str(modules_root):
                return None
            return original_resolve(p)

        with patch("cauldron.modules.discovery._resolve_safely",
                   side_effect=resolve_returning_none_for_root):
            r = discover_modules(project_module_root=modules_root)

        errors = [e for e in r.errors if e.kind == "project_path"]
        assert errors
        assert not any(rec.source_type == "project" for rec in r.records)

    def test_unresolvable_sys_path_entry_does_not_crash(self, tmp_path):
        """An invalid/unresolvable entry in sys.path must not crash discovery."""
        from unittest.mock import patch

        _write_project_module(tmp_path, dir_name="mymod", slug="mymod.pkg")
        # Add an unresolvable path that will cause _resolve_safely to return None
        unresolvable = "\x00invalid\x00path"
        sys.path.insert(0, unresolvable)
        try:
            with patch("cauldron.modules.discovery.entry_points", return_value=[]):
                r = discover_modules(project_module_root=tmp_path)
            project_recs = [rec for rec in r.records if rec.source_type == "project"]
            assert len(project_recs) == 1
        finally:
            if unresolvable in sys.path:
                sys.path.remove(unresolvable)

    def test_unresolvable_module_origin_is_rejected(self, tmp_path):
        """Module with __file__ that _resolve_safely returns None for is rejected."""
        import types
        from unittest.mock import patch
        from cauldron.modules.discovery import _resolve_safely as original_resolve

        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        pkg = modules_root / "originmod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='origin.mod', label='O'))\n"
        )

        def resolve_unresolvable_file(p):
            if "originmod" in str(p) and "__file__" in str(p):
                return None
            return original_resolve(p)

        # Inject fake module with __file__ that can't be resolved
        fake_mod = types.ModuleType("originmod")
        fake_mod.__file__ = "/\x00invalid/__init__.py"
        sys.modules["originmod"] = fake_mod
        try:
            with patch("cauldron.modules.discovery.entry_points", return_value=[]):
                r = discover_modules(project_module_root=modules_root)
            # originmod already in sys.modules but with bad __file__: rejected
            errors = [e for e in r.errors if e.kind == "project_path"]
            assert errors
            assert not any(rec.source_type == "project" for rec in r.records)
        finally:
            sys.modules.pop("originmod", None)

    def test_project_path_errors_contain_no_absolute_paths_section2(self, tmp_path):
        """Errors from _resolve_safely-based checks must not contain absolute paths."""
        from unittest.mock import patch
        from cauldron.modules.discovery import _resolve_safely as original_resolve

        modules_root = tmp_path / "modules"
        modules_root.mkdir()
        pkg = modules_root / "escapemod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("x = 1")

        # Simulate resolve returning None for the candidate (unresolvable)
        def resolve_none_for_candidate(p):
            if "escapemod" in str(p) and str(p) != str(pkg / "__init__.py"):
                return None
            return original_resolve(p)

        with patch("cauldron.modules.discovery._resolve_safely",
                   side_effect=resolve_none_for_candidate):
            r = discover_modules(project_module_root=modules_root)

        for e in r.errors:
            assert str(tmp_path) not in e.message, (
                f"Absolute path leaked: {e.message!r}"
            )


# ---------------------------------------------------------------------------
# Packaging transition (#34 / #33 interop)
# ---------------------------------------------------------------------------

class TestPackagingTransition:
    """Same module code works both as a project module and as a packaged entry point."""

    @pytest.fixture(autouse=True)
    def _clean_import_state(self):
        original_path = list(sys.path)
        original_modules = set(sys.modules.keys())
        yield
        sys.path[:] = original_path
        for key in list(sys.modules.keys()):
            if key not in original_modules:
                del sys.modules[key]

    def test_same_manifest_in_both_forms(self, tmp_path):
        """Identical __init__.py bytes produce equal manifests in project and package forms.

        After the project-form pass, sys.modules is cleared for the package and the
        project root is removed from sys.path.  The packaged form loads via real
        importlib.import_module() from the installed root.
        """
        from unittest.mock import patch

        INIT_CODE = (
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "SENTINEL = __file__\n"
            "module = BaseModule(ModuleManifest(\n"
            "    slug='trans.mod', label='Transition Module', version='2.0.0',\n"
            "    django_apps=('transmod',),\n"
            "))\n"
        )

        # Project form
        project_root = tmp_path / "project"
        project_root.mkdir()
        proj_pkg = project_root / "transmod"
        proj_pkg.mkdir()
        (proj_pkg / "__init__.py").write_text(INIT_CODE)

        # Installed form (different directory, same code)
        installed_root = tmp_path / "installed"
        installed_root.mkdir()
        inst_pkg = installed_root / "transmod"
        inst_pkg.mkdir()
        (inst_pkg / "__init__.py").write_text(INIT_CODE)

        # --- Project form pass ---
        proj_result = discover_modules(project_module_root=project_root)
        proj_recs = [r for r in proj_result.records if r.slug == "trans.mod"]
        assert len(proj_recs) == 1, proj_result.errors
        proj_rec = proj_recs[0]
        assert proj_rec.source_type == "project"
        assert proj_rec.project_path == "transmod"
        proj_sentinel = getattr(sys.modules.get("transmod"), "SENTINEL", None)

        # --- Clear project-form state ---
        for key in list(sys.modules.keys()):
            if key == "transmod" or key.startswith("transmod."):
                del sys.modules[key]
        importlib.invalidate_caches()

        # --- Installed form pass ---
        sys.path.insert(0, str(installed_root))

        def ep_load(self=None):
            return importlib.import_module("transmod").module

        fake_ep = type("EP", (), {
            "name": "trans.mod",
            "value": "transmod:module",
            "dist": None,
            "load": ep_load,
        })()
        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep]):
            pkg_result = discover_modules()  # no project_module_root

        pkg_recs = [r for r in pkg_result.records if r.slug == "trans.mod"]
        assert len(pkg_recs) == 1, pkg_result.errors
        pkg_rec = pkg_recs[0]
        assert pkg_rec.source_type == "package"
        assert pkg_rec.project_path == ""

        # The two module objects are NOT the same cached object (different imports)
        inst_sentinel = getattr(sys.modules.get("transmod"), "SENTINEL", None)
        assert proj_sentinel != inst_sentinel, (
            "Project and installed sentinels should differ (different __file__)"
        )

        # Manifests are equal
        assert proj_rec.manifest.slug == pkg_rec.manifest.slug
        assert proj_rec.manifest.label == pkg_rec.manifest.label
        assert proj_rec.manifest.version == pkg_rec.manifest.version
        assert proj_rec.manifest.django_apps == pkg_rec.manifest.django_apps

    def test_both_in_same_pass_produces_duplicate_error(self, tmp_path):
        """Loading project + packaged versions of same slug produces a duplicate error.

        The EP uses a different top-level import name than the project directory
        so that import-name collision detection does not apply — only slug-race
        duplicate detection fires.
        """
        from unittest.mock import patch
        from cauldron.modules import BaseModule, ModuleManifest

        pkg_obj = BaseModule(ModuleManifest(slug="trans.mod", label="Transition Module"))

        pkg = tmp_path / "transmod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(slug='trans.mod', label='Transition Module'))\n"
        )

        # EP value uses "transmod_installed" (not "transmod") to avoid
        # import-name collision with the project directory "transmod".
        fake_ep = type("EP", (), {
            "name": "trans.mod",
            "value": "transmod_installed:module",
            "dist": None,
            "load": lambda s: pkg_obj,
        })()
        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep]):
            r = discover_modules(project_module_root=tmp_path)

        # Project wins (first), package gets duplicate error
        dup_errors = [e for e in r.errors if e.kind == "duplicate_slug"]
        assert len(dup_errors) == 1
        winner = next(rec for rec in r.records if rec.slug == "trans.mod")
        assert winner.source_type == "project"

    def test_project_path_empty_for_packaged_form(self, tmp_path):
        """Packaged discovery must not set project_path."""
        from unittest.mock import patch
        from cauldron.modules import BaseModule, ModuleManifest

        pkg_obj = BaseModule(ModuleManifest(slug="pkg.only", label="Package Only"))
        fake_ep = type("EP", (), {
            "name": "pkg.only",
            "value": "pkgonly:module",
            "dist": None,
            "load": lambda s: pkg_obj,
        })()
        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep]):
            r = discover_modules(project_module_root=tmp_path)
        recs = [rec for rec in r.records if rec.slug == "pkg.only"]
        assert len(recs) == 1
        assert recs[0].project_path == ""
        assert recs[0].source_type == "package"

    def test_same_django_apps_in_both_forms(self, tmp_path):
        """manifest.django_apps is identical between project and package forms."""
        from unittest.mock import patch

        INIT_CODE = (
            "from cauldron.modules import BaseModule, ModuleManifest\n"
            "module = BaseModule(ModuleManifest(\n"
            "    slug='apps.mod', label='Apps Module',\n"
            "    django_apps=('myapp_a', 'myapp_b'),\n"
            "))\n"
        )

        # Project form (separate directory)
        project_root = tmp_path / "project"
        project_root.mkdir()
        proj_pkg = project_root / "appsmod"
        proj_pkg.mkdir()
        (proj_pkg / "__init__.py").write_text(INIT_CODE)

        # Installed form (different directory)
        installed_root = tmp_path / "installed"
        installed_root.mkdir()
        inst_pkg = installed_root / "appsmod"
        inst_pkg.mkdir()
        (inst_pkg / "__init__.py").write_text(INIT_CODE)

        # Project form pass
        proj_result = discover_modules(project_module_root=project_root)
        proj_recs = [r for r in proj_result.records if r.slug == "apps.mod"]
        assert len(proj_recs) == 1
        proj_apps = proj_recs[0].manifest.django_apps

        # Clear project-form state
        for key in list(sys.modules.keys()):
            if key == "appsmod" or key.startswith("appsmod."):
                del sys.modules[key]
        importlib.invalidate_caches()

        # Installed form pass — EP load() uses installed_root
        sys.path.insert(0, str(installed_root))

        def ep_load(self=None):
            return importlib.import_module("appsmod").module

        fake_ep = type("EP", (), {
            "name": "apps.mod",
            "value": "appsmod:module",
            "dist": None,
            "load": ep_load,
        })()
        with patch("cauldron.modules.discovery.entry_points", return_value=[fake_ep]):
            pkg_result = discover_modules()
        pkg_recs = [r for r in pkg_result.records if r.slug == "apps.mod"]
        assert len(pkg_recs) == 1
        pkg_apps = pkg_recs[0].manifest.django_apps

        assert proj_apps == pkg_apps, (
            f"django_apps differ: project={proj_apps!r}, package={pkg_apps!r}"
        )
