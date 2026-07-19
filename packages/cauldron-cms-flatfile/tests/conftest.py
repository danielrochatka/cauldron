"""Test configuration for cauldron-cms-flatfile package tests."""
import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PARITY_FIXTURES = REPO_ROOT / "fixtures" / "content-parity"


def pytest_configure(config):
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "cauldron",
                "cauldron_content",
                "cauldron_cms_flatfile",
            ],
            CAULDRON_MODULES={
                "cauldron.content": {},
                "cauldron.cms.flatfile": {},
            },
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
        )


@pytest.fixture
def parity_dir() -> Path:
    return PARITY_FIXTURES


@pytest.fixture
def temp_site(tmp_path: Path) -> Path:
    """Copy the parity fixtures into a temporary site tree."""
    site = tmp_path / "site"
    content_src = PARITY_FIXTURES
    (site / "content" / "pages").mkdir(parents=True)
    (site / "content" / "posts").mkdir(parents=True)
    (site / "schemas").mkdir(parents=True)
    for name in ("home.md", "about.md", "draft-page.md"):
        shutil.copy2(content_src / "pages" / name, site / "content" / "pages" / name)
    shutil.copy2(
        content_src / "posts" / "first-post.md",
        site / "content" / "posts" / "first-post.md",
    )
    for name in ("pages.schema.json", "posts.schema.json"):
        shutil.copy2(content_src / "schemas" / name, site / "schemas" / name)
    return site
