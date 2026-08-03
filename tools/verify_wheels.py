#!/usr/bin/env python3
"""Wheel validation and manifest tool for the Cauldron distribution.

Modes
-----
  generate-manifest  Write wheelhouse/manifest.json with source SHA and per-wheel
                     sha256 digests.  Called by _build_wheels() after building.

  verify             Validate wheel contents and optionally check the manifest
                     source SHA + per-wheel digests.  When --repo-root is given,
                     also cross-validates wheel contents against the source tree
                     (entry points, migrations, templates, static assets).

Output format (verify mode)
---------------------------
  Each wheel produces one line on stdout:
      OK    <pkg-label>: <stats>
      FAIL  <pkg-label>: <error details>
  Manifest errors and cross-package errors are printed to stderr before
  per-wheel results.

Exit codes
----------
  0  all checks passed
  1  at least one check failed
  2  usage error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11 fallback (shouldn't be needed with 3.12 requirement)
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pkg_label(whl_name: str) -> str:
    """cauldron_foo-1.0-py3-none-any.whl → cauldron_foo"""
    return re.sub(r"-[0-9].*", "", whl_name)


def _normalize_name(name: str) -> str:
    """cauldron-ai-admin → cauldron_ai_admin"""
    return re.sub(r"[-_.]+", "_", name).lower()


def _validate_wheel(whl_path: Path) -> tuple[bool, str]:
    """Return (ok, detail_string) for one wheel file."""
    errors: list[str] = []
    stats: dict[str, int] = {}

    try:
        with zipfile.ZipFile(whl_path) as z:
            names = z.namelist()

            if not any(re.search(r"\.dist-info/METADATA$", n) for n in names):
                errors.append("missing METADATA")

            py_files = [n for n in names if n.endswith(".py")]
            if not py_files:
                errors.append("no .py files")
            stats["py"] = len(py_files)

            # [cauldron.modules] entry point section must be non-empty when present
            ep_files = [n for n in names if n.endswith("entry_points.txt")]
            for ep_file in ep_files:
                ep = z.read(ep_file).decode()
                if "[cauldron.modules]" in ep:
                    in_sec = found = False
                    for line in ep.splitlines():
                        s = line.strip()
                        if s == "[cauldron.modules]":
                            in_sec = True
                        elif s.startswith("[") and in_sec:
                            in_sec = False
                        elif in_sec and "=" in s:
                            found = True
                    if not found:
                        errors.append("[cauldron.modules] entry point section is empty")

            stats["migrations"] = len(
                [n for n in names if "/migrations/" in n and n.endswith(".py")]
            )
            stats["templates"] = len([n for n in names if "/templates/" in n])
            stats["static"] = len([n for n in names if "/static/" in n])

    except zipfile.BadZipFile as exc:
        errors.append(f"bad zip: {exc}")

    if errors:
        return False, "; ".join(errors)
    return True, (
        f"{stats['py']} py, {stats['migrations']} migrations, "
        f"{stats['templates']} templates, {stats['static']} static"
    )


# ---------------------------------------------------------------------------
# Source-aware validation
# ---------------------------------------------------------------------------

def _find_src_root(pkg_data: dict, pkg_root: Path) -> Path:
    """Return the Python source root for a package."""
    tool = pkg_data.get("tool", {})
    where = (
        tool.get("setuptools", {})
        .get("packages", {})
        .get("find", {})
        .get("where", [])
    )
    if where:
        return pkg_root / where[0]
    src = pkg_root / "src"
    if src.is_dir():
        return src
    return pkg_root


def _collect_package_files(src_root: Path, subdir: str, *,
                           extensions: tuple[str, ...] | None = None) -> list[str]:
    """Return files in src_root/<pkg>/<subdir>/**/* relative to src_root.

    Skips __pycache__ directories and .pyc files.  When *extensions* is given
    only files with one of those suffixes are included.
    """
    files: list[str] = []
    try:
        top_dirs = [d for d in src_root.iterdir() if d.is_dir() and not d.name.startswith(".")]
    except OSError:
        return files

    for pkg_dir in top_dirs:
        target = pkg_dir / subdir
        if not target.is_dir():
            continue
        for f in target.rglob("*"):
            if not f.is_file():
                continue
            if "__pycache__" in f.parts:
                continue
            if f.suffix == ".pyc":
                continue
            if extensions and f.suffix not in extensions:
                continue
            rel = str(f.relative_to(src_root)).replace("\\", "/")
            files.append(rel)

    return sorted(files)


def _discover_source_packages(repo_root: Path) -> dict[str, dict]:
    """Return {normalized_name: pkg_info} for all first-party source packages."""
    result: dict[str, dict] = {}
    pyprojects: list[tuple[Path, Path]] = []

    root_pp = repo_root / "pyproject.toml"
    if root_pp.exists():
        pyprojects.append((root_pp, repo_root))

    pkg_dir = repo_root / "packages"
    if pkg_dir.is_dir():
        for pp in sorted(pkg_dir.glob("*/pyproject.toml")):
            pyprojects.append((pp, pp.parent))

    for pp_path, pkg_root in pyprojects:
        try:
            with open(pp_path, "rb") as fh:
                data = tomllib.load(fh)
        except Exception:
            continue

        project = data.get("project", {})
        name = project.get("name", "")
        if not name:
            continue

        norm = _normalize_name(name)
        src_root = _find_src_root(data, pkg_root)

        ep_entries: dict[str, str] = (
            project.get("entry-points", {}).get("cauldron.modules", {})
        )

        migrations = _collect_package_files(src_root, "migrations", extensions=(".py",))
        # Exclude __init__.py — not a migration
        migrations = [f for f in migrations if not f.endswith("__init__.py")]

        templates = _collect_package_files(src_root, "templates")
        static = _collect_package_files(src_root, "static")

        result[norm] = {
            "name": name,
            "normalized_name": norm,
            "ep_entries": ep_entries,
            "src_root": src_root,
            "migrations": migrations,
            "templates": templates,
            "static": static,
        }

    return result


def _parse_ep_section(ep_content: str, section: str) -> dict[str, str]:
    """Parse one section from an entry_points.txt INI file."""
    found: dict[str, str] = {}
    in_sec = False
    for line in ep_content.splitlines():
        s = line.strip()
        if s == f"[{section}]":
            in_sec = True
        elif s.startswith("[") and in_sec:
            in_sec = False
        elif in_sec and "=" in s:
            key, _, val = s.partition("=")
            found[key.strip()] = val.strip()
    return found


def _validate_wheel_source(whl_path: Path, pkg_info: dict) -> tuple[bool, list[str]]:
    """Cross-validate one wheel against its source package info."""
    errors: list[str] = []

    try:
        with zipfile.ZipFile(whl_path) as z:
            names = set(z.namelist())

            # --- Entry points ---
            ep_entries = pkg_info["ep_entries"]
            if ep_entries:
                ep_files = [n for n in names if n.endswith("entry_points.txt")]
                if not ep_files:
                    errors.append(
                        "missing entry_points.txt (expected cauldron.modules entries)"
                    )
                else:
                    ep_content = z.read(ep_files[0]).decode()
                    found_eps = _parse_ep_section(ep_content, "cauldron.modules")
                    for ep_key, ep_val in ep_entries.items():
                        if ep_key not in found_eps:
                            errors.append(f"missing entry point {ep_key!r}")
                        elif found_eps[ep_key] != ep_val:
                            errors.append(
                                f"entry point {ep_key!r}: "
                                f"expected {ep_val!r}, got {found_eps[ep_key]!r}"
                            )

            # --- Migrations ---
            for migration_path in pkg_info["migrations"]:
                if migration_path not in names:
                    errors.append(f"missing migration {migration_path}")

            # --- Templates ---
            for template_path in pkg_info["templates"]:
                if template_path not in names:
                    errors.append(f"missing template {template_path}")

            # --- Static assets ---
            for static_path in pkg_info["static"]:
                if static_path not in names:
                    errors.append(f"missing static {static_path}")

    except zipfile.BadZipFile:
        pass  # Already caught by _validate_wheel

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_generate_manifest(args: argparse.Namespace) -> int:
    wheelhouse = Path(args.wheelhouse)
    source_sha: str = args.source_sha

    wheels: dict[str, dict] = {}
    for whl in sorted(wheelhouse.glob("*.whl")):
        wheels[whl.name] = {"sha256": _sha256(whl)}

    manifest = {"source_sha": source_sha, "wheels": wheels}
    (wheelhouse / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"Generated manifest.json: {len(wheels)} wheels from {source_sha[:8]}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    wheelhouse = Path(args.wheelhouse)
    any_fail = False

    # --- Source package discovery (when --repo-root given) -------------------
    source_pkgs: dict[str, dict] = {}
    if args.repo_root:
        source_pkgs = _discover_source_packages(Path(args.repo_root))

    # --- Manifest / SHA validation (before per-wheel checks) -----------------
    recorded_wheels: dict[str, dict] = {}
    if args.require_sha:
        manifest_path = wheelhouse / "manifest.json"
        if not manifest_path.exists():
            print(
                f"FAIL  manifest: manifest.json not found in {wheelhouse}",
                file=sys.stderr,
            )
            return 1
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"FAIL  manifest: cannot read manifest.json: {exc}",
                file=sys.stderr,
            )
            return 1

        recorded_sha: str = manifest.get("source_sha", "")
        if recorded_sha != args.require_sha:
            print(
                f"FAIL  manifest: source_sha mismatch "
                f"(manifest={recorded_sha[:8]!r}, required={args.require_sha[:8]!r})",
                file=sys.stderr,
            )
            return 1

        recorded_wheels = manifest.get("wheels", {})

        # Verify per-wheel sha256 digests and set equality
        digest_failures: list[str] = []
        disk_wheel_names = {w.name for w in wheelhouse.glob("*.whl")}
        manifest_wheel_names = set(recorded_wheels.keys())

        # Wheels in manifest but missing on disk
        for whl_name, whl_meta in recorded_wheels.items():
            whl_path = wheelhouse / whl_name
            if not whl_path.exists():
                digest_failures.append(
                    f"FAIL  {whl_name}: in manifest but not found in wheelhouse"
                )
                continue
            actual = _sha256(whl_path)
            expected = whl_meta.get("sha256", "")
            if actual != expected:
                digest_failures.append(
                    f"FAIL  {_pkg_label(whl_name)}: "
                    f"sha256 mismatch (manifest={expected[:8]}, actual={actual[:8]})"
                )

        # Wheels on disk but absent from manifest
        for whl_name in sorted(disk_wheel_names - manifest_wheel_names):
            digest_failures.append(
                f"FAIL  {_pkg_label(whl_name)}: wheel on disk but absent from manifest"
            )

        if digest_failures:
            for line in digest_failures:
                print(line, file=sys.stderr)
            return 1

    # --- Per-wheel content validation ----------------------------------------
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels:
        print(
            f"FAIL  (empty): no *.whl files found in {wheelhouse}",
            file=sys.stderr,
        )
        return 1

    # --- Cross-package checks (when --repo-root given) -----------------------
    if source_pkgs:
        # Detect duplicate distributions
        wheel_by_label: dict[str, list[Path]] = {}
        for whl_path in wheels:
            label = _pkg_label(whl_path.name)
            wheel_by_label.setdefault(label, []).append(whl_path)

        for label, wpaths in wheel_by_label.items():
            if len(wpaths) > 1:
                print(
                    f"FAIL  {label}: {len(wpaths)} duplicate wheels in wheelhouse",
                    file=sys.stderr,
                )
                any_fail = True

        # Wheels with no corresponding source package
        for whl_path in wheels:
            label = _pkg_label(whl_path.name)
            if label not in source_pkgs:
                print(
                    f"FAIL  {label}: wheel has no corresponding source package in repo",
                    file=sys.stderr,
                )
                any_fail = True

        # Source packages with cauldron.modules entry point but no wheel
        wheel_labels = {_pkg_label(w.name) for w in wheels}
        for norm_name, pkg_info in source_pkgs.items():
            if pkg_info["ep_entries"] and norm_name not in wheel_labels:
                print(
                    f"FAIL  {norm_name}: has cauldron.modules entry point but no wheel found",
                    file=sys.stderr,
                )
                any_fail = True

    for whl_path in wheels:
        label = _pkg_label(whl_path.name)
        ok, detail = _validate_wheel(whl_path)

        src_errors: list[str] = []
        if source_pkgs and label in source_pkgs:
            src_ok, src_errors = _validate_wheel_source(whl_path, source_pkgs[label])
        else:
            src_ok = True

        if ok and src_ok:
            print(f"OK    {label}: {detail}")
        else:
            all_errors = ([detail] if not ok else []) + src_errors
            print(f"FAIL  {label}: {'; '.join(all_errors)}")
            any_fail = True

    if any_fail:
        total = len(wheels)
        failed = sum(
            1 for whl in wheels
            if not _validate_wheel(whl)[0]
            or (source_pkgs and _pkg_label(whl.name) in source_pkgs
                and not _validate_wheel_source(whl, source_pkgs[_pkg_label(whl.name)])[0])
        )
        print(
            f"\nFAIL: {failed} of {total} wheel(s) failed content check.",
            file=sys.stderr,
        )
        return 1

    total = len(wheels)
    print(f"\nAll {total} wheel{'s' if total != 1 else ''} passed content check.")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cauldron wheel validation and manifest tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # generate-manifest
    gen = sub.add_parser(
        "generate-manifest",
        help="Write wheelhouse/manifest.json with source SHA and sha256 digests",
    )
    gen.add_argument(
        "--wheelhouse", required=True, metavar="DIR",
        help="Directory containing .whl files",
    )
    gen.add_argument(
        "--source-sha", required=True, metavar="SHA",
        help="Git commit SHA the wheels were built from",
    )

    # verify
    ver = sub.add_parser(
        "verify",
        help="Validate wheel contents; optionally check manifest SHA and digests",
    )
    ver.add_argument(
        "--wheelhouse", required=True, metavar="DIR",
        help="Directory containing .whl files",
    )
    ver.add_argument(
        "--require-sha", default=None, metavar="SHA",
        help="Assert manifest.json source_sha equals SHA and verify per-wheel digests",
    )
    ver.add_argument(
        "--repo-root", default=None, metavar="DIR",
        help="Repo root for source-aware validation: cross-validates wheel contents "
             "against source entry points, migrations, templates, and static assets",
    )

    args = parser.parse_args(argv)

    if args.command == "generate-manifest":
        return cmd_generate_manifest(args)
    return cmd_verify(args)


if __name__ == "__main__":
    sys.exit(main())
