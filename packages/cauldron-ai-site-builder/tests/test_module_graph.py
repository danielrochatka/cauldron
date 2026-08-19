"""Module graph regression test for the Admin AI site-builder vertical.

Proves that enabling the full site-builder module set together does not
produce E014 (circular dependency) or E016 (blocked dependency) errors.
The resolver is the authoritative check — this test must not weaken it.
"""
from __future__ import annotations

import pytest

from cauldron.modules.resolver import ErrorKind, resolve


def _load_module(dotted_path: str):
    module_path, attr = dotted_path.rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, attr)


# All six real module manifests for the site-builder vertical.
_MODULE_PATHS = [
    "cauldron_ai.module:module",
    "cauldron_ai_admin.module:module",
    "cauldron_ai_attachments.module:module",
    "cauldron_ai_web.module:module",
    "cauldron_ai_site_builder.module:module",
    "cauldron_site_astro.module:module",
]


def test_site_builder_module_graph_has_no_cycles():
    """The complete site-builder vertical must resolve without E014 or E016 errors.

    Modules under test:
        cauldron.ai
        cauldron.ai.admin
        cauldron.ai.attachments
        cauldron.ai.web
        cauldron.ai.sitebuilder
        cauldron.site.astro

    This is a regression guard: any change that re-introduces a cycle between
    cauldron.ai.admin and cauldron.ai.attachments (or any other pair) must
    cause this test to fail.
    """
    modules = [_load_module(p) for p in _MODULE_PATHS]
    result = resolve(modules, {})

    cycle_errors = [e for e in result.errors if e.kind == ErrorKind.CIRCULAR_DEPENDENCY]
    blocked_errors = [e for e in result.errors if e.kind == ErrorKind.BLOCKED_DEPENDENCY]

    assert not cycle_errors, (
        "Circular dependency detected among site-builder modules "
        f"(E014): {[e.module_slug for e in cycle_errors]}"
    )
    assert not blocked_errors, (
        "Blocked dependency detected among site-builder modules "
        f"(E016): {[e.module_slug for e in blocked_errors]}"
    )

    # Verify all six modules appear in the load order.
    expected_slugs = {
        "cauldron.ai",
        "cauldron.ai.admin",
        "cauldron.ai.attachments",
        "cauldron.ai.web",
        "cauldron.ai.sitebuilder",
        "cauldron.site.astro",
    }
    resolved_slugs = set(result.load_order)
    missing = expected_slugs - resolved_slugs
    assert not missing, f"Modules missing from load order: {missing}"


def test_admin_does_not_depend_on_attachments():
    """cauldron.ai.admin must not declare cauldron.ai.attachments as a dependency.

    This guards the specific cycle that was introduced and fixed: Admin AI
    declaring an optional dependency on the attachments package while
    attachments already optionally depends on Admin AI.
    """
    admin_module = _load_module("cauldron_ai_admin.module:module")
    manifest = admin_module.manifest

    all_deps = list(manifest.requires) + list(manifest.optional)
    dep_slugs = {req.slug for req in all_deps}

    assert "cauldron.ai.attachments" not in dep_slugs, (
        "cauldron.ai.admin must not declare cauldron.ai.attachments as a "
        "dependency (required or optional) — this creates a circular module "
        "dependency with cauldron.ai.attachments → optional cauldron.ai.admin."
    )
