#!/usr/bin/env python3
"""Wheel validation and manifest tool for the Cauldron distribution.

Modes
-----
  generate-manifest  Write wheelhouse/manifest.json with source SHA and per-wheel
                     sha256 digests.  Fails when the wheelhouse is absent or empty.

  verify             Validate wheel contents and optionally check the manifest
                     source SHA + per-wheel digests.  When --repo-root is given,
                     performs source-aware validation: requires exactly one wheel
                     per source project and cross-validates wheel contents against
                     the source tree (all Python files, entry points, migrations,
                     templates, static assets).  --repo-root always fails closed
                     on bad input.

Output format (verify mode)
---------------------------
  Each wheel produces one line on stdout:
      OK    <pkg-label>: <stats>
      FAIL  <pkg-label>: <error details>
  Discovery, manifest, and cross-package failures are printed to stderr.

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
except ImportError:  # Python < 3.11 (shouldn't be needed; project requires 3.12)
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class DiscoveryError(Exception):
    """Raised when source package discovery fails fatally."""


_SKIP_DIRS = frozenset({"__pycache__", "build", "dist"})


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


def _collect_all_py_files(src_root: Path) -> list[str]:
    """Collect all distributable .py files under src_root, relative to src_root.

    Skips __pycache__, build, dist, and *.egg-info directories.
    """
    files: list[str] = []
    for f in src_root.rglob("*.py"):
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(src_root)
        except ValueError:
            continue
        parts = rel.parts[:-1]  # directory components
        if any(p in _SKIP_DIRS or p.endswith(".egg-info") for p in parts):
            continue
        files.append(str(rel).replace("\\", "/"))
    return sorted(files)


def _collect_package_files(src_root: Path, subdir: str, *,
                           extensions: tuple[str, ...] | None = None) -> list[str]:
    """Return files in src_root/<pkg>/<subdir>/**/* relative to src_root.

    Skips __pycache__ directories and .pyc files.
    """
    files: list[str] = []
    try:
        top_dirs = [
            d for d in src_root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
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
    """Return {normalized_name: pkg_info} for all first-party source packages.

    Raises DiscoveryError on any fatal problem.  Per-package errors are
    collected and re-raised together so all problems surface in one pass.
    """
    if not repo_root.exists():
        raise DiscoveryError(f"repo root does not exist: {repo_root}")
    if not repo_root.is_dir():
        raise DiscoveryError(f"repo root is not a directory: {repo_root}")

    root_pp = repo_root / "pyproject.toml"
    if not root_pp.exists():
        raise DiscoveryError(f"missing root pyproject.toml in {repo_root}")

    pyprojects: list[tuple[Path, Path]] = [(root_pp, repo_root)]
    pkg_dir = repo_root / "packages"
    if pkg_dir.is_dir():
        for pp in sorted(pkg_dir.glob("*/pyproject.toml")):
            pyprojects.append((pp, pp.parent))

    result: dict[str, dict] = {}
    errors: list[str] = []

    for pp_path, pkg_root in pyprojects:
        rel_pp = pp_path.relative_to(repo_root)
        try:
            with open(pp_path, "rb") as fh:
                data = tomllib.load(fh)
        except Exception as exc:
            errors.append(f"cannot parse {rel_pp}: {exc}")
            continue

        project = data.get("project", {})
        if not isinstance(project, dict):
            errors.append(f"[project] table is not a mapping in {rel_pp}")
            continue

        name = project.get("name", "")
        if not name:
            errors.append(f"missing [project].name in {rel_pp}")
            continue
        if not isinstance(name, str):
            errors.append(f"[project].name is not a string in {rel_pp}")
            continue

        norm = _normalize_name(name)
        if norm in result:
            existing = result[norm]["pyproject_path"].relative_to(repo_root)
            raise DiscoveryError(
                f"duplicate normalized distribution name {norm!r}: "
                f"{existing} and {rel_pp}"
            )

        src_root = _find_src_root(data, pkg_root)
        if not src_root.is_dir():
            errors.append(f"source root {src_root} does not exist for {name!r}")
            continue

        ep_entries: dict[str, str] = (
            project.get("entry-points", {}).get("cauldron.modules", {})
        )

        py_files = _collect_all_py_files(src_root)
        migrations = _collect_package_files(src_root, "migrations", extensions=(".py",))
        migrations = [f for f in migrations if not f.endswith("__init__.py")]
        templates = _collect_package_files(src_root, "templates")
        static = _collect_package_files(src_root, "static")

        result[norm] = {
            "name": name,
            "normalized_name": norm,
            "pyproject_path": pp_path,
            "ep_entries": ep_entries,
            "src_root": src_root,
            "py_files": py_files,
            "migrations": migrations,
            "templates": templates,
            "static": static,
        }

    if errors:
        for err in errors:
            print(f"FAIL  discovery: {err}", file=sys.stderr)
        raise DiscoveryError(f"{len(errors)} package(s) failed discovery")

    if not result:
        raise DiscoveryError(f"no first-party packages discovered in {repo_root}")

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

            # --- All distributable Python source files ---
            for py_path in pkg_info["py_files"]:
                if py_path not in names:
                    errors.append(f"missing source file {py_path}")

            # --- Entry points (exact bidirectional check) ---
            ep_entries = pkg_info["ep_entries"]
            ep_files = [n for n in names if n.endswith("entry_points.txt")]
            wheel_ep_txt = z.read(ep_files[0]).decode() if ep_files else ""
            wheel_eps = _parse_ep_section(wheel_ep_txt, "cauldron.modules")

            if ep_entries:
                if not ep_files:
                    errors.append(
                        "missing entry_points.txt (expected cauldron.modules entries)"
                    )
                elif "[cauldron.modules]" not in wheel_ep_txt:
                    errors.append(
                        "missing [cauldron.modules] section in entry_points.txt"
                    )
                else:
                    # Source → wheel: every declared entry must be present and correct
                    for ep_key, ep_val in ep_entries.items():
                        if ep_key not in wheel_eps:
                            errors.append(f"missing entry point {ep_key!r}")
                        elif wheel_eps[ep_key] != ep_val:
                            errors.append(
                                f"entry point {ep_key!r}: "
                                f"expected {ep_val!r}, got {wheel_eps[ep_key]!r}"
                            )
                    # Wheel → source: no extra entries in wheel
                    for ep_key in wheel_eps:
                        if ep_key not in ep_entries:
                            errors.append(f"extra entry point {ep_key!r} in wheel")
            else:
                # Source declares no cauldron.modules eps — wheel must have none either
                for ep_key in wheel_eps:
                    errors.append(
                        f"extra entry point {ep_key!r} in wheel (not declared in source)"
                    )

            # --- Migrations (non-__init__ .py files under migrations/) ---
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

    if not wheelhouse.exists():
        print(
            f"FAIL  manifest: wheelhouse does not exist: {wheelhouse}",
            file=sys.stderr,
        )
        return 1
    if not wheelhouse.is_dir():
        print(
            f"FAIL  manifest: wheelhouse is not a directory: {wheelhouse}",
            file=sys.stderr,
        )
        return 1

    wheels: dict[str, dict] = {}
    for whl in sorted(wheelhouse.glob("*.whl")):
        wheels[whl.name] = {"sha256": _sha256(whl)}

    if not wheels:
        print(
            f"FAIL  manifest: no *.whl files found in {wheelhouse}",
            file=sys.stderr,
        )
        return 1

    manifest = {"source_sha": source_sha, "wheels": wheels}
    (wheelhouse / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"Generated manifest.json: {len(wheels)} wheels from {source_sha[:8]}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    wheelhouse = Path(args.wheelhouse)
    any_fail = False
    cross_fail = 0   # distribution-level failures (not per-wheel content)
    per_wheel_fail = 0

    # --- Source package discovery (when --repo-root given) -------------------
    source_pkgs: dict[str, dict] = {}
    if args.repo_root:
        try:
            source_pkgs = _discover_source_packages(Path(args.repo_root))
        except DiscoveryError as exc:
            print(f"FAIL  discovery: {exc}", file=sys.stderr)
            return 1

    # --- Manifest / SHA validation -------------------------------------------
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

        # Validate top-level types before consuming fields
        if not isinstance(manifest, dict):
            print("FAIL  manifest: manifest.json is not a JSON object", file=sys.stderr)
            return 1

        recorded_sha = manifest.get("source_sha", "")
        if not isinstance(recorded_sha, str):
            print(
                f"FAIL  manifest: source_sha is not a string "
                f"(got {type(recorded_sha).__name__})",
                file=sys.stderr,
            )
            return 1

        if recorded_sha != args.require_sha:
            print(
                f"FAIL  manifest: source_sha mismatch "
                f"(manifest={recorded_sha[:8]!r}, required={args.require_sha[:8]!r})",
                file=sys.stderr,
            )
            return 1

        recorded_wheels_raw = manifest.get("wheels", {})
        if not isinstance(recorded_wheels_raw, dict):
            print(
                f"FAIL  manifest: 'wheels' is not a JSON object "
                f"(got {type(recorded_wheels_raw).__name__})",
                file=sys.stderr,
            )
            return 1

        type_errors: list[str] = []
        for whl_name, whl_meta in recorded_wheels_raw.items():
            if not isinstance(whl_meta, dict):
                type_errors.append(
                    f"wheel entry for {whl_name!r} is not an object "
                    f"(got {type(whl_meta).__name__})"
                )
                continue
            sha256 = whl_meta.get("sha256", "")
            if not isinstance(sha256, str):
                type_errors.append(
                    f"sha256 for {whl_name!r} is not a string "
                    f"(got {type(sha256).__name__})"
                )
        if type_errors:
            for err in type_errors:
                print(f"FAIL  manifest: {err}", file=sys.stderr)
            return 1

        recorded_wheels = recorded_wheels_raw

        # Per-wheel sha256 digest + set equality
        digest_failures: list[str] = []
        disk_wheel_names = {w.name for w in wheelhouse.glob("*.whl")}
        manifest_wheel_names = set(recorded_wheels.keys())

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

        for whl_name in sorted(disk_wheel_names - manifest_wheel_names):
            digest_failures.append(
                f"FAIL  {_pkg_label(whl_name)}: wheel on disk but absent from manifest"
            )

        if digest_failures:
            for line in digest_failures:
                print(line, file=sys.stderr)
            return 1

    # --- Per-wheel list -------------------------------------------------------
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels:
        print(
            f"FAIL  (empty): no *.whl files found in {wheelhouse}",
            file=sys.stderr,
        )
        return 1

    # --- Cross-package checks (when --repo-root given) -----------------------
    if source_pkgs:
        wheel_by_label: dict[str, list[Path]] = {}
        for whl_path in wheels:
            label = _pkg_label(whl_path.name)
            wheel_by_label.setdefault(label, []).append(whl_path)

        # Duplicate distributions
        for label, wpaths in wheel_by_label.items():
            if len(wpaths) > 1:
                print(
                    f"FAIL  {label}: {len(wpaths)} duplicate wheels in wheelhouse",
                    file=sys.stderr,
                )
                cross_fail += 1
                any_fail = True

        # Wheels with no corresponding source project
        for whl_path in wheels:
            label = _pkg_label(whl_path.name)
            if label not in source_pkgs:
                print(
                    f"FAIL  {label}: wheel has no corresponding source package in repo",
                    file=sys.stderr,
                )
                cross_fail += 1
                any_fail = True

        # Every source project requires exactly one wheel
        wheel_labels = {_pkg_label(w.name) for w in wheels}
        for norm_name in source_pkgs:
            if norm_name not in wheel_labels:
                print(f"FAIL  {norm_name}: no wheel found", file=sys.stderr)
                cross_fail += 1
                any_fail = True

    # --- Per-wheel content validation ----------------------------------------
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
            per_wheel_fail += 1
            any_fail = True

    if any_fail:
        total = len(wheels)
        parts: list[str] = []
        if per_wheel_fail:
            parts.append(f"{per_wheel_fail} of {total} wheel(s) failed content check")
        if cross_fail:
            parts.append(f"{cross_fail} distribution-level issue(s)")
        print(f"\nFAIL: {'; '.join(parts)}.", file=sys.stderr)
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
        help="Validate wheel contents; optionally check manifest SHA, digests, "
             "and source-aware correctness",
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
        help="Repo root for source-aware validation.  Requires a root pyproject.toml. "
             "Cross-validates every wheel against its source entry points, Python "
             "files, migrations, templates, and static assets.  Fails closed on any "
             "discovery error.",
    )

    args = parser.parse_args(argv)

    if args.command == "generate-manifest":
        return cmd_generate_manifest(args)
    return cmd_verify(args)


if __name__ == "__main__":
    sys.exit(main())
