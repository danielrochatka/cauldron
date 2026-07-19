"""Tests for dependency resolution, topological ordering, and validation."""

import pytest

from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement
from cauldron.modules.resolver import ErrorKind, resolve


def _mod(slug, *, version="1.0.0", cauldron_version="", requires=(), optional=(), provides=()):
    manifest = ModuleManifest(
        slug=slug,
        label=slug,
        version=version,
        cauldron_version=cauldron_version,
        requires=requires,
        optional=optional,
        provides=provides,
    )
    return BaseModule(manifest)


class TestTopologicalOrdering:
    def test_single_module_no_deps(self):
        a = _mod("a")
        result = resolve([a], {})
        assert result.load_order == ["a"]
        assert not result.has_errors

    def test_two_modules_correct_order(self):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        result = resolve([a, b], {})
        assert result.load_order.index("a") < result.load_order.index("b")
        assert not result.has_errors

    def test_linear_chain_abc(self):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        c = _mod("c", requires=(ModuleRequirement(slug="b"),))
        result = resolve([a, b, c], {})
        order = result.load_order
        assert order.index("a") < order.index("b") < order.index("c")
        assert not result.has_errors

    def test_diamond_dependency(self):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        c = _mod("c", requires=(ModuleRequirement(slug="a"),))
        d = _mod("d", requires=(ModuleRequirement(slug="b"), ModuleRequirement(slug="c")))
        result = resolve([a, b, c, d], {})
        order = result.load_order
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")
        assert not result.has_errors

    def test_deterministic_across_calls(self):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        c = _mod("c", requires=(ModuleRequirement(slug="a"),))
        r1 = resolve([a, b, c], {})
        r2 = resolve([a, b, c], {})
        assert r1.load_order == r2.load_order


class TestCircularDependencyDetection:
    def test_two_module_cycle(self):
        a = _mod("a", requires=(ModuleRequirement(slug="b"),))
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        result = resolve([a, b], {})
        cycle_errors = [e for e in result.errors if e.kind == ErrorKind.CIRCULAR_DEPENDENCY]
        assert len(cycle_errors) == 2
        cycle_slugs = {e.module_slug for e in cycle_errors}
        assert cycle_slugs == {"a", "b"}

    def test_three_module_cycle(self):
        a = _mod("a", requires=(ModuleRequirement(slug="c"),))
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        c = _mod("c", requires=(ModuleRequirement(slug="b"),))
        result = resolve([a, b, c], {})
        cycle_errors = [e for e in result.errors if e.kind == ErrorKind.CIRCULAR_DEPENDENCY]
        assert len(cycle_errors) == 3

    def test_cycle_with_safe_module(self):
        safe = _mod("safe")
        a = _mod("a", requires=(ModuleRequirement(slug="b"),))
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        result = resolve([safe, a, b], {})
        cycle_slugs = {e.module_slug for e in result.errors if e.kind == ErrorKind.CIRCULAR_DEPENDENCY}
        assert cycle_slugs == {"a", "b"}
        assert "safe" in result.load_order


class TestMissingDependencyDetection:
    def test_missing_required_module(self):
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        result = resolve([b], {})
        errors = [e for e in result.errors if e.kind == ErrorKind.MISSING_DEPENDENCY]
        assert len(errors) == 1
        assert errors[0].module_slug == "b"
        assert "a" in errors[0].message

    def test_missing_required_capability(self):
        b = _mod("b", requires=(ModuleRequirement(slug="some.cap", kind="capability"),))
        result = resolve([b], {})
        errors = [e for e in result.errors if e.kind == ErrorKind.MISSING_CAPABILITY]
        assert len(errors) == 1
        assert errors[0].module_slug == "b"

    def test_optional_missing_capability_is_not_an_error(self):
        b = _mod("b", optional=(ModuleRequirement(slug="missing.cap", kind="capability"),))
        result = resolve([b], {})
        assert not result.has_errors

    def test_optional_missing_module_is_not_an_error(self):
        b = _mod("b", optional=(ModuleRequirement(slug="missing.module"),))
        result = resolve([b], {})
        assert not result.has_errors


class TestVersionConstraints:
    def test_required_version_satisfied(self):
        a = _mod("a", version="2.0.0")
        b = _mod("b", requires=(ModuleRequirement(slug="a", version=">=2.0.0"),))
        result = resolve([a, b], {})
        assert not result.has_errors

    def test_required_version_not_satisfied(self):
        a = _mod("a", version="1.5.0")
        b = _mod("b", requires=(ModuleRequirement(slug="a", version=">=2.0.0"),))
        result = resolve([a, b], {})
        errors = [e for e in result.errors if e.kind == ErrorKind.VERSION_CONSTRAINT]
        assert len(errors) == 1
        assert errors[0].module_slug == "b"

    def test_optional_dep_version_mismatch_is_warning(self):
        a = _mod("a", version="1.0.0")
        b = _mod("b", optional=(ModuleRequirement(slug="a", version=">=2.0.0"),))
        result = resolve([a, b], {})
        assert not result.has_errors
        assert any("a" in w.message for w in result.warnings)

    def test_version_constraint_with_upper_bound(self):
        a = _mod("a", version="3.0.0")
        b = _mod("b", requires=(ModuleRequirement(slug="a", version=">=2.0.0,<4.0.0"),))
        result = resolve([a, b], {})
        assert not result.has_errors

    def test_version_upper_bound_violated(self):
        a = _mod("a", version="5.0.0")
        b = _mod("b", requires=(ModuleRequirement(slug="a", version=">=2.0.0,<4.0.0"),))
        result = resolve([a, b], {})
        errors = [e for e in result.errors if e.kind == ErrorKind.VERSION_CONSTRAINT]
        assert len(errors) == 1


class TestCauldronVersionCompatibility:
    def test_compatible_cauldron_version(self):
        a = _mod("a", cauldron_version=">=0.1.0")
        result = resolve([a], {}, cauldron_version="0.1.0")
        assert not result.has_errors

    def test_incompatible_cauldron_version(self):
        a = _mod("a", cauldron_version=">=1.0.0")
        result = resolve([a], {}, cauldron_version="0.1.0")
        errors = [e for e in result.errors if e.kind == ErrorKind.CAULDRON_VERSION]
        assert len(errors) == 1
        assert errors[0].module_slug == "a"

    def test_no_cauldron_version_constraint_always_passes(self):
        a = _mod("a", cauldron_version="")
        result = resolve([a], {}, cauldron_version="0.1.0")
        assert not result.has_errors

    def test_cauldron_version_not_checked_when_omitted(self):
        a = _mod("a", cauldron_version=">=99.0.0")
        result = resolve([a], {})  # no cauldron_version kwarg
        cauldron_errors = [e for e in result.errors if e.kind == ErrorKind.CAULDRON_VERSION]
        assert not cauldron_errors


class TestCapabilityResolution:
    def test_capability_dep_resolved_to_provider(self):
        provider = _mod("provider", provides=("some.cap",))
        consumer = _mod("consumer", requires=(ModuleRequirement(slug="some.cap", kind="capability"),))
        caps = {"some.cap": ["provider"]}
        result = resolve([provider, consumer], caps)
        assert not result.has_errors
        assert result.load_order.index("provider") < result.load_order.index("consumer")

    def test_multiple_capability_providers_all_precede_consumer(self):
        p1 = _mod("p1", provides=("shared.cap",))
        p2 = _mod("p2", provides=("shared.cap",))
        consumer = _mod("consumer", requires=(ModuleRequirement(slug="shared.cap", kind="capability"),))
        caps = {"shared.cap": ["p1", "p2"]}
        result = resolve([p1, p2, consumer], caps)
        assert not result.has_errors
        order = result.load_order
        assert order.index("p1") < order.index("consumer")
        assert order.index("p2") < order.index("consumer")

    def test_dep_graph_includes_capability_resolved_slugs(self):
        provider = _mod("provider", provides=("cap",))
        consumer = _mod("consumer", requires=(ModuleRequirement(slug="cap", kind="capability"),))
        caps = {"cap": ["provider"]}
        result = resolve([provider, consumer], caps)
        assert "provider" in result.dep_graph.get("consumer", [])
