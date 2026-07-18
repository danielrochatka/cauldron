from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_django_consumer_keeps_content_outside_cauldron_core():
    consumer_content = ROOT / "examples" / "django-consumer" / "site_content" / "welcome.md"
    assert consumer_content.exists()
    assert ROOT / "src" / "cauldron" not in consumer_content.parents


def test_astro_consumer_depends_on_package_without_copying_source():
    package_json = ROOT / "examples" / "astro-consumer" / "package.json"
    assert '"@procyonsoft/cauldron-astro": "file:../../packages/cauldron-astro"' in package_json.read_text()
    assert not (ROOT / "examples" / "astro-consumer" / "src" / "index.ts").exists()
