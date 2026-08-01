"""Tests for ModuleRequirement, ModuleManifest, BaseModule, and CauldronModule protocol."""

import json

import pytest

from cauldron.modules import (
    BaseModule,
    CauldronModule,
    ModuleManifest,
    ModuleMigrationDeclaration,
    ModuleNavigationDeclaration,
    ModulePermissionDeclaration,
    ModuleRequirement,
    ModuleSettingsDeclaration,
    ProvidedCapability,
    RuntimeRequirement,
)


class TestModuleRequirement:
    def test_defaults(self):
        req = ModuleRequirement(slug="some.module")
        assert req.slug == "some.module"
        assert req.version == ""
        assert req.kind == "module"

    def test_capability_kind(self):
        req = ModuleRequirement(slug="some.capability", kind="capability")
        assert req.kind == "capability"

    def test_version_constraint(self):
        req = ModuleRequirement(slug="some.module", version=">=1.0.0,<2.0.0")
        assert req.version == ">=1.0.0,<2.0.0"

    def test_frozen(self):
        req = ModuleRequirement(slug="some.module")
        with pytest.raises(Exception):
            req.slug = "other"  # type: ignore[misc]

    # -- validation --

    def test_empty_slug_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModuleRequirement(slug="")

    def test_invalid_slug_raises(self):
        with pytest.raises(ValueError, match="pattern"):
            ModuleRequirement(slug="Bad-Slug")

    def test_invalid_version_specifier_raises(self):
        with pytest.raises(ValueError, match="specifier"):
            ModuleRequirement(slug="a", version="not_a_specifier!")

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError, match="kind"):
            ModuleRequirement(slug="a", kind="unknown")  # type: ignore[arg-type]

    # -- serialization --

    def test_round_trip_module(self):
        req = ModuleRequirement(slug="cauldron.dep", version=">=1.0.0", kind="module")
        assert ModuleRequirement.from_dict(req.to_dict()) == req

    def test_round_trip_capability(self):
        req = ModuleRequirement(slug="some.cap", kind="capability")
        assert ModuleRequirement.from_dict(req.to_dict()) == req

    def test_to_dict_is_json_serializable(self):
        req = ModuleRequirement(slug="a", version=">=1.0.0")
        json.dumps(req.to_dict())  # must not raise


# ---------------------------------------------------------------------------
# New value objects
# ---------------------------------------------------------------------------

class TestModuleSettingsDeclaration:
    def test_defaults(self):
        d = ModuleSettingsDeclaration(key="site_root")
        assert d.key == "site_root"
        assert d.required is False
        assert d.description == ""

    def test_required_and_description(self):
        d = ModuleSettingsDeclaration(key="routing", required=True, description="Routing config.")
        assert d.required is True
        assert d.description == "Routing config."

    def test_frozen(self):
        d = ModuleSettingsDeclaration(key="x")
        with pytest.raises(Exception):
            d.key = "y"  # type: ignore[misc]

    def test_empty_key_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModuleSettingsDeclaration(key="")

    def test_key_with_spaces_raises(self):
        with pytest.raises(ValueError, match="lowercase identifier"):
            ModuleSettingsDeclaration(key="site root")

    def test_key_with_uppercase_raises(self):
        with pytest.raises(ValueError, match="lowercase identifier"):
            ModuleSettingsDeclaration(key="SiteRoot")

    def test_key_with_dots_raises(self):
        with pytest.raises(ValueError, match="lowercase identifier"):
            ModuleSettingsDeclaration(key="site.root")

    def test_valid_underscored_key(self):
        d = ModuleSettingsDeclaration(key="workspace_root")
        assert d.key == "workspace_root"

    def test_round_trip(self):
        d = ModuleSettingsDeclaration(key="output_root", required=True, description="Build dir.")
        assert ModuleSettingsDeclaration.from_dict(d.to_dict()) == d

    def test_from_dict_defaults(self):
        d = ModuleSettingsDeclaration.from_dict({"key": "foo"})
        assert d.required is False
        assert d.description == ""
        assert d.setting_path == ""

    def test_setting_path_top_level_django_setting(self):
        d = ModuleSettingsDeclaration(
            key="ui_overrides_dir",
            required=False,
            description="Override dir.",
            setting_path="CAULDRON_UI_OVERRIDES_DIR",
        )
        assert d.setting_path == "CAULDRON_UI_OVERRIDES_DIR"

    def test_invalid_setting_path_lowercase_raises(self):
        with pytest.raises(ValueError, match="UPPER_SNAKE_CASE"):
            ModuleSettingsDeclaration(key="mykey", setting_path="lowercase_path")

    def test_setting_path_round_trip(self):
        d = ModuleSettingsDeclaration(key="dir", setting_path="SOME_DIR")
        assert ModuleSettingsDeclaration.from_dict(d.to_dict()) == d

    def test_to_dict_json_serializable(self):
        d = ModuleSettingsDeclaration(key="database_alias", description="DB alias.")
        json.dumps(d.to_dict())  # must not raise


class TestRuntimeRequirement:
    def test_basic(self):
        r = RuntimeRequirement(kind="database")
        assert r.kind == "database"
        assert r.alias == ""
        assert r.description == ""

    def test_full(self):
        r = RuntimeRequirement(kind="cache", alias="sessions", description="Used for session caching.")
        assert r.kind == "cache"
        assert r.alias == "sessions"
        assert r.description == "Used for session caching."

    def test_frozen(self):
        r = RuntimeRequirement(kind="database")
        with pytest.raises(Exception):
            r.kind = "cache"  # type: ignore[misc]

    def test_empty_kind_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            RuntimeRequirement(kind="")

    def test_round_trip(self):
        r = RuntimeRequirement(kind="worker", alias="celery", description="Background task queue.")
        assert RuntimeRequirement.from_dict(r.to_dict()) == r

    def test_from_dict_defaults(self):
        r = RuntimeRequirement.from_dict({"kind": "storage"})
        assert r.alias == ""
        assert r.description == ""

    def test_to_dict_json_serializable(self):
        r = RuntimeRequirement(kind="database")
        json.dumps(r.to_dict())  # must not raise


class TestModuleMigrationDeclaration:
    def test_basic(self):
        m = ModuleMigrationDeclaration(app_label="cauldron_ai_admin")
        assert m.app_label == "cauldron_ai_admin"

    def test_frozen(self):
        m = ModuleMigrationDeclaration(app_label="myapp")
        with pytest.raises(Exception):
            m.app_label = "other"  # type: ignore[misc]

    def test_empty_app_label_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModuleMigrationDeclaration(app_label="")

    def test_invalid_app_label_raises(self):
        with pytest.raises(ValueError, match="valid Django app label"):
            ModuleMigrationDeclaration(app_label="bad app")

    def test_round_trip(self):
        m = ModuleMigrationDeclaration(app_label="cauldron_content_operations")
        assert ModuleMigrationDeclaration.from_dict(m.to_dict()) == m

    def test_to_dict_json_serializable(self):
        m = ModuleMigrationDeclaration(app_label="myapp")
        json.dumps(m.to_dict())  # must not raise


class TestModulePermissionDeclaration:
    def test_basic(self):
        p = ModulePermissionDeclaration(
            codename="use_admin_ai",
            name="Can use Admin AI",
            app_label="cauldron_ai_admin",
        )
        assert p.codename == "use_admin_ai"
        assert p.name == "Can use Admin AI"
        assert p.app_label == "cauldron_ai_admin"

    def test_frozen(self):
        p = ModulePermissionDeclaration(codename="x", name="X", app_label="myapp")
        with pytest.raises(Exception):
            p.codename = "y"  # type: ignore[misc]

    def test_empty_codename_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModulePermissionDeclaration(codename="", name="X", app_label="myapp")

    def test_invalid_codename_uppercase_raises(self):
        with pytest.raises(ValueError, match="lowercase identifier"):
            ModulePermissionDeclaration(codename="UseAdminAI", name="X", app_label="myapp")

    def test_invalid_codename_spaces_raises(self):
        with pytest.raises(ValueError, match="lowercase identifier"):
            ModulePermissionDeclaration(codename="use admin ai", name="X", app_label="myapp")

    def test_invalid_codename_leading_digit_raises(self):
        with pytest.raises(ValueError, match="lowercase identifier"):
            ModulePermissionDeclaration(codename="1bad", name="X", app_label="myapp")

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModulePermissionDeclaration(codename="mycode", name="", app_label="myapp")

    def test_empty_app_label_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModulePermissionDeclaration(codename="mycode", name="My Code", app_label="")

    def test_round_trip(self):
        p = ModulePermissionDeclaration(
            codename="view_content_audit",
            name="Can view content audit",
            app_label="cauldron_content_operations",
        )
        assert ModulePermissionDeclaration.from_dict(p.to_dict()) == p

    def test_to_dict_json_serializable(self):
        p = ModulePermissionDeclaration(codename="do_thing", name="Can do thing", app_label="myapp")
        json.dumps(p.to_dict())  # must not raise


class TestModuleNavigationDeclaration:
    def test_section(self):
        n = ModuleNavigationDeclaration(key="content", label="Content")
        assert n.key == "content"
        assert n.label == "Content"
        assert n.section == ""
        assert n.url_name == ""
        assert n.order == 0
        assert n.permission == ""
        assert n.url_prefix == ""
        assert n.url_prefix_exact is False
        assert n.description == ""

    def test_item(self):
        n = ModuleNavigationDeclaration(
            key="cauldron.admin.content.browser",
            label="Content Browser",
            section="content",
        )
        assert n.section == "content"

    def test_full_item(self):
        n = ModuleNavigationDeclaration(
            key="cauldron.ai.admin.page",
            label="AI Assistant",
            section="ai",
            url_name="cauldron_ai_admin:ai-page",
            order=10,
            permission="cauldron_ai_admin.use_admin_ai",
            url_prefix="/cauldron/admin/ai/",
            description="Interact with the Admin AI assistant",
        )
        assert n.url_name == "cauldron_ai_admin:ai-page"
        assert n.order == 10
        assert n.permission == "cauldron_ai_admin.use_admin_ai"
        assert n.url_prefix == "/cauldron/admin/ai/"
        assert n.url_prefix_exact is False
        assert n.description == "Interact with the Admin AI assistant"

    def test_url_prefix_exact(self):
        n = ModuleNavigationDeclaration(
            key="cauldron.dashboard",
            label="Dashboard",
            section="overview",
            url_name="cauldron:dashboard",
            url_prefix="/cauldron/",
            url_prefix_exact=True,
        )
        assert n.url_prefix_exact is True

    def test_key_with_hyphens(self):
        n = ModuleNavigationDeclaration(
            key="cauldron.admin.content.page-create",
            label="New Page",
            section="content",
        )
        assert n.key == "cauldron.admin.content.page-create"

    def test_frozen(self):
        n = ModuleNavigationDeclaration(key="x", label="X")
        with pytest.raises(Exception):
            n.key = "y"  # type: ignore[misc]

    def test_empty_key_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModuleNavigationDeclaration(key="", label="X")

    def test_invalid_key_raises(self):
        with pytest.raises(ValueError, match="dotted lowercase"):
            ModuleNavigationDeclaration(key="Bad Key", label="X")

    def test_empty_label_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModuleNavigationDeclaration(key="mykey", label="")

    def test_invalid_permission_format_raises(self):
        with pytest.raises(ValueError, match="app_label.codename"):
            ModuleNavigationDeclaration(
                key="mykey", label="My Key",
                permission="just_codename_no_dot",
            )

    def test_empty_permission_allowed(self):
        n = ModuleNavigationDeclaration(key="mykey", label="My Key", permission="")
        assert n.permission == ""

    def test_round_trip(self):
        n = ModuleNavigationDeclaration(
            key="cauldron.admin.content.change-requests",
            label="Change Requests",
            section="content",
            url_name="cauldron_admin_content:change-request-list",
            order=30,
            permission="cauldron_content_operations.view_content_change_requests",
            url_prefix="/cauldron/content/change-requests/",
            description="Review content change requests",
        )
        assert ModuleNavigationDeclaration.from_dict(n.to_dict()) == n

    def test_from_dict_defaults_section_empty(self):
        n = ModuleNavigationDeclaration.from_dict({"key": "overview", "label": "Overview"})
        assert n.section == ""
        assert n.url_name == ""
        assert n.order == 0
        assert n.permission == ""
        assert n.url_prefix == ""
        assert n.url_prefix_exact is False
        assert n.description == ""

    def test_to_dict_json_serializable(self):
        n = ModuleNavigationDeclaration(
            key="mykey", label="My Key",
            url_name="ns:view-name", order=10,
        )
        json.dumps(n.to_dict())  # must not raise


class TestProvidedCapability:
    def test_slug_only(self):
        c = ProvidedCapability(slug="ai.model.providers")
        assert c.slug == "ai.model.providers"
        assert c.contract == ""
        assert c.description == ""

    def test_full(self):
        c = ProvidedCapability(
            slug="site.public",
            contract="cauldron_content.site.SitePublicUrlProvider",
            description="Provides public URL generation.",
        )
        assert c.contract == "cauldron_content.site.SitePublicUrlProvider"
        assert c.description == "Provides public URL generation."

    def test_frozen(self):
        c = ProvidedCapability(slug="a.cap")
        with pytest.raises(Exception):
            c.slug = "b.cap"  # type: ignore[misc]

    def test_empty_slug_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ProvidedCapability(slug="")

    def test_invalid_slug_raises(self):
        with pytest.raises(ValueError, match="pattern"):
            ProvidedCapability(slug="Bad-Slug")

    def test_invalid_contract_path_raises(self):
        with pytest.raises(ValueError, match="valid dotted Python identifier"):
            ProvidedCapability(slug="a.cap", contract="bad contract path!")

    def test_empty_contract_allowed(self):
        c = ProvidedCapability(slug="a.cap", contract="")
        assert c.contract == ""

    def test_round_trip(self):
        c = ProvidedCapability(
            slug="ai.model.providers",
            contract="cauldron_ai.providers.AIProviderRegistry",
            description="Provider registry.",
        )
        assert ProvidedCapability.from_dict(c.to_dict()) == c

    def test_from_dict_defaults(self):
        c = ProvidedCapability.from_dict({"slug": "content.routing"})
        assert c.contract == ""
        assert c.description == ""

    def test_to_dict_json_serializable(self):
        c = ProvidedCapability(slug="a.cap", description="A capability.")
        json.dumps(c.to_dict())  # must not raise


# ---------------------------------------------------------------------------
# ModuleManifest — existing fields (unchanged behaviour)
# ---------------------------------------------------------------------------

class TestModuleManifest:
    def test_minimal(self):
        m = ModuleManifest(slug="test.module", label="Test Module")
        assert m.slug == "test.module"
        assert m.label == "Test Module"
        assert m.version == "0.0.0"
        assert m.cauldron_version == ""
        assert m.django_apps == ()
        assert m.django_middleware == ()
        assert m.django_context_processors == ()
        assert m.settings == {}
        assert m.requires == ()
        assert m.optional == ()
        assert m.provides == ()

    def test_full(self):
        req = ModuleRequirement(slug="dep.module")
        opt = ModuleRequirement(slug="opt.capability", kind="capability")
        m = ModuleManifest(
            slug="test.module",
            label="Test",
            version="2.1.0",
            cauldron_version=">=0.1.0,<1.0.0",
            django_apps=("myapp",),
            settings={"key": "value"},
            requires=(req,),
            optional=(opt,),
            provides=("some.capability",),
        )
        assert m.version == "2.1.0"
        assert m.cauldron_version == ">=0.1.0,<1.0.0"
        assert m.django_apps == ("myapp",)
        assert m.settings == {"key": "value"}
        assert m.requires == (req,)
        assert m.optional == (opt,)
        assert m.provides == ("some.capability",)

    def test_frozen(self):
        m = ModuleManifest(slug="test.module", label="Test")
        with pytest.raises(Exception):
            m.slug = "other"  # type: ignore[misc]

    # -- validation --

    def test_empty_slug_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            ModuleManifest(slug="", label="Test")

    def test_invalid_slug_raises(self):
        with pytest.raises(ValueError, match="pattern"):
            ModuleManifest(slug="Bad_Slug", label="Test")

    def test_empty_label_raises(self):
        with pytest.raises(ValueError, match="label"):
            ModuleManifest(slug="valid.slug", label="")

    def test_invalid_version_raises(self):
        with pytest.raises(ValueError, match="version"):
            ModuleManifest(slug="a", label="A", version="not-a-version!")

    def test_invalid_cauldron_version_raises(self):
        with pytest.raises(ValueError, match="specifier"):
            ModuleManifest(slug="a", label="A", cauldron_version="bad!specifier")

    def test_invalid_provides_entry_raises(self):
        with pytest.raises(ValueError, match="pattern"):
            ModuleManifest(slug="a", label="A", provides=("Bad-Cap",))

    def test_empty_django_app_entry_raises(self):
        with pytest.raises(ValueError, match="non-empty strings"):
            ModuleManifest(slug="a", label="A", django_apps=("",))

    def test_non_string_django_app_entry_raises(self):
        with pytest.raises(ValueError, match="non-empty strings"):
            ModuleManifest(slug="a", label="A", django_apps=(123,))  # type: ignore[arg-type]

    def test_empty_django_middleware_entry_raises(self):
        with pytest.raises(ValueError, match="non-empty strings"):
            ModuleManifest(slug="a", label="A", django_middleware=("",))

    def test_empty_django_context_processor_entry_raises(self):
        with pytest.raises(ValueError, match="non-empty strings"):
            ModuleManifest(slug="a", label="A", django_context_processors=("",))

    def test_valid_dotted_slug(self):
        m = ModuleManifest(slug="cauldron.content.core", label="Content Core")
        assert m.slug == "cauldron.content.core"

    # -- serialization --

    def test_round_trip_minimal(self):
        m = ModuleManifest(slug="a", label="A")
        assert ModuleManifest.from_dict(m.to_dict()) == m

    def test_round_trip_full(self):
        req = ModuleRequirement(slug="dep.module", version=">=1.0.0")
        opt = ModuleRequirement(slug="opt.cap", kind="capability")
        m = ModuleManifest(
            slug="test.module",
            label="Test",
            version="1.2.3",
            cauldron_version=">=0.1.0",
            django_apps=("app1",),
            settings={"k": "v"},
            requires=(req,),
            optional=(opt,),
            provides=("my.cap",),
        )
        assert ModuleManifest.from_dict(m.to_dict()) == m

    def test_to_dict_is_json_serializable(self):
        m = ModuleManifest(
            slug="a",
            label="A",
            version="1.0.0",
            provides=("my.cap",),
        )
        json.dumps(m.to_dict())  # must not raise

    def test_from_dict_applies_defaults(self):
        m = ModuleManifest.from_dict({"slug": "a", "label": "A"})
        assert m.version == "0.0.0"
        assert m.cauldron_version == ""
        assert m.django_apps == ()
        assert m.django_middleware == ()
        assert m.django_context_processors == ()
        assert m.requires == ()

    def test_round_trip_with_middleware_and_context_processors(self):
        m = ModuleManifest(
            slug="test.module",
            label="Test",
            django_middleware=("my.middleware.Cls",),
            django_context_processors=("my.ctx.proc",),
        )
        restored = ModuleManifest.from_dict(m.to_dict())
        assert restored == m
        assert restored.django_middleware == ("my.middleware.Cls",)
        assert restored.django_context_processors == ("my.ctx.proc",)

    def test_from_dict_middleware_context_processor_defaults_empty(self):
        m = ModuleManifest.from_dict({"slug": "a", "label": "A"})
        assert m.django_middleware == ()
        assert m.django_context_processors == ()


# ---------------------------------------------------------------------------
# ModuleManifest — new fields: defaults and basic access
# ---------------------------------------------------------------------------

class TestModuleManifestNewFieldDefaults:
    def _minimal(self) -> ModuleManifest:
        return ModuleManifest(slug="test.module", label="Test")

    def test_settings_declarations_default_empty(self):
        assert self._minimal().settings_declarations == ()

    def test_migration_apps_default_empty(self):
        assert self._minimal().migration_apps == ()

    def test_permissions_default_empty(self):
        assert self._minimal().permissions == ()

    def test_navigation_default_empty(self):
        assert self._minimal().navigation == ()

    def test_ai_tools_default_empty(self):
        assert self._minimal().ai_tools == ()

    def test_prompt_templates_default_empty(self):
        assert self._minimal().prompt_templates == ()

    def test_provided_capabilities_default_empty(self):
        assert self._minimal().provided_capabilities == ()

    def test_requires_restart_false_without_django_apps(self):
        assert self._minimal().requires_restart is False

    def test_requires_restart_true_with_django_apps(self):
        m = ModuleManifest(slug="a", label="A", django_apps=("myapp",))
        assert m.requires_restart is True

    def test_requires_restart_true_with_middleware_only(self):
        m = ModuleManifest(slug="a", label="A", django_middleware=("some.Middleware",))
        assert m.requires_restart is True

    def test_requires_restart_true_with_context_processors_only(self):
        m = ModuleManifest(slug="a", label="A", django_context_processors=("some.processor",))
        assert m.requires_restart is True

    def test_restart_required_defaults_false(self):
        assert self._minimal().restart_required is False

    def test_runtime_requirements_default_empty(self):
        assert self._minimal().runtime_requirements == ()

    def test_restart_required_explicit_true(self):
        m = ModuleManifest(slug="a", label="A", restart_required=True)
        assert m.restart_required is True
        assert m.requires_restart is True

    def test_requires_restart_true_from_explicit_flag(self):
        # No django_apps but restart_required set explicitly.
        m = ModuleManifest(slug="a", label="A", restart_required=True)
        assert m.django_apps == ()
        assert m.requires_restart is True

    def test_runtime_requirements_accepted(self):
        m = ModuleManifest(
            slug="a", label="A",
            runtime_requirements=(
                RuntimeRequirement(kind="database"),
                RuntimeRequirement(kind="cache", alias="sessions"),
            ),
        )
        assert len(m.runtime_requirements) == 2
        assert m.runtime_requirements[0].kind == "database"
        assert m.runtime_requirements[1].alias == "sessions"

    def test_backwards_compat_from_dict_without_new_fields(self):
        # Old serialised manifests (missing new keys) must still deserialise.
        m = ModuleManifest.from_dict({"slug": "a", "label": "A"})
        assert m.settings_declarations == ()
        assert m.migration_apps == ()
        assert m.permissions == ()
        assert m.navigation == ()
        assert m.ai_tools == ()
        assert m.prompt_templates == ()
        assert m.provided_capabilities == ()
        assert m.restart_required is False
        assert m.runtime_requirements == ()


# ---------------------------------------------------------------------------
# ModuleManifest — new field validation
# ---------------------------------------------------------------------------

class TestModuleManifestSettingsValidation:
    def test_settings_declaration_accepted(self):
        m = ModuleManifest(
            slug="a", label="A",
            settings_declarations=(ModuleSettingsDeclaration(key="site_root", required=True),),
        )
        assert len(m.settings_declarations) == 1

    def test_duplicate_settings_key_raises(self):
        with pytest.raises(ValueError, match="duplicate key"):
            ModuleManifest(
                slug="a", label="A",
                settings_declarations=(
                    ModuleSettingsDeclaration(key="site_root"),
                    ModuleSettingsDeclaration(key="site_root"),
                ),
            )


class TestModuleManifestMigrationValidation:
    def test_migration_app_accepted_when_in_django_apps(self):
        m = ModuleManifest(
            slug="a", label="A",
            django_apps=("myapp",),
            migration_apps=(ModuleMigrationDeclaration(app_label="myapp"),),
        )
        assert len(m.migration_apps) == 1

    def test_migration_app_not_in_django_apps_raises(self):
        with pytest.raises(ValueError, match="does not correspond to any entry in django_apps"):
            ModuleManifest(
                slug="a", label="A",
                django_apps=("otherapp",),
                migration_apps=(ModuleMigrationDeclaration(app_label="myapp"),),
            )

    def test_django_builtin_app_label_accepted(self):
        # "auth" is the label for "django.contrib.auth" — last dotted segment.
        m = ModuleManifest(
            slug="a", label="A",
            django_apps=("django.contrib.auth",),
            migration_apps=(ModuleMigrationDeclaration(app_label="auth"),),
        )
        assert m.migration_apps[0].app_label == "auth"

    def test_contenttypes_label_accepted(self):
        m = ModuleManifest(
            slug="a", label="A",
            django_apps=("django.contrib.contenttypes", "django.contrib.auth"),
            migration_apps=(
                ModuleMigrationDeclaration(app_label="contenttypes"),
                ModuleMigrationDeclaration(app_label="auth"),
            ),
        )
        assert len(m.migration_apps) == 2

    def test_duplicate_migration_app_raises(self):
        with pytest.raises(ValueError, match="duplicate app_label"):
            ModuleManifest(
                slug="a", label="A",
                django_apps=("myapp",),
                migration_apps=(
                    ModuleMigrationDeclaration(app_label="myapp"),
                    ModuleMigrationDeclaration(app_label="myapp"),
                ),
            )


class TestModuleManifestPermissionsValidation:
    def test_permission_accepted_when_app_in_django_apps(self):
        m = ModuleManifest(
            slug="a", label="A",
            django_apps=("myapp",),
            permissions=(
                ModulePermissionDeclaration(codename="do_thing", name="Can do thing", app_label="myapp"),
            ),
        )
        assert len(m.permissions) == 1

    def test_permission_app_not_in_django_apps_raises(self):
        with pytest.raises(ValueError, match="does not correspond to any entry in django_apps"):
            ModuleManifest(
                slug="a", label="A",
                django_apps=("myapp",),
                permissions=(
                    ModulePermissionDeclaration(codename="do_thing", name="Do it", app_label="otherapp"),
                ),
            )

    def test_duplicate_codename_same_app_raises(self):
        with pytest.raises(ValueError, match="duplicate.*app_label.*codename"):
            ModuleManifest(
                slug="a", label="A",
                django_apps=("myapp",),
                permissions=(
                    ModulePermissionDeclaration(codename="do_thing", name="Do it", app_label="myapp"),
                    ModulePermissionDeclaration(codename="do_thing", name="Do it too", app_label="myapp"),
                ),
            )

    def test_same_codename_in_different_apps_accepted(self):
        # The same codename in two distinct Django apps is a valid combination;
        # uniqueness is enforced on the (app_label, codename) pair, not codename alone.
        m = ModuleManifest(
            slug="a", label="A",
            django_apps=("myapp", "otherapp"),
            permissions=(
                ModulePermissionDeclaration(codename="do_thing", name="Do it in myapp", app_label="myapp"),
                ModulePermissionDeclaration(codename="do_thing", name="Do it in otherapp", app_label="otherapp"),
            ),
        )
        assert len(m.permissions) == 2

    def test_multiple_permissions_different_codenames_accepted(self):
        m = ModuleManifest(
            slug="a", label="A",
            django_apps=("myapp",),
            permissions=(
                ModulePermissionDeclaration(codename="view_x", name="View X", app_label="myapp"),
                ModulePermissionDeclaration(codename="edit_x", name="Edit X", app_label="myapp"),
            ),
        )
        assert len(m.permissions) == 2


class TestModuleManifestNavigationValidation:
    def test_navigation_accepted(self):
        m = ModuleManifest(
            slug="a", label="A",
            navigation=(
                ModuleNavigationDeclaration(key="overview", label="Overview"),
                ModuleNavigationDeclaration(
                    key="cauldron.dashboard", label="Dashboard",
                    section="overview", url_name="cauldron:dashboard",
                ),
            ),
        )
        assert len(m.navigation) == 2

    def test_duplicate_nav_key_raises(self):
        with pytest.raises(ValueError, match="duplicate key"):
            ModuleManifest(
                slug="a", label="A",
                navigation=(
                    ModuleNavigationDeclaration(key="overview", label="Overview"),
                    ModuleNavigationDeclaration(key="overview", label="Overview Again"),
                ),
            )

    def test_hyphenated_nav_key_accepted(self):
        m = ModuleManifest(
            slug="a", label="A",
            navigation=(
                ModuleNavigationDeclaration(key="cauldron.content.page-create", label="New Page"),
            ),
        )
        assert m.navigation[0].key == "cauldron.content.page-create"

    def test_item_without_url_name_raises(self):
        with pytest.raises(ValueError, match="url_name"):
            ModuleManifest(
                slug="a", label="A",
                navigation=(
                    ModuleNavigationDeclaration(key="overview", label="Overview"),
                    ModuleNavigationDeclaration(key="cauldron.dashboard", label="Dashboard", section="overview"),
                ),
            )

    def test_section_with_url_name_raises(self):
        with pytest.raises(ValueError, match="item-only"):
            ModuleManifest(
                slug="a", label="A",
                navigation=(
                    ModuleNavigationDeclaration(key="overview", label="Overview", url_name="cauldron:home"),
                ),
            )

    def test_section_with_permission_raises(self):
        with pytest.raises(ValueError, match="item-only"):
            ModuleManifest(
                slug="a", label="A",
                navigation=(
                    ModuleNavigationDeclaration(key="overview", label="Overview", permission="myapp.view_x"),
                ),
            )

    def test_section_with_url_prefix_exact_raises(self):
        with pytest.raises(ValueError, match="item-only"):
            ModuleManifest(
                slug="a", label="A",
                navigation=(
                    ModuleNavigationDeclaration(key="overview", label="Overview", url_prefix_exact=True),
                ),
            )

    def test_item_references_section_in_another_module(self):
        # Items may reference sections that live in a different module's manifest.
        m = ModuleManifest(
            slug="a", label="A",
            navigation=(
                ModuleNavigationDeclaration(
                    key="cauldron.dashboard", label="Dashboard",
                    section="overview",           # "overview" defined by another module
                    url_name="cauldron:dashboard",
                ),
            ),
        )
        assert m.navigation[0].section == "overview"


class TestModuleManifestAIToolsValidation:
    def test_ai_tools_accepted(self):
        m = ModuleManifest(
            slug="a", label="A",
            ai_tools=("content.list_collections", "site.publish"),
        )
        assert m.ai_tools == ("content.list_collections", "site.publish")

    def test_duplicate_ai_tool_raises(self):
        with pytest.raises(ValueError, match="duplicate entry"):
            ModuleManifest(
                slug="a", label="A",
                ai_tools=("site.inspect", "site.inspect"),
            )

    def test_invalid_ai_tool_name_raises(self):
        with pytest.raises(ValueError, match="dotted lowercase"):
            ModuleManifest(slug="a", label="A", ai_tools=("Bad Tool",))

    def test_duplicate_prompt_template_raises(self):
        with pytest.raises(ValueError, match="duplicate entry"):
            ModuleManifest(
                slug="a", label="A",
                prompt_templates=("site.inspect", "site.inspect"),
            )

    def test_invalid_prompt_template_name_raises(self):
        with pytest.raises(ValueError, match="dotted lowercase"):
            ModuleManifest(slug="a", label="A", prompt_templates=("Bad Template",))


class TestModuleManifestProvidedCapabilitiesValidation:
    def test_provided_capability_accepted(self):
        m = ModuleManifest(
            slug="a", label="A",
            provides=("ai.providers",),
            provided_capabilities=(
                ProvidedCapability(slug="ai.providers", description="AI providers."),
            ),
        )
        assert len(m.provided_capabilities) == 1

    def test_provided_capability_slug_not_in_provides_raises(self):
        with pytest.raises(ValueError, match="must appear in provides"):
            ModuleManifest(
                slug="a", label="A",
                provides=("ai.providers",),
                provided_capabilities=(
                    ProvidedCapability(slug="unrelated.cap"),
                ),
            )

    def test_duplicate_provided_capability_slug_raises(self):
        with pytest.raises(ValueError, match="duplicate slug"):
            ModuleManifest(
                slug="a", label="A",
                provides=("ai.providers",),
                provided_capabilities=(
                    ProvidedCapability(slug="ai.providers"),
                    ProvidedCapability(slug="ai.providers"),
                ),
            )

    def test_provided_capability_with_contract(self):
        m = ModuleManifest(
            slug="a", label="A",
            provides=("site.public",),
            provided_capabilities=(
                ProvidedCapability(
                    slug="site.public",
                    contract="cauldron_content.site.SitePublicUrlProvider",
                ),
            ),
        )
        assert m.provided_capabilities[0].contract == "cauldron_content.site.SitePublicUrlProvider"

    def test_contract_in_own_namespace_must_be_in_public_api(self):
        # Contract namespace "myapp" is in the module's own namespaces but the
        # path "myapp._internal.MyProtocol" is NOT under public_api.
        with pytest.raises(ValueError, match="not under public_api"):
            ModuleManifest(
                slug="a", label="A",
                namespaces=("myapp",),
                public_api=("myapp.api",),
                provides=("my.cap",),
                provided_capabilities=(
                    ProvidedCapability(
                        slug="my.cap",
                        contract="myapp._internal.MyProtocol",
                    ),
                ),
            )

    def test_contract_in_own_namespace_under_public_api_passes(self):
        m = ModuleManifest(
            slug="a", label="A",
            namespaces=("myapp",),
            public_api=("myapp.contracts",),
            provides=("my.cap",),
            provided_capabilities=(
                ProvidedCapability(
                    slug="my.cap",
                    contract="myapp.contracts.MyProtocol",
                ),
            ),
        )
        assert m.provided_capabilities[0].contract == "myapp.contracts.MyProtocol"

    def test_contract_in_dependency_namespace_always_passes(self):
        # Contract in a dependency's namespace — no cross-manifest validation possible.
        m = ModuleManifest(
            slug="a", label="A",
            namespaces=("provider_app",),
            public_api=("provider_app.impl",),
            provides=("my.cap",),
            provided_capabilities=(
                ProvidedCapability(
                    slug="my.cap",
                    contract="dependency_pkg.contracts.MyProtocol",
                ),
            ),
        )
        assert m.provided_capabilities[0].contract == "dependency_pkg.contracts.MyProtocol"

    # -- boundary-aware namespace ownership --

    def test_contract_in_dotted_owned_namespace_under_public_api_passes(self):
        # Namespace "myapp.core" is dotted; contract "myapp.core.contracts.MyProtocol"
        # is owned and under public_api.
        m = ModuleManifest(
            slug="a", label="A",
            namespaces=("myapp.core",),
            public_api=("myapp.core.contracts",),
            provides=("my.cap",),
            provided_capabilities=(
                ProvidedCapability(
                    slug="my.cap",
                    contract="myapp.core.contracts.MyProtocol",
                ),
            ),
        )
        assert m.provided_capabilities[0].contract == "myapp.core.contracts.MyProtocol"

    def test_contract_in_dotted_owned_namespace_outside_public_api_raises(self):
        # Namespace "myapp.core" owns the contract but "myapp.core._internal.X"
        # is not under public_api.
        with pytest.raises(ValueError, match="not under public_api"):
            ModuleManifest(
                slug="a", label="A",
                namespaces=("myapp.core",),
                public_api=("myapp.core.contracts",),
                provides=("my.cap",),
                provided_capabilities=(
                    ProvidedCapability(
                        slug="my.cap",
                        contract="myapp.core._internal.MyProtocol",
                    ),
                ),
            )

    def test_similarly_prefixed_namespace_not_treated_as_owned(self):
        # "myapp_extra" starts with "myapp" but is NOT in namespace "myapp".
        # The contract "myapp_extra.contracts.X" must NOT trigger the public_api check.
        m = ModuleManifest(
            slug="a", label="A",
            namespaces=("myapp",),
            public_api=("myapp.api",),
            provides=("my.cap",),
            provided_capabilities=(
                ProvidedCapability(
                    slug="my.cap",
                    contract="myapp_extra.contracts.MyProtocol",
                ),
            ),
        )
        assert m.provided_capabilities[0].contract == "myapp_extra.contracts.MyProtocol"

    def test_top_level_owned_namespace_contract_under_public_api_passes(self):
        m = ModuleManifest(
            slug="a", label="A",
            namespaces=("myapp",),
            public_api=("myapp.contracts",),
            provides=("my.cap",),
            provided_capabilities=(
                ProvidedCapability(
                    slug="my.cap",
                    contract="myapp.contracts.MyProtocol",
                ),
            ),
        )
        assert m.provided_capabilities[0].contract == "myapp.contracts.MyProtocol"

    def test_top_level_owned_namespace_contract_outside_public_api_raises(self):
        with pytest.raises(ValueError, match="not under public_api"):
            ModuleManifest(
                slug="a", label="A",
                namespaces=("myapp",),
                public_api=("myapp.contracts",),
                provides=("my.cap",),
                provided_capabilities=(
                    ProvidedCapability(
                        slug="my.cap",
                        contract="myapp.internal.MyProtocol",
                    ),
                ),
            )


# ---------------------------------------------------------------------------
# ModuleManifest — round-trip with all new fields
# ---------------------------------------------------------------------------

class TestModuleManifestRoundTrip:
    def test_round_trip_with_all_new_fields(self):
        m = ModuleManifest(
            slug="test.full",
            label="Full Test",
            version="1.0.0",
            django_apps=("myapp",),
            provides=("my.cap",),
            settings_declarations=(
                ModuleSettingsDeclaration(key="api_key", required=True, description="API key."),
            ),
            migration_apps=(
                ModuleMigrationDeclaration(app_label="myapp"),
            ),
            permissions=(
                ModulePermissionDeclaration(codename="use_feature", name="Can use feature", app_label="myapp"),
            ),
            navigation=(
                ModuleNavigationDeclaration(key="mymodule", label="My Module"),
                ModuleNavigationDeclaration(key="mymodule.home", label="Home", section="mymodule", url_name="mymodule:home"),
            ),
            ai_tools=("mymodule.inspect",),
            prompt_templates=("mymodule.inspect",),
            provided_capabilities=(
                ProvidedCapability(slug="my.cap", description="My capability."),
            ),
        )
        restored = ModuleManifest.from_dict(m.to_dict())
        assert restored == m

    def test_round_trip_all_new_fields_json_serializable(self):
        m = ModuleManifest(
            slug="test.full",
            label="Full Test",
            django_apps=("myapp",),
            provides=("my.cap",),
            settings_declarations=(ModuleSettingsDeclaration(key="key_x"),),
            migration_apps=(ModuleMigrationDeclaration(app_label="myapp"),),
            permissions=(ModulePermissionDeclaration(codename="do_x", name="Do X", app_label="myapp"),),
            navigation=(ModuleNavigationDeclaration(key="mykey", label="My Key"),),
            ai_tools=("my.tool",),
            prompt_templates=("my.tool",),
            provided_capabilities=(ProvidedCapability(slug="my.cap"),),
        )
        json.dumps(m.to_dict())  # must not raise

    def test_manifest_without_new_fields_round_trips_cleanly(self):
        # Existing manifests serialised without new fields must still deserialise.
        old_dict = {
            "slug": "old.module",
            "label": "Old Module",
            "version": "0.1.0",
            "cauldron_version": ">=0.1.0",
            "django_apps": ["myapp"],
            "django_middleware": [],
            "django_context_processors": [],
            "settings": {},
            "requires": [],
            "optional": [],
            "provides": ["some.cap"],
            "namespaces": ["myapp_ns"],
            "public_api": ["myapp_ns.api"],
            "capability_implementations": [],
        }
        m = ModuleManifest.from_dict(old_dict)
        assert m.slug == "old.module"
        assert m.settings_declarations == ()
        assert m.migration_apps == ()
        assert m.permissions == ()
        assert m.navigation == ()
        assert m.ai_tools == ()
        assert m.prompt_templates == ()
        assert m.provided_capabilities == ()
        # Confirm round-trip of the enriched object.
        assert ModuleManifest.from_dict(m.to_dict()) == m


# ---------------------------------------------------------------------------
# All current modules load successfully
# ---------------------------------------------------------------------------

class TestAllCurrentModulesLoad:
    """Verify every shipped module.py constructs a valid ModuleManifest."""

    def _import_module(self, dotted: str) -> ModuleManifest:
        import importlib
        import pytest
        pkg = dotted.split(".")[0]
        try:
            mod = importlib.import_module(dotted)
        except ModuleNotFoundError:
            pytest.skip(f"{pkg} not installed in this environment")
        manifest: ModuleManifest = mod._manifest
        assert isinstance(manifest, ModuleManifest)
        return manifest

    def _try_import_manifest(self, dotted: str):
        import importlib
        pkg = dotted.split(".")[0]
        try:
            mod = importlib.import_module(dotted)
        except ModuleNotFoundError:
            return None
        return mod._manifest

    def test_cauldron_django_state(self):
        m = self._import_module("cauldron_django_state.module")
        assert m.slug == "cauldron.django.state"
        assert len(m.settings_declarations) == 1

    def test_cauldron_django_auth(self):
        m = self._import_module("cauldron_django_auth.module")
        assert m.slug == "cauldron.django.auth"
        assert len(m.migration_apps) == 3
        labels = {d.app_label for d in m.migration_apps}
        assert labels == {"contenttypes", "auth", "sessions"}

    def test_cauldron_django_admin(self):
        m = self._import_module("cauldron_django_admin.module")
        assert m.slug == "cauldron.django.admin"
        assert len(m.settings_declarations) == 1
        assert m.settings_declarations[0].setting_path == "CAULDRON_UI_OVERRIDES_DIR"
        assert len(m.navigation) == 4
        nav_by_key = {n.key: n for n in m.navigation}
        assert nav_by_key["cauldron.dashboard"].url_name == "cauldron:dashboard"
        assert nav_by_key["cauldron.dashboard"].url_prefix_exact is True
        assert nav_by_key["cauldron.modules"].url_prefix == "/cauldron/modules/"

    def test_cauldron_content(self):
        m = self._import_module("cauldron_content.module")
        assert m.slug == "cauldron.content"
        assert len(m.settings_declarations) == 1

    def test_cauldron_cms_flatfile(self):
        m = self._import_module("cauldron_cms_flatfile.module")
        assert m.slug == "cauldron.cms.flatfile"
        assert len(m.settings_declarations) == 3

    def test_cauldron_workspace_flatfile(self):
        m = self._import_module("cauldron_workspace_flatfile.module")
        assert m.slug == "cauldron.workspace.flatfile"
        assert len(m.settings_declarations) == 1

    def test_cauldron_content_operations(self):
        m = self._import_module("cauldron_content_operations.module")
        assert m.slug == "cauldron.content.operations"
        assert len(m.migration_apps) == 1
        assert m.migration_apps[0].app_label == "cauldron_content_operations"
        assert len(m.permissions) == 10

    def test_cauldron_content_api(self):
        m = self._import_module("cauldron_content_api.module")
        assert m.slug == "cauldron.content.api"

    def test_cauldron_admin_content(self):
        m = self._import_module("cauldron_admin_content.module")
        assert m.slug == "cauldron.admin.content"
        assert len(m.navigation) == 6

    def test_cauldron_ai(self):
        m = self._import_module("cauldron_ai.module")
        assert m.slug == "cauldron.ai"
        assert len(m.provided_capabilities) == 3
        assert m.requires_restart is False

    def test_cauldron_ai_openai(self):
        m = self._import_module("cauldron_ai_openai.module")
        assert m.slug == "cauldron.ai.openai"

    def test_cauldron_ai_admin(self):
        m = self._import_module("cauldron_ai_admin.module")
        assert m.slug == "cauldron.ai.admin"
        assert len(m.settings_declarations) == 2
        assert len(m.migration_apps) == 1
        assert len(m.permissions) == 8
        assert len(m.navigation) == 5
        assert len(m.ai_tools) == 12
        assert len(m.prompt_templates) == 12

    def test_cauldron_site_astro(self):
        m = self._import_module("cauldron_site_astro.module")
        assert m.slug == "cauldron.site.astro"
        assert len(m.settings_declarations) == 9
        assert len(m.migration_apps) == 1
        assert len(m.ai_tools) == 5
        assert len(m.prompt_templates) == 5

    def test_all_modules_requires_restart_consistent(self):
        """Every module with django_apps has requires_restart=True."""
        module_paths = [
            "cauldron_django_state.module",
            "cauldron_django_auth.module",
            "cauldron_django_admin.module",
            "cauldron_content.module",
            "cauldron_cms_flatfile.module",
            "cauldron_workspace_flatfile.module",
            "cauldron_content_operations.module",
            "cauldron_content_api.module",
            "cauldron_admin_content.module",
            "cauldron_ai.module",
            "cauldron_ai_openai.module",
            "cauldron_ai_admin.module",
            "cauldron_site_astro.module",
        ]
        checked = 0
        for dotted in module_paths:
            m = self._try_import_manifest(dotted)
            if m is None:
                continue
            expected = bool(
                m.django_apps
                or m.django_middleware
                or m.django_context_processors
                or m.restart_required
            )
            assert m.requires_restart == expected, (
                f"{m.slug}: requires_restart={m.requires_restart!r} but "
                f"django_apps={m.django_apps!r}, restart_required={m.restart_required!r}"
            )
            checked += 1
        assert checked > 0, "No modules were importable — check package installation"

    def test_all_module_manifests_round_trip(self):
        """Every shipped manifest survives to_dict → from_dict with equality."""
        module_paths = [
            "cauldron_django_state.module",
            "cauldron_django_auth.module",
            "cauldron_django_admin.module",
            "cauldron_content.module",
            "cauldron_cms_flatfile.module",
            "cauldron_workspace_flatfile.module",
            "cauldron_content_operations.module",
            "cauldron_content_api.module",
            "cauldron_admin_content.module",
            "cauldron_ai.module",
            "cauldron_ai_openai.module",
            "cauldron_ai_admin.module",
            "cauldron_site_astro.module",
        ]
        checked = 0
        for dotted in module_paths:
            m = self._try_import_manifest(dotted)
            if m is None:
                continue
            assert ModuleManifest.from_dict(m.to_dict()) == m, (
                f"{m.slug}: round-trip failed"
            )
            checked += 1
        assert checked > 0, "No modules were importable — check package installation"

    def test_all_module_manifests_json_serializable(self):
        """Every shipped manifest produces JSON-serializable output from to_dict."""
        module_paths = [
            "cauldron_django_state.module",
            "cauldron_django_auth.module",
            "cauldron_django_admin.module",
            "cauldron_content.module",
            "cauldron_cms_flatfile.module",
            "cauldron_workspace_flatfile.module",
            "cauldron_content_operations.module",
            "cauldron_content_api.module",
            "cauldron_admin_content.module",
            "cauldron_ai.module",
            "cauldron_ai_openai.module",
            "cauldron_ai_admin.module",
            "cauldron_site_astro.module",
        ]
        checked = 0
        for dotted in module_paths:
            m = self._try_import_manifest(dotted)
            if m is None:
                continue
            try:
                json.dumps(m.to_dict())
            except (TypeError, ValueError) as exc:
                raise AssertionError(f"{m.slug}: to_dict is not JSON-serializable: {exc}") from exc
            checked += 1
        assert checked > 0, "No modules were importable — check package installation"


# ---------------------------------------------------------------------------
# BaseModule and CauldronModule protocol
# ---------------------------------------------------------------------------

class TestBaseModule:
    def _make(self, **kwargs) -> BaseModule:
        manifest = ModuleManifest(slug="test.module", label="Test Module", **kwargs)
        return BaseModule(manifest)

    def test_slug_and_label_from_manifest(self):
        mod = self._make()
        assert mod.slug == "test.module"
        assert mod.label == "Test Module"

    def test_manifest_accessible(self):
        mod = self._make(version="1.2.3")
        assert mod.manifest.version == "1.2.3"

    def test_django_apps_delegates_to_manifest(self):
        mod = self._make(django_apps=("myapp", "otherapp"))
        assert list(mod.django_apps()) == ["myapp", "otherapp"]

    def test_django_apps_empty_by_default(self):
        mod = self._make()
        assert list(mod.django_apps()) == []

    def test_on_ready_is_callable(self):
        mod = self._make()
        mod.on_ready()  # must not raise

    def test_register_is_callable(self):
        from cauldron.modules import ModuleContext

        mod = self._make()
        ctx = ModuleContext(slug="test.module", config={})
        mod.register(ctx)  # must not raise

    def test_satisfies_protocol(self):
        mod = self._make()
        assert isinstance(mod, CauldronModule)


class TestModuleContext:
    def test_frozen(self):
        from cauldron.modules import ModuleContext

        ctx = ModuleContext(slug="a", config={})
        with pytest.raises(Exception):
            ctx.slug = "b"  # type: ignore[misc]

    def test_slug_and_config_accessible(self):
        from cauldron.modules import ModuleContext

        ctx = ModuleContext(slug="my.module", config={"k": "v"})
        assert ctx.slug == "my.module"
        assert ctx.config == {"k": "v"}
