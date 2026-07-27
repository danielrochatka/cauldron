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


class TestPublicAssetServing:
    def test_astro_asset_served(self, client, tmp_path):
        """/_astro/chunk.js is served with correct content."""
        astro_dir = tmp_path / "_astro"
        astro_dir.mkdir()
        (astro_dir / "chunk-abc123.js").write_bytes(b"console.log('hello');")

        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/_astro/chunk-abc123.js")

        assert response.status_code == 200
        content = b"".join(response.streaming_content)
        assert b"console.log" in content

    def test_astro_asset_content_type(self, client, tmp_path):
        """CSS assets are served with text/css content type."""
        astro_dir = tmp_path / "_astro"
        astro_dir.mkdir()
        (astro_dir / "style.css").write_bytes(b"body { color: red; }")

        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/_astro/style.css")

        assert response.status_code == 200
        assert "css" in response.get("Content-Type", "").lower()

    def test_nested_image_asset_served(self, client, tmp_path):
        """Nested public assets like images/hero.png are served."""
        img_dir = tmp_path / "images"
        img_dir.mkdir()
        (img_dir / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/images/hero.png")

        assert response.status_code == 200

    def test_asset_head_request_supported(self, client, tmp_path):
        """HEAD requests for assets return 200 (no body)."""
        astro_dir = tmp_path / "_astro"
        astro_dir.mkdir()
        (astro_dir / "chunk.js").write_bytes(b"x=1;")

        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.head("/_astro/chunk.js")

        assert response.status_code == 200

    def test_dotfile_asset_rejected(self, client, tmp_path):
        """Assets with dotfile components are rejected with 404."""
        hidden_dir = tmp_path / "_astro"
        hidden_dir.mkdir()
        (hidden_dir / ".hidden").write_bytes(b"secret")

        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/_astro/.hidden")

        # asset_path="_astro/.hidden" — the dotfile component ".hidden" is rejected
        assert response.status_code == 404

    def test_traversal_via_nested_asset_path_rejected(self, client, tmp_path):
        """Path traversal via nested asset path is rejected or normalised away."""
        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/_astro/../../../etc/passwd")

        # Django normalises the URL before dispatch; the path collapses so
        # it no longer matches the nested-asset pattern and ends up as a
        # slug redirect (302) or 404 — either is safe.
        assert response.status_code in (302, 404, 400)

    def test_missing_asset_returns_404(self, client, tmp_path):
        """Requesting a non-existent nested asset returns 404."""
        (tmp_path / "_astro").mkdir()

        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/_astro/nonexistent.js")

        assert response.status_code == 404

    def test_asset_handler_does_not_expose_output_root_siblings(self, client, tmp_path):
        """Asset handler cannot serve files outside output_root."""
        # Create a file in a sibling directory (not inside output_root)
        sibling = tmp_path.parent / "sibling_secret.txt"
        sibling.write_text("top secret")

        # Try to traverse to it via a nested path
        with override_settings(
            ROOT_URLCONF="tests.urls",
            CAULDRON_MODULES=_make_astro_module(str(tmp_path)),
        ):
            response = client.get("/_astro/../../sibling_secret.txt")

        # Django normalises the URL so traversal collapses; safe outcome is
        # 302 (slug redirect), 404, or 400 — none of which serve the file.
        assert response.status_code in (302, 404, 400)
