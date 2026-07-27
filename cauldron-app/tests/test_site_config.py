"""Integration tests for the self-hosted Cauldron site configuration.

Verifies that the stock checkout has a valid cauldron.cms.flatfile layout and
that the flatfile provider can be registered from real settings. These tests
act as a regression guard: any change that points site_root at a non-existent
directory will be caught here before it reaches ./update.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatfile_cfg() -> dict:
    return (getattr(settings, "CAULDRON_MODULES", {}) or {}).get("cauldron.cms.flatfile", {})


def _site_root() -> Path:
    return Path(_flatfile_cfg().get("site_root", ""))


# ---------------------------------------------------------------------------
# site_root layout assertions
# ---------------------------------------------------------------------------

class TestSiteRootLayout:
    def test_site_root_is_configured(self):
        cfg = _flatfile_cfg()
        assert cfg.get("site_root"), "cauldron.cms.flatfile.site_root must be set"

    def test_site_root_exists(self):
        root = _site_root()
        assert root.is_dir(), (
            f"cauldron.cms.flatfile.site_root {str(root)!r} does not exist on disk. "
            "PR #40 regression: the configured site_root must be an existing directory "
            "so that manage.py check and ./update succeed."
        )

    def test_site_root_content_dir_resolves_to_cauldron_app_content(self):
        content_dir = _site_root() / "content"
        assert content_dir.is_dir(), (
            f"Expected {str(content_dir)!r} to exist. "
            "The flatfile CMS module stores pages under site_root/content."
        )

    def test_site_root_schemas_dir_resolves_to_cauldron_app_schemas(self):
        schemas_dir = _site_root() / "schemas"
        assert schemas_dir.is_dir(), (
            f"Expected {str(schemas_dir)!r} to exist. "
            "The flatfile CMS module loads schemas from site_root/schemas."
        )

    def test_page_schema_is_present(self):
        schema_file = _site_root() / "schemas" / "page.schema.json"
        assert schema_file.is_file(), (
            f"Page schema not found at {str(schema_file)!r}. "
            "The page schema must ship with the self-hosted checkout."
        )

    def test_page_schema_is_valid_json(self):
        import json
        schema_file = _site_root() / "schemas" / "page.schema.json"
        text = schema_file.read_text()
        doc = json.loads(text)
        assert doc.get("type") == "object", "page.schema.json must describe an object type"


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------

class TestFlatfileProviderRegistration:
    @pytest.fixture(autouse=True)
    def reset_registry(self):
        from cauldron_content.registry import registry
        registry.reset()
        yield
        registry.reset()

    def test_flatfile_provider_registers_successfully(self):
        from cauldron_cms_flatfile.apps import _register_provider
        from cauldron_content.registry import registry
        _register_provider()
        assert registry.get("flatfile") is not None, (
            "Flatfile provider failed to register. "
            "Check that site_root exists and points to a valid CMS directory."
        )

    def test_flatfile_provider_idempotent_on_double_register(self):
        from cauldron_cms_flatfile.apps import _register_provider
        from cauldron_content.registry import registry
        _register_provider()
        repo_first = registry.get("flatfile")
        _register_provider()
        assert registry.get("flatfile") is repo_first

    def test_content_dir_readable_after_provider_reconstruction(self, tmp_path):
        """Content written before a registry reset is readable after provider reconstruction.

        Uses an isolated tmp_path site root so no real checkout files are touched.
        """
        import json
        import shutil
        from django.test import override_settings
        from cauldron_cms_flatfile.apps import _register_provider
        from cauldron_content.registry import registry
        from cauldron_content.contracts import (
            ContentChangeSet,
            ContentOperation,
            ContentOperationKind,
            ContentStatus,
        )

        # Build a minimal temporary site root with the same layout as the real
        # self-hosted checkout: content/ and schemas/page.schema.json.
        real_schema = _site_root() / "schemas" / "page.schema.json"
        tmp_schemas = tmp_path / "schemas"
        tmp_schemas.mkdir()
        shutil.copy(real_schema, tmp_schemas / "page.schema.json")
        (tmp_path / "content" / "pages").mkdir(parents=True)

        from django.conf import settings as djsettings
        tmp_modules = {
            **getattr(djsettings, "CAULDRON_MODULES", {}),
            "cauldron.cms.flatfile": {"site_root": str(tmp_path)},
        }
        with override_settings(CAULDRON_MODULES=tmp_modules):
            _register_provider()
            repo = registry.get("flatfile")
            assert repo is not None, "Provider did not register with temporary site_root"

            # Write a content item through the repository API.
            op = ContentOperation(
                kind=ContentOperationKind.CREATE,
                provider="flatfile",
                collection="pages",
                item_id="reconstruction-sentinel",
                slug="reconstruction-sentinel",
                status=ContentStatus.DRAFT,
                schema="page",
                data={"title": "Reconstruction Sentinel"},
                body="Sentinel body.",
            )
            result = repo.apply(ContentChangeSet(id="cs-reconstruction-001", operations=(op,)))
            assert result.success, f"apply failed: {result.validation_errors}"

            # Simulate a service restart: rebuild the registry from the same settings.
            registry.reset()
            _register_provider()
            repo2 = registry.get("flatfile")
            assert repo2 is not None

            items = repo2.list_items("pages", include_drafts=True)
            ids = [item.id for item in items]
            assert "reconstruction-sentinel" in ids, (
                "Item written before registry reset was not visible after provider "
                "reconstruction. site_root may not be persisting to the right path."
            )


# ---------------------------------------------------------------------------
# Django system check regression guard
# ---------------------------------------------------------------------------

class TestSystemChecks:
    def test_cms_flatfile_check_passes_with_no_errors(self):
        """cauldron.cms.flatfile system checks must emit no errors for the stock checkout.

        This is the regression guard for ./update: if site_root is missing or
        misconfigured, manage.py check exits non-zero and ./update aborts.
        """
        from django.core import checks
        from django.apps import apps

        # Run only the cauldron.cms.flatfile compatibility checks.
        all_results = checks.run_checks(
            app_configs=apps.get_app_configs(),
            tags=[checks.Tags.compatibility],
            include_deployment_checks=False,
        )
        errors = [r for r in all_results if r.level >= checks.ERROR]
        cms_errors = [r for r in errors if "cms.flatfile" in (r.id or "")]
        assert not cms_errors, (
            "cauldron.cms.flatfile system checks emitted errors:\n"
            + "\n".join(f"  {r.id}: {r.msg}" for r in cms_errors)
        )

    def test_site_root_setting_points_to_existing_directory(self):
        """Regression guard: site_root in settings must be an existing directory.

        A PR that changes cauldron.cms.flatfile.site_root to a non-existent path
        (such as BASE_DIR / 'site') will be caught here before reaching ./update.
        """
        root = _site_root()
        assert root != Path(""), "site_root must not be empty"
        assert root.is_absolute(), f"site_root {str(root)!r} must be an absolute path"
        assert root.is_dir(), (
            f"site_root {str(root)!r} does not exist. "
            "This setting must point to an existing directory that contains "
            "'content/' and 'schemas/' sub-directories."
        )
