"""Tests for the cauldron.django.auth module manifest and dependency resolution."""
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


def test_auth_module_discoverable():
    result = discover_modules()
    slugs = [m.slug for m in result.modules]
    assert "cauldron.django.auth" in slugs


def test_auth_requires_state():
    auth = _get_module("cauldron.django.auth")
    assert auth is not None
    req_slugs = [r.slug for r in auth.manifest.requires]
    assert "cauldron.django.state" in req_slugs


def test_state_only_resolves_without_errors():
    state = _get_module("cauldron.django.state")
    active = [state]
    cap_map = _build_cap_map(active)
    result = resolve(active, cap_map)
    assert not result.has_errors


def test_auth_alone_fails_missing_state():
    """Auth without state → missing dependency error."""
    auth = _get_module("cauldron.django.auth")
    active = [auth]
    cap_map = _build_cap_map(active)
    result = resolve(active, cap_map)
    assert result.has_errors
    kinds = [e.kind for e in result.errors]
    assert ErrorKind.MISSING_DEPENDENCY in kinds


def test_state_and_auth_resolve_correctly():
    state = _get_module("cauldron.django.state")
    auth = _get_module("cauldron.django.auth")
    active = [state, auth]
    cap_map = _build_cap_map(active)
    result = resolve(active, cap_map)
    assert not result.has_errors
    order = result.load_order
    assert order.index("cauldron.django.state") < order.index("cauldron.django.auth")


def test_auth_provides_identity_capabilities():
    auth = _get_module("cauldron.django.auth")
    provides = set(auth.manifest.provides)
    assert "identity.users" in provides
    assert "identity.authentication" in provides
    assert "identity.sessions" in provides


def test_auth_has_middleware():
    auth = _get_module("cauldron.django.auth")
    mw = auth.manifest.django_middleware
    assert "django.contrib.sessions.middleware.SessionMiddleware" in mw
    assert "django.contrib.auth.middleware.AuthenticationMiddleware" in mw


def test_auth_has_context_processors():
    auth = _get_module("cauldron.django.auth")
    cp = auth.manifest.django_context_processors
    assert "django.contrib.auth.context_processors.auth" in cp
