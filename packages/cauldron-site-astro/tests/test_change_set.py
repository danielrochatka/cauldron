"""Tests for SiteChangeSet model."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.django_db


def test_site_change_set_default_status():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create()
    assert cs.status == SiteChangeSet.PREPARING


def test_site_change_set_str_representation():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create(status=SiteChangeSet.DRAFT_READY)
    s = str(cs)
    assert "SiteChangeSet(" in s
    assert "draft_ready" in s


def test_site_change_set_uuid_primary_key():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create()
    assert isinstance(cs.id, uuid.UUID)


def test_site_change_set_content_request_ids_default_empty():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create()
    assert cs.content_request_ids == []


def test_site_change_set_page_routes_default_empty():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create()
    assert cs.page_routes == []


def test_site_change_set_publish_build_result_default_empty():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create()
    assert cs.publish_build_result == {}


def test_site_change_set_staged_theme_css_blank():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create()
    assert cs.staged_theme_css == ""


def test_site_change_set_status_choices():
    from cauldron_site_astro.models import SiteChangeSet
    valid_statuses = {choice[0] for choice in SiteChangeSet.STATUS_CHOICES}
    assert SiteChangeSet.PREPARING in valid_statuses
    assert SiteChangeSet.DRAFT_READY in valid_statuses
    assert SiteChangeSet.PREVIEW_FAILED in valid_statuses
    assert SiteChangeSet.PUBLISHING in valid_statuses
    assert SiteChangeSet.PUBLISHED in valid_statuses
    assert SiteChangeSet.PUBLISH_FAILED in valid_statuses


def test_site_change_set_can_set_content_request_ids():
    from cauldron_site_astro.models import SiteChangeSet
    ids = ["req-1", "req-2"]
    cs = SiteChangeSet.objects.create(content_request_ids=ids)
    cs.refresh_from_db()
    assert cs.content_request_ids == ids


def test_site_change_set_can_set_staged_theme_css():
    from cauldron_site_astro.models import SiteChangeSet
    css = "body { color: red; }"
    cs = SiteChangeSet.objects.create(staged_theme_css=css)
    cs.refresh_from_db()
    assert cs.staged_theme_css == css


def test_site_change_set_originating_run_id_nullable():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create(originating_run_id=None)
    assert cs.originating_run_id is None


def test_site_change_set_originating_run_id_can_be_set():
    from cauldron_site_astro.models import SiteChangeSet
    run_id = uuid.uuid4()
    cs = SiteChangeSet.objects.create(originating_run_id=run_id)
    cs.refresh_from_db()
    assert cs.originating_run_id == run_id


def test_site_change_set_preview_dir_default_blank():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create()
    assert cs.preview_dir == ""


def test_site_change_set_ordering_most_recent_first():
    from cauldron_site_astro.models import SiteChangeSet
    cs1 = SiteChangeSet.objects.create()
    cs2 = SiteChangeSet.objects.create()
    qs = list(SiteChangeSet.objects.all())
    # Most recently created should be first
    assert qs[0].id == cs2.id
    assert qs[1].id == cs1.id


def test_site_change_set_creator_nullable():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create(creator=None)
    assert cs.creator is None


def test_site_change_set_creator_can_be_set():
    from django.contrib.auth import get_user_model
    from cauldron_site_astro.models import SiteChangeSet
    User = get_user_model()
    user = User.objects.create_user(username="test-creator", password="pw")
    cs = SiteChangeSet.objects.create(creator=user)
    cs.refresh_from_db()
    assert cs.creator == user


def test_site_change_set_timestamps_auto_set():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create()
    assert cs.created_at is not None
    assert cs.updated_at is not None


def test_site_change_set_draft_ready_at_nullable():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create()
    assert cs.draft_ready_at is None


def test_site_change_set_published_at_nullable():
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create()
    assert cs.published_at is None
