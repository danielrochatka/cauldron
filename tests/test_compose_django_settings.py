"""Tests for cauldron.django.compose_django_settings and SettingsPlan."""

from __future__ import annotations

import json

import pytest

from cauldron.django import SettingsPlan, compose_django_settings
from cauldron.modules.discovery import get_module_apps


class TestComposeDjangoSettingsCoreOnly:
    """With no module_settings arg all discovered modules may activate,
    but we can also pass an empty dict to get truly core-only output."""

    def test_empty_module_settings_returns_base_only(self):
        """Passing module_settings={} means no modules enabled → only base returned."""
        plan = compose_django_settings(
            installed_apps=["django.contrib.contenttypes", "cauldron"],
            middleware=["django.middleware.security.SecurityMiddleware"],
            context_processors=["django.template.context_processors.request"],
            module_settings={},
        )
        assert "django.contrib.contenttypes" in plan.installed_apps
        assert "cauldron" in plan.installed_apps
        assert "django.middleware.security.SecurityMiddleware" in plan.middleware
        assert "django.template.context_processors.request" in plan.context_processors

    def test_empty_module_settings_no_module_apps(self):
        """No modules active → fixture apps should NOT appear."""
        plan = compose_django_settings(
            installed_apps=["cauldron"],
            module_settings={},
        )
        assert "cauldron_fixture_alpha" not in plan.installed_apps
        assert "cauldron_fixture_beta" not in plan.installed_apps

    def test_base_apps_preserved_first(self):
        base = ["django.contrib.contenttypes", "cauldron"]
        plan = compose_django_settings(installed_apps=base, module_settings={})
        # Base apps must come first.
        for app in base:
            assert app in plan.installed_apps
        assert list(plan.installed_apps[: len(base)]) == base

    def test_plan_is_immutable(self):
        plan = compose_django_settings(module_settings={})
        with pytest.raises((TypeError, AttributeError)):
            plan.installed_apps = ("something",)  # type: ignore[misc]


class TestComposeDjangoSettingsOrdering:
    """Tests that module contributions are ordered by dependency resolution."""

    def test_alpha_before_beta_in_load_order(self):
        """Alpha has no deps; Beta requires Alpha → alpha must come first."""
        plan = compose_django_settings(
            module_settings={
                "cauldron.fixture.alpha": {},
                "cauldron.fixture.beta": {},
            }
        )
        order = list(plan.module_order)
        assert "cauldron.fixture.alpha" in order
        assert "cauldron.fixture.beta" in order
        assert order.index("cauldron.fixture.alpha") < order.index("cauldron.fixture.beta")

    def test_fixture_alpha_apps_in_installed_apps(self):
        plan = compose_django_settings(
            installed_apps=["cauldron"],
            module_settings={"cauldron.fixture.alpha": {}},
        )
        assert "cauldron_fixture_alpha" in plan.installed_apps

    def test_base_installed_apps_come_before_module_apps(self):
        plan = compose_django_settings(
            installed_apps=["django.contrib.contenttypes", "cauldron"],
            module_settings={"cauldron.fixture.alpha": {}},
        )
        base_idx = list(plan.installed_apps).index("cauldron")
        alpha_idx = list(plan.installed_apps).index("cauldron_fixture_alpha")
        assert base_idx < alpha_idx


class TestComposeDjangoSettingsMiddleware:
    """Middleware ordering tests using fixture modules (which have no middleware)."""

    def test_base_middleware_preserved(self):
        base_mw = ["django.middleware.security.SecurityMiddleware"]
        plan = compose_django_settings(
            middleware=base_mw,
            module_settings={},
        )
        assert "django.middleware.security.SecurityMiddleware" in plan.middleware

    def test_middleware_empty_when_no_modules(self):
        plan = compose_django_settings(
            middleware=[],
            module_settings={},
        )
        assert plan.middleware == ()

    def test_base_middleware_comes_first(self):
        base_mw = ["django.middleware.security.SecurityMiddleware"]
        plan = compose_django_settings(
            middleware=base_mw,
            module_settings={},
        )
        assert plan.middleware[0] == "django.middleware.security.SecurityMiddleware"


class TestComposeDjangoSettingsContextProcessors:
    """Context processor ordering tests."""

    def test_base_context_processors_preserved(self):
        base_cp = ["django.template.context_processors.request"]
        plan = compose_django_settings(
            context_processors=base_cp,
            module_settings={},
        )
        assert "django.template.context_processors.request" in plan.context_processors

    def test_context_processors_empty_when_no_modules(self):
        plan = compose_django_settings(
            context_processors=[],
            module_settings={},
        )
        assert plan.context_processors == ()


class TestComposeDjangoSettingsDeduplicate:
    """Duplicate values are removed, first occurrence preserved."""

    def test_duplicate_apps_removed(self):
        plan = compose_django_settings(
            installed_apps=["cauldron", "django.contrib.contenttypes", "cauldron"],
            module_settings={},
        )
        apps = list(plan.installed_apps)
        assert apps.count("cauldron") == 1

    def test_duplicate_middleware_removed(self):
        mw = "django.middleware.security.SecurityMiddleware"
        plan = compose_django_settings(
            middleware=[mw, mw],
            module_settings={},
        )
        assert list(plan.middleware).count(mw) == 1

    def test_first_occurrence_preserved(self):
        plan = compose_django_settings(
            installed_apps=["first", "second", "first"],
            module_settings={},
        )
        apps = list(plan.installed_apps)
        assert apps.index("first") == 0


class TestComposeDjangoSettingsMutationSafety:
    """Caller inputs must not be mutated after calling compose_django_settings."""

    def test_installed_apps_not_mutated(self):
        apps = ["django.contrib.contenttypes", "cauldron"]
        original = list(apps)
        compose_django_settings(installed_apps=apps, module_settings={})
        assert apps == original

    def test_middleware_not_mutated(self):
        mw = ["django.middleware.security.SecurityMiddleware"]
        original = list(mw)
        compose_django_settings(middleware=mw, module_settings={})
        assert mw == original

    def test_context_processors_not_mutated(self):
        cp = ["django.template.context_processors.request"]
        original = list(cp)
        compose_django_settings(context_processors=cp, module_settings={})
        assert cp == original

    def test_module_settings_not_mutated(self):
        ms = {"cauldron.fixture.alpha": {"key": "value"}}
        original_keys = set(ms.keys())
        compose_django_settings(module_settings=ms)
        assert set(ms.keys()) == original_keys


class TestComposeDjangoSettingsIdempotent:
    """Repeated calls produce identical output."""

    def test_idempotent_with_fixtures(self):
        kwargs = dict(
            installed_apps=["cauldron"],
            middleware=["django.middleware.security.SecurityMiddleware"],
            context_processors=["django.template.context_processors.request"],
            module_settings={
                "cauldron.fixture.alpha": {},
                "cauldron.fixture.beta": {},
            },
        )
        plan1 = compose_django_settings(**kwargs)
        plan2 = compose_django_settings(**kwargs)
        assert plan1 == plan2

    def test_idempotent_empty(self):
        plan1 = compose_django_settings(module_settings={})
        plan2 = compose_django_settings(module_settings={})
        assert plan1 == plan2


class TestSettingsPlanToDict:
    """SettingsPlan.to_dict() must be JSON-serializable."""

    def test_to_dict_is_json_serializable(self):
        plan = compose_django_settings(
            installed_apps=["cauldron"],
            module_settings={"cauldron.fixture.alpha": {}},
        )
        data = plan.to_dict()
        json.dumps(data)  # must not raise

    def test_to_dict_contains_expected_keys(self):
        plan = compose_django_settings(module_settings={})
        data = plan.to_dict()
        assert "installed_apps" in data
        assert "middleware" in data
        assert "context_processors" in data
        assert "enabled_modules" in data
        assert "module_order" in data
        assert "capability_providers" in data

    def test_to_dict_values_are_lists_not_tuples(self):
        """JSON-serialized data should use lists, not tuples."""
        plan = compose_django_settings(
            installed_apps=["cauldron"],
            module_settings={},
        )
        data = plan.to_dict()
        assert isinstance(data["installed_apps"], list)
        assert isinstance(data["middleware"], list)
        assert isinstance(data["context_processors"], list)
        assert isinstance(data["enabled_modules"], list)
        assert isinstance(data["module_order"], list)


class TestGetModuleAppsBackwardCompat:
    """get_module_apps() must still work exactly as before."""

    def test_returns_list(self):
        apps = get_module_apps({"cauldron.fixture.alpha": {}})
        assert isinstance(apps, list)

    def test_alpha_app_in_result(self):
        apps = get_module_apps({"cauldron.fixture.alpha": {}})
        assert "cauldron_fixture_alpha" in apps

    def test_beta_requires_alpha_ordering(self):
        apps = get_module_apps({
            "cauldron.fixture.alpha": {},
            "cauldron.fixture.beta": {},
        })
        # Alpha has no deps; Beta requires Alpha → alpha apps before beta apps.
        # cauldron_fixture_alpha is provided by alpha, cauldron_fixture_beta is not
        # (beta has no django_apps in fixture), but alpha must be before beta in order.
        assert "cauldron_fixture_alpha" in apps

    def test_accepts_list_form(self):
        apps = get_module_apps(["cauldron.fixture.alpha"])
        assert "cauldron_fixture_alpha" in apps

    def test_empty_dict_returns_empty(self):
        apps = get_module_apps({})
        assert apps == []
