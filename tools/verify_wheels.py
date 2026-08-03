#!/usr/bin/env python3
"""Wheel validation and manifest tool for the Cauldron distribution.

Modes
-----
  generate-manifest  Write wheelhouse/manifest.json with source SHA and per-wheel
                     sha256 digests.  Called by _build_wheels() after building.

  verify             Validate wheel contents and optionally check the manifest
                     source SHA + per-wheel digests.

Output format (verify mode)
---------------------------
  Each wheel produces one line on stdout:
      OK    <pkg-label>: <stats>
      FAIL  <pkg-label>: <error details>
  Manifest errors are printed to stderr before per-wheel results.

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pkg_label(whl_name: str) -> str:
    """cauldron_foo-1.0-py3-none-any.whl → cauldron_foo"""
    return re.sub(r"-[0-9].*", "", whl_name)


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

    # --- Manifest / SHA validation (before per-wheel checks) -----------------
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

        # Verify per-wheel sha256 digests
        recorded_wheels: dict[str, dict] = manifest.get("wheels", {})
        digest_failures: list[str] = []
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

    for whl_path in wheels:
        label = _pkg_label(whl_path.name)
        ok, detail = _validate_wheel(whl_path)
        if ok:
            print(f"OK    {label}: {detail}")
        else:
            print(f"FAIL  {label}: {detail}")
            any_fail = True

    if any_fail:
        total = len(wheels)
        failed = sum(
            1 for whl in wheels if not _validate_wheel(whl)[0]
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
        help="Repo root for source-aware validation (reserved for future use)",
    )

    args = parser.parse_args(argv)

    if args.command == "generate-manifest":
        return cmd_generate_manifest(args)
    return cmd_verify(args)


if __name__ == "__main__":
    sys.exit(main())
