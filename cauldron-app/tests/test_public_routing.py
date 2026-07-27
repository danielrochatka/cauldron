"""Tests for public static-site routing."""
import pytest
from pathlib import Path
from django.test import override_settings, Client
from unittest.mock import patch

pytestmark = pytest.mark.django_db


def _make_astro_module(output_root: str) -> dict:
    from django.conf import settings
    modules = dict(getattr(settings, "CAULDRON_MODULES", {}))
    modules["cauldron.site.astro"] = {
        "frontend_root": "/tmp/nonexistent",
        "output_root": output_root,
    }
    return modules


class TestPublicRouting:
    def test_index_serves_homepage_html(self, client, tmp_path):
        index = tmp_path / "index.html"
        index.write_text("<html><body>Hello Homepage</body></html>")

        from django.test import override_settings
        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/")
        assert response.status_code == 200
        content = b"".join(response.streaming_content)
        assert b"Hello Homepage" in content

    def test_slug_serves_page_html(self, client, tmp_path):
        about = tmp_path / "about"
        about.mkdir()
        (about / "index.html").write_text("<html><body>About Us</body></html>")

        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/about/")
        assert response.status_code == 200
        content = b"".join(response.streaming_content)
        assert b"About Us" in content

    def test_missing_page_returns_404(self, client, tmp_path):
        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/nonexistent/")
        assert response.status_code == 404

    def test_traversal_returns_404(self, client, tmp_path):
        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/../etc/passwd/")
        assert response.status_code in (404, 400)

    def test_no_output_root_returns_404(self, client):
        from django.conf import settings
        modules = dict(getattr(settings, "CAULDRON_MODULES", {}))
        modules.pop("cauldron.site.astro", None)
        with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=modules):
            response = client.get("/")
        assert response.status_code == 404

    def test_slug_without_trailing_slash_redirects(self, client, tmp_path):
        (tmp_path / "about").mkdir()
        (tmp_path / "about" / "index.html").write_text("<html>About</html>")
        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/about")
        assert response.status_code == 302
        assert response["Location"].endswith("/about/")
