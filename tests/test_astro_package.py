import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "cauldron-astro"


def test_astro_package_exports_committed_dist_entrypoint():
    package_json = json.loads((PACKAGE / "package.json").read_text())
    exported = package_json["exports"]["."]
    assert (PACKAGE / exported["default"]).exists()
    assert (PACKAGE / exported["types"]).exists()


def test_astro_package_files_include_dist_entrypoint():
    package_json = json.loads((PACKAGE / "package.json").read_text())
    assert "dist" in package_json["files"]
    assert (PACKAGE / "dist" / "index.js").exists()
    assert (PACKAGE / "dist" / "index.d.ts").exists()
