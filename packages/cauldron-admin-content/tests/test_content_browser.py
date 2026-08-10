"""Regression tests for ContentBrowserView: Preview/View buttons.

Covers:
- Regular published pages: View button links to /{slug}/
- Homepage published: View button links to / (not /homepage/)
- Draft pages: Preview button links to page-detail admin URL
- Read-only users (view_published_content only): see View/Preview, no Edit/Publish
- Non-pages collections: no Actions column, no View/Preview buttons
- No site capability registered: View button absent (graceful degradation)
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db

_URL = "/cauldron-admin/content/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_perm(codename):
    from django.contrib.auth.models import Permission
    return Permission.objects.get(
        codename=codename,
        content_type__app_label="cauldron_content_operations",
    )


def _make_user(username, perms=()):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username=username, password="pw")
    for codename in perms:
        try:
            user.user_permissions.add(_get_perm(codename))
        except Exception:
            pass
    return User.objects.get(pk=user.pk)


def _item(item_id, slug, status="published", schema="page", collection="pages", provider="flatfile"):
    """Return a to_dict()-style dict matching ContentBrowserView's item format."""
    return {
        "id": item_id,
        "slug": slug,
        "status": status,
        "schema": schema,
        "provider": provider,
        "hash": "abc123def456",
        "body": "# Hello",
        "collection": collection,
        "data": {"title": f"Page {slug}"},
    }


def _mock_service(items):
    """Return a mock ContentOperationService that yields the given item dicts.

    list_items returns SimpleNamespace objects with a to_dict() method so that
    ContentBrowserView's [item.to_dict() for item in items_raw] works correctly.
    """
    ns_items = [SimpleNamespace(**d, to_dict=lambda d=d: d) for d in items]
    svc = MagicMock()
    svc.list_collections.return_value = [SimpleNamespace(name="pages")]
    svc.list_items.return_value = ns_items
    return svc


class FakeSitePublicUrlProvider:
    """Local fake that mirrors AstroPublicUrlProvider behaviour.

    Used in tests to avoid importing the concrete provider from a sibling
    package (ARCH003).  Implements the same rules:
    - Non-pages collections → None
    - Homepage item → "/"
    - All other pages → "/{slug}/"
    """

    _PAGE_COLLECTION = "pages"
    _HOMEPAGE_ITEM_ID = "homepage"

    def get_public_url(self, *, item_id: str, slug: str, collection: str) -> str | None:
        if collection != self._PAGE_COLLECTION:
            return None
        if item_id == self._HOMEPAGE_ITEM_ID:
            return "/"
        return f"/{slug}/"


def _astro_provider():
    return FakeSitePublicUrlProvider()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_public_url_provider():
    """Ensure public URL provider state is clean between tests."""
    from cauldron_content.site import register_public_url_provider
    original_provider = None
    import cauldron_content.site as site_mod
    original_provider = site_mod._provider
    yield
    register_public_url_provider(original_provider)


# ---------------------------------------------------------------------------
# Regular published page → View button at /{slug}/
# ---------------------------------------------------------------------------

def test_view_button_regular_published_page(client):
    from django.test import override_settings
    from cauldron_content.site import register_public_url_provider

    register_public_url_provider(_astro_provider())
    user = _make_user("viewer_reg", ["view_published_content", "view_draft_content"])
    client.force_login(user)

    items = [_item("about", "about", status="published")]
    svc = _mock_service(items)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc):
            resp = client.get(_URL + "?collection=pages")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'href="/about/"' in content
    assert "View" in content


# ---------------------------------------------------------------------------
# Homepage published → View button at / (not /homepage/)
# ---------------------------------------------------------------------------

def test_view_button_homepage_routes_to_root(client):
    from django.test import override_settings
    from cauldron_content.site import register_public_url_provider
    from cauldron_content.homepage import HOMEPAGE_ITEM_ID

    register_public_url_provider(_astro_provider())
    user = _make_user("viewer_home", ["view_published_content", "view_draft_content"])
    client.force_login(user)

    items = [_item(HOMEPAGE_ITEM_ID, HOMEPAGE_ITEM_ID, status="published")]
    svc = _mock_service(items)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc):
            resp = client.get(_URL + "?collection=pages")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'href="/"' in content
    assert 'href="/homepage/"' not in content


# ---------------------------------------------------------------------------
# Draft page → Preview button (no View button)
# ---------------------------------------------------------------------------

def test_preview_button_draft_page(client):
    from django.test import override_settings
    from cauldron_content.site import register_public_url_provider

    register_public_url_provider(_astro_provider())
    user = _make_user("viewer_draft", ["view_published_content", "view_draft_content"])
    client.force_login(user)

    items = [_item("services", "services", status="draft")]
    svc = _mock_service(items)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc):
            resp = client.get(_URL + "?collection=pages")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Preview" in content
    assert "View" not in content
    # Preview must point to the page-detail admin URL
    assert "/content/pages/services/" in content


# ---------------------------------------------------------------------------
# Read-only user: View/Preview visible; Edit/Publish absent
# ---------------------------------------------------------------------------

def test_read_only_user_sees_view_not_edit(client):
    from django.test import override_settings
    from cauldron_content.site import register_public_url_provider

    register_public_url_provider(_astro_provider())
    # view_published_content + view_draft_content but NOT propose/publish
    user = _make_user("readonly", ["view_published_content", "view_draft_content"])
    client.force_login(user)

    items = [
        _item("published-pg", "published-pg", status="published"),
        _item("draft-pg", "draft-pg", status="draft"),
    ]
    svc = _mock_service(items)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc):
            resp = client.get(_URL + "?collection=pages")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "View" in content
    assert "Preview" in content
    assert "Edit" not in content
    assert "Publish" not in content


# ---------------------------------------------------------------------------
# Non-pages collection: no View/Preview, no Actions column
# ---------------------------------------------------------------------------

def test_non_pages_collection_has_no_view_preview(client):
    from django.test import override_settings
    from cauldron_content.site import register_public_url_provider

    register_public_url_provider(_astro_provider())
    user = _make_user("viewer_other", ["view_published_content"])
    client.force_login(user)

    svc = MagicMock()
    svc.list_collections.return_value = [SimpleNamespace(name="blog")]
    blog_item = SimpleNamespace(
        id="post-1", slug="post-1", status="published", schema="post",
        provider="flatfile", hash="xyz", body="", collection="blog",
        data={"title": "Post 1"},
        to_dict=lambda: {
            "id": "post-1", "slug": "post-1", "status": "published",
            "schema": "post", "provider": "flatfile", "hash": "xyz",
            "body": "", "collection": "blog", "data": {"title": "Post 1"},
        },
    )
    svc.list_items.return_value = [blog_item]

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc):
            resp = client.get(_URL + "?collection=blog")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "View" not in content
    assert "Preview" not in content
    assert "Actions" not in content


# ---------------------------------------------------------------------------
# No site capability registered → View button absent
# ---------------------------------------------------------------------------

def test_no_site_capability_hides_view_button(client):
    from django.test import override_settings
    from cauldron_content.site import register_public_url_provider

    register_public_url_provider(None)  # explicitly no provider
    user = _make_user("viewer_nocap", ["view_published_content", "view_draft_content"])
    client.force_login(user)

    items = [_item("contact", "contact", status="published")]
    svc = _mock_service(items)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc):
            resp = client.get(_URL + "?collection=pages")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "View" not in content


# ---------------------------------------------------------------------------
# Pending revision indicator: published items with active ContentChangeRequests
# ---------------------------------------------------------------------------

def _make_pending_indicator_service(item_id, *, pending_op_item_id=None):
    """Return a mock service where _workspace yields a pending op for pending_op_item_id."""
    from types import SimpleNamespace
    items = [_item(item_id, "my-page", status="published")]
    svc = _mock_service(items)
    if pending_op_item_id is not None:
        fake_op = SimpleNamespace(item_id=pending_op_item_id, kind="update", slug="my-page")
        fake_changeset = SimpleNamespace(operations=[fake_op])
        fake_workspace = MagicMock()
        fake_workspace.load_changeset.return_value = fake_changeset
        svc._workspace = fake_workspace
    else:
        svc._workspace = None
    return svc


def test_pending_indicator_shown_for_published_item_with_active_cr(client):
    """A published item with a proposed ContentChangeRequest shows a Pending badge."""
    from django.test import override_settings
    from cauldron_content_operations.models import ContentChangeRequest

    item_id = "page-with-pending"
    cr = ContentChangeRequest.objects.create(
        request_id="cr-pending-1",
        lifecycle_state="proposed",
        request_version=1,
        provider_name="",
        workspace_changeset_id="ws-pending-1",
    )

    user = _make_user("pending_viewer1", ["view_published_content", "view_draft_content"])
    client.force_login(user)
    svc = _make_pending_indicator_service(item_id, pending_op_item_id=item_id)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc):
            resp = client.get(_URL + "?collection=pages")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Pending" in content


def test_no_pending_indicator_without_active_cr(client):
    """A published item with no pending ContentChangeRequest shows no Pending badge."""
    from django.test import override_settings

    user = _make_user("pending_viewer2", ["view_published_content"])
    client.force_login(user)
    svc = _make_pending_indicator_service("page-clean", pending_op_item_id=None)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc):
            resp = client.get(_URL + "?collection=pages")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Pending" not in content


def test_pending_indicator_only_for_matching_item(client):
    """Pending badge appears only on the item whose ID matches the active CR operation."""
    from django.test import override_settings
    from cauldron_content_operations.models import ContentChangeRequest
    from types import SimpleNamespace

    cr = ContentChangeRequest.objects.create(
        request_id="cr-pending-2",
        lifecycle_state="validated",
        request_version=1,
        provider_name="",
        workspace_changeset_id="ws-pending-2",
    )

    affected_id = "page-affected"
    unaffected_id = "page-clean"

    items_data = [
        _item(affected_id, "affected", status="published"),
        _item(unaffected_id, "clean", status="published"),
    ]
    ns_items = [SimpleNamespace(**d, to_dict=lambda d=d: d) for d in items_data]
    svc = MagicMock()
    svc.list_collections.return_value = [SimpleNamespace(name="pages")]
    svc.list_items.return_value = ns_items

    fake_op = SimpleNamespace(item_id=affected_id, kind="update", slug="affected")
    fake_changeset = SimpleNamespace(operations=[fake_op])
    fake_workspace = MagicMock()
    fake_workspace.load_changeset.return_value = fake_changeset
    svc._workspace = fake_workspace

    user = _make_user("pending_viewer3", ["view_published_content", "view_draft_content"])
    client.force_login(user)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc):
            resp = client.get(_URL + "?collection=pages")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Pending" in content
    # Only one badge (for affected_id, not unaffected_id)
    assert content.count(">Pending<") == 1


def test_browser_draft_publish_button_says_review_when_site_astro(client):
    """When Site Astro is installed, the draft-page Publish button reads 'Review & Preview'."""
    from django.test import override_settings

    user = _make_user("astro_browser1", [
        "view_published_content",
        "view_draft_content",
        "propose_content_changes",
        "validate_content_changes",
        "apply_content_changes",
    ])
    client.force_login(user)

    items = [_item("draft-page-1", "draft-page", status="draft")]
    svc = _mock_service(items)
    svc._workspace = None

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc), \
             patch("cauldron_admin_content.views._site_astro_installed", return_value=True):
            resp = client.get(_URL + "?collection=pages")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Review" in content and "Preview" in content


def test_browser_draft_publish_button_says_publish_without_site_astro(client):
    """Without Site Astro, the draft-page button reads 'Publish'."""
    from django.test import override_settings

    user = _make_user("noastro_browser1", [
        "view_published_content",
        "view_draft_content",
        "propose_content_changes",
        "validate_content_changes",
        "apply_content_changes",
    ])
    client.force_login(user)

    items = [_item("draft-page-2", "draft-page-2", status="draft")]
    svc = _mock_service(items)
    svc._workspace = None

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=svc), \
             patch("cauldron_admin_content.views._site_astro_installed", return_value=False):
            resp = client.get(_URL + "?collection=pages")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'value="publish"' in content
