"""Tests for URL resolution."""
import pytest
from django.urls import reverse


def test_collections_url_resolves():
    url = reverse("cauldron_content_api:collections-list")
    assert url == "/collections/"


def test_change_requests_url_resolves():
    url = reverse("cauldron_content_api:change-requests-list")
    assert url == "/change-requests/"


def test_module_manifest():
    from cauldron_content_api.module import module
    assert module.slug == "cauldron.content.api"
    assert "content.httpapi" in module.manifest.provides
    assert "content.httpapi.v1" in module.manifest.provides
