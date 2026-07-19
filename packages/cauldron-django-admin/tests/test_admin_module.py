"""Tests for the cauldron.django.admin module manifest and dependency resolution."""
import pytest
from cauldron.modules.discovery import discover_modules
from cauldron.modules.resolver import resolve, ErrorKind


def _get_module(slug):
    result = discover_modules()
    for m in result.modules:
        if m.slug == slug:
            return m
    return None


def _build_cap_map(modules):
    cap_map = {}
    for m in modules:
        for cap in m.manifest.provides:
            cap_map.setdefault(cap, []).append(m.slug)
    return cap_map


def test_admin_module_discoverable():
    result = discover_modules()
    slugs = [m.slug for m in result.modules]
    assert "cauldron.django.admin" in slugs


def test_admin_requires_auth():
    admin_mod = _get_module("cauldron.django.admin")
    assert admin_mod is not None
    req_slugs = [r.slug for r in admin_mod.manifest.requires]
    assert "cauldron.django.auth" in req_slugs


def test_auth_only_resolves_without_errors():
    state = _get_module("cauldron.django.state")
    auth = _get_module("cauldron.django.auth")
    active = [state, auth]
    cap_map = _build_cap_map(active)
    result = resolve(active, cap_map)
    assert not result.has_errors


def test_admin_alone_fails_missing_auth():
    """Admin without auth → missing dependency error."""
    admin_mod = _get_module("cauldron.django.admin")
    active = [admin_mod]
    cap_map = _build_cap_map(active)
    result = resolve(active, cap_map)
    assert result.has_errors
    kinds = [e.kind for e in result.errors]
    assert ErrorKind.MISSING_DEPENDENCY in kinds


def test_state_auth_admin_resolves_correctly():
    state = _get_module("cauldron.django.state")
    auth = _get_module("cauldron.django.auth")
    admin_mod = _get_module("cauldron.django.admin")
    active = [state, auth, admin_mod]
    cap_map = _build_cap_map(active)
    result = resolve(active, cap_map)
    assert not result.has_errors
    order = result.load_order
    assert order.index("cauldron.django.state") < order.index("cauldron.django.auth")
    assert order.index("cauldron.django.auth") < order.index("cauldron.django.admin")


def test_admin_provides_capabilities():
    admin_mod = _get_module("cauldron.django.admin")
    provides = set(admin_mod.manifest.provides)
    assert "admin.interface" in provides
    assert "admin.users" in provides


def test_admin_has_middleware():
    admin_mod = _get_module("cauldron.django.admin")
    mw = admin_mod.manifest.django_middleware
    assert "django.contrib.messages.middleware.MessageMiddleware" in mw


def test_admin_has_context_processors():
    admin_mod = _get_module("cauldron.django.admin")
    cp = admin_mod.manifest.django_context_processors
    assert "django.contrib.messages.context_processors.messages" in cp
