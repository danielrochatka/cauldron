"""Test configuration for cauldron-site-astro package tests."""
from unittest.mock import patch

import pytest


def _make_fake_cr_dict(req_ids):
    """Return a dict of {req_id: mock_cr} suitable for bypassing DB lookups."""
    from types import SimpleNamespace
    return {
        req_id: SimpleNamespace(lifecycle_state="proposed", request_version=1)
        for req_id in req_ids
    }


@pytest.fixture
def bypass_db_integrity():
    """Patch publication_service DB validators so tests that focus on build
    logic, CSS handoff, or signal suppression don't need real
    ContentChangeRequest rows in the test database.

    Patches:
      - _check_request_integrity → always passes (True, "")
      - _extract_draft_items (publication_service) → returns empty lists
      - _fetch_eligible_change_requests → returns mock CRs for any req_ids
    """
    from unittest.mock import MagicMock

    def _fake_fetch(content_request_ids, allowed_states, require_approval):
        return _make_fake_cr_dict(content_request_ids), None

    with (
        patch(
            "cauldron_site_astro.publication_service._check_request_integrity",
            return_value=(True, ""),
        ),
        patch(
            "cauldron_site_astro.publication_service._extract_draft_items",
            return_value=([], [], []),
        ),
        patch(
            "cauldron_site_astro.publication_service._fetch_eligible_change_requests",
            side_effect=_fake_fetch,
        ),
    ):
        yield


@pytest.fixture
def bypass_integrity_only():
    """Patch only _check_request_integrity (not _extract_draft_items).

    Use for tests that need the DB integrity gate bypassed but still want
    _extract_draft_items to run normally (e.g. to verify extra_items plumbing).
    """
    with patch(
        "cauldron_site_astro.publication_service._check_request_integrity",
        return_value=(True, ""),
    ):
        yield


def pytest_configure(config):
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
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
                }
            },
            ROOT_URLCONF="tests.urls",
            SECRET_KEY="test-key",
        )
