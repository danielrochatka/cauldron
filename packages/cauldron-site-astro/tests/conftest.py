"""Test configuration for cauldron-site-astro package tests."""
import pytest


@pytest.fixture(autouse=True)
def _bypass_integrity_check(monkeypatch):
    """Patch integrity-check helpers to pass for all site-astro tests.

    Tests in this suite use synthetic request IDs that have no matching DB
    rows. The integrity guards are prod-time safeguards against missing/terminal
    requests, but they are irrelevant to the build-flow, CSS-handoff, and
    workflow-state concerns that these tests cover.

    Two functions are patched:
    - ``_check_request_integrity``: called by ``prepare()`` before creating a
      SiteChangeSet.
    - ``_fetch_eligible_change_requests``: called by ``publish()`` to load and
      lifecycle-check change requests before the build step.

    Tests that specifically exercise integrity rejection should create real
    ContentChangeRequest records (and not rely on this fixture).
    """
    from types import SimpleNamespace

    monkeypatch.setattr(
        "cauldron_site_astro.publication_service._check_request_integrity",
        lambda ids, *, require_approval=False: (True, ""),
    )

    def _mock_fetch_eligible(request_ids, allowed_states, require_approval):
        return (
            {
                req_id: SimpleNamespace(lifecycle_state="proposed", request_version=1)
                for req_id in request_ids
            },
            None,
        )

    monkeypatch.setattr(
        "cauldron_site_astro.publication_service._fetch_eligible_change_requests",
        _mock_fetch_eligible,
    )


def pytest_configure(config):
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                # Correction 2: publication_service.prepare() now runs an
                # integrity check that reads ContentChangeRequest. Include the
                # operations app so the model + migrations are available in
                # the site-astro test suite. Its dependency chain
                # (cauldron_content, cauldron_workspace_flatfile) must also
                # be installed for the migrations to apply.
                "cauldron_content",
                "cauldron_workspace_flatfile",
                "cauldron_content_operations",
                "cauldron_site_astro",
            ],
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            CAULDRON_MODULES={
                "cauldron.site.astro": {
                    "frontend_root": "/tmp/nonexistent_frontend",
                    "output_root": "/tmp/cauldron_test_output",
                },
                "cauldron.content.operations": {
                    "require_approval": False,
                    "max_operations_per_change_set": 100,
                },
            },
            ROOT_URLCONF="tests.urls",
            SECRET_KEY="test-key",
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
        )
