"""Tests for SitePublicUrlProvider registration."""
import pytest

from cauldron_content.site import (
    _reset_public_url_provider_for_tests,
    get_public_url,
    has_public_url_provider,
    register_public_url_provider,
)


class _FakeProvider:
    def get_public_url(self, *, item_id, slug, collection):
        return f"https://example.com/{collection}/{slug}"


class _OtherProvider:
    def get_public_url(self, *, item_id, slug, collection):
        return f"https://other.com/{collection}/{slug}"


@pytest.fixture(autouse=True)
def _clean_provider():
    _reset_public_url_provider_for_tests()
    yield
    _reset_public_url_provider_for_tests()


def test_no_provider_returns_none():
    assert has_public_url_provider() is False
    assert get_public_url(item_id="1", slug="my-page", collection="pages") is None


def test_register_and_get():
    p = _FakeProvider()
    register_public_url_provider(p)
    assert has_public_url_provider() is True
    result = get_public_url(item_id="1", slug="my-page", collection="pages")
    assert result == "https://example.com/pages/my-page"


def test_same_instance_reregistration_is_idempotent():
    p = _FakeProvider()
    register_public_url_provider(p)
    register_public_url_provider(p)  # must not raise
    assert has_public_url_provider() is True


def test_same_class_reregistration_is_idempotent():
    """A new instance of the same class (e.g. Django autoreload) is allowed."""
    register_public_url_provider(_FakeProvider())
    register_public_url_provider(_FakeProvider())  # different instance, same type — no error
    assert has_public_url_provider() is True


def test_different_class_raises():
    """Registering a different provider class when one is already set raises."""
    register_public_url_provider(_FakeProvider())
    with pytest.raises(ValueError, match="already registered"):
        register_public_url_provider(_OtherProvider())


def test_different_class_error_includes_owner_hint():
    register_public_url_provider(_FakeProvider(), owning_module="cauldron.site.astro")
    with pytest.raises(ValueError, match="cauldron.site.astro"):
        register_public_url_provider(_OtherProvider())


def test_register_none_clears_provider():
    register_public_url_provider(_FakeProvider())
    register_public_url_provider(None)
    assert has_public_url_provider() is False


def test_owning_module_recorded():
    register_public_url_provider(_FakeProvider(), owning_module="cauldron.site.astro")
    assert has_public_url_provider() is True
