# Cauldron

Cauldron is an AI-powered CMS — create, edit, and publish content with built-in AI assistance.

## Prerequisites

- Python 3.11 or newer ([python.org/downloads](https://www.python.org/downloads/))
- Node.js 18 or newer with npm ([nodejs.org](https://nodejs.org/))
- Git

## Setup

**1. Clone the repository**

```bash
git clone https://github.com/danielrochatka/cauldron.git
cd cauldron/cauldron-app
```

**2. Install**

```bash
./install
```

`./install` provisions everything from scratch — Python virtualenv, all Python packages, `config.env` with a secure secret key, Node modules and the Astro frontend toolchain, database migrations, and an initial site build. It is idempotent: reruns are safe and preserve your existing configuration, content, and database.

What `./install` provisions:
- `.venv/` — Python virtualenv with all packages
- `config.env` — generated on first run; existing keys are never overwritten
- `frontend/node_modules/` — npm dependencies locked to `package-lock.json`
- `data/public/` — generated public site output (rebuilt on each publish)

**3. Create your admin account**

```bash
./manage createsuperuser
```

**4. Start the server**

```bash
./start
```

Then visit [http://localhost:8000](http://localhost:8000) and log in at [http://localhost:8000/accounts/login/](http://localhost:8000/accounts/login/)

---

## Lifecycle

| Command | What it does |
|---------|--------------|
| `./install` | Full idempotent provisioning — run after cloning or to repair a broken install |
| `./start` | Start the server; installs frontend dependencies if they are missing |
| `./stop` | Stop the server |
| `./update` | Pull latest code, upgrade dependencies, migrate, rebuild site, restart |

### Rebuilding the public site manually

```bash
./manage cauldron_site_build
```

The generated site is written to `data/public/`. Build logs are at `logs/site_build.log`.

### Resetting the site

`cauldron_site_reset` deletes all content, clears the active and staged CSS, and triggers a fresh rebuild.

```bash
# Reset everything (content + styles) — prompts for confirmation
./manage cauldron_site_reset

# Reset content only
./manage cauldron_site_reset --content

# Reset styles only
./manage cauldron_site_reset --styles

# Skip the confirmation prompt (for automation / CI)
./manage cauldron_site_reset --yes
```

The command prints a summary on success:

```
Website reset complete: N content item(s) removed, styles reset, public site rebuilt.
```

If the rebuild fails after the reset, the command exits with code 1 and the error is written to stderr.

### Rerunning the installer

`./install` is safe to rerun at any time. It skips steps that are already complete (e.g. an existing virtualenv or `node_modules/`) and repairs anything that is missing or broken. Your `config.env`, database, and content are never modified.

---

## Updating

To update Cauldron to the latest version:

```bash
./update
```

`./update` runs a **preflight verification** against the exact incoming commit before stopping the live server.  If verification fails the server keeps running and nothing is changed.

The update sequence:
1. Fetch the remote (updates the upstream tracking ref).
2. Resolve the upstream to an immutable commit SHA.
3. Confirm the candidate is a valid fast-forward from HEAD.
4. Run `verify-update <CANDIDATE_SHA>` — builds wheels, verifies their contents, migrates, and checks HTTP health in a throwaway environment.
5. If preflight passes: stop server → back up database → advance checkout to the exact verified SHA with `git merge --ff-only` → install → migrate → rebuild → restart.

A newer remote commit that arrives after step 2 is never installed — the checkout is pinned to the SHA that was verified.

### Emergency bypass

If the preflight check itself is preventing an update (e.g. a critical security fix where the environment is already broken), you can skip it:

```bash
./update --skip-preflight
```

This is a last-resort flag.  The server will stop before the update is verified, so a broken candidate can leave the server down.  Missing `verify-update` fails closed unless `--skip-preflight` is specified.

## Verification

`verify-update` checks that a given Git ref installs (from distribution wheels), migrates, and serves correctly, without touching the live checkout, database, virtualenv, or running server.  All work happens in a temporary Git worktree and throwaway virtualenv that are removed on exit.

```bash
./verify-update [--wheelhouse DIR] [REF]
./verify-update [--wheelhouse DIR] --from-ref OLD --to-ref NEW
```

`--wheelhouse DIR` installs from pre-built wheels instead of editable source, matching the production distribution.  Without `--wheelhouse`, wheels are built from source in the worktree before installation.

### Clean-install verification

```bash
./verify-update          # verify HEAD
./verify-update v1.2.3   # verify a tag or commit SHA
./verify-update --wheelhouse /tmp/wh v1.2.3  # verify pre-built wheels
```

### Upgrade-path verification

```bash
./verify-update --from-ref v1.1.0 --to-ref main
```

This simulates a real upgrade: installs and migrates the old version from source, creates representative persisted state, builds or uses a wheelhouse for the new version, upgrades packages in-place, re-migrates, verifies state is still readable, then runs a server health check.

### What is verified

Each run exercises these phases (failures are collected; a summary is always printed):

| Phase | What it checks |
|-------|---------------|
| `worktree` | Git ref resolves and worktree can be created |
| `venv` | Python virtualenv creates successfully |
| `wheel:build` | Wheels built from source (when no `--wheelhouse`) |
| `wheel:<pkg>` | Each wheel contains METADATA, Python source, valid entry points |
| `packages` | All packages install from wheels |
| `config` | `config.env` initialises without error |
| `check` | `manage.py check` passes |
| `makemigrations --check` | No unapplied schema changes |
| `migrate` | Migrations apply cleanly |
| `collectstatic` | Static assets collect without error |
| `cauldron_site_build` | Frontend build succeeds; SKIP when no `frontend/package.json` |
| `server startup` | Dev server starts on 127.0.0.1, process exit detected immediately |
| `health` | `/accounts/login/` and CSS tokens asset return 200 on 127.0.0.1 |

Frontend is fail-closed: when `frontend/package.json` exists, Node ≥18, npm, and Astro are required.

### Persistent failure diagnostics

Set `VERIFY_ARTIFACT_DIR` to copy logs and the phase report before the worktree is removed:

```bash
VERIFY_ARTIFACT_DIR=/tmp/verify-artifacts ./verify-update HEAD
```

### Package tests vs distribution verification

Package tests (run with `pytest` inside each `packages/*/tests/`) verify individual module logic in isolation.  `verify-update` verifies the full distribution: wheel artifacts, install sequence, migration chain, and HTTP surface.  Both are required before shipping.

### CI

`verify-update` runs in the `distribution-smoke` GitHub Actions workflow on every push and pull request to `main`.  The workflow also runs `arch-check`, unit tests, and package tests.  The `distribution-smoke / upgrade` job is the **branch-protection gate**.

## Stopping the server

```bash
./stop
```

## Configuration

All configuration lives in `config.env`. See `config.env.example` for available options including port, debug mode, and AI provider settings.

Your content lives in `content/`, schemas in `schemas/`, and CSS overrides in `overrides/`. These are yours — Cauldron updates will never touch them.
