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

### Rerunning the installer

`./install` is safe to rerun at any time. It skips steps that are already complete (e.g. an existing virtualenv or `node_modules/`) and repairs anything that is missing or broken. Your `config.env`, database, and content are never modified.

---

## Updating

To update Cauldron to the latest version:

```bash
./update
```

`./update` runs a **preflight verification** against the incoming code before stopping the live server.  If verification fails the server keeps running and nothing is changed.  If verification passes, the script stops the server, backs up the database, pulls the latest code, upgrades all Python and frontend dependencies, applies migrations, rebuilds the public site, runs system checks, and restarts.

### Emergency bypass

If the preflight check itself is preventing an update (e.g. a critical security fix where the environment is already broken), you can skip it:

```bash
./update --skip-preflight
```

This is a last-resort flag.  The server will stop before the update is verified, so a broken candidate can leave the server down.

## Verification

`verify-update` checks that a given Git ref installs, migrates, and serves correctly, without touching the live checkout, database, virtualenv, or running server.  All work happens in a temporary Git worktree and throwaway virtualenv that are removed on exit.

### Clean-install verification

```bash
./verify-update          # verify HEAD
./verify-update main     # verify a branch
./verify-update v1.2.3   # verify a tag
```

### Upgrade-path verification

```bash
./verify-update --from-ref v1.1.0 --to-ref main
```

This simulates a real upgrade: installs and migrates the old version, creates representative persisted state, upgrades packages in-place to the new version, re-migrates, and verifies the state is still readable before running a server health check.

### What is verified

Each run exercises these phases (failures are collected; a summary is always printed):

| Phase | What it checks |
|-------|---------------|
| `worktree` | Git ref resolves and worktree can be created |
| `venv` | Python virtualenv creates successfully |
| `packages` | All packages install without error |
| `config` | `config.env` initialises without error |
| `check` | `manage.py check` passes |
| `makemigrations --check` | No unapplied schema changes |
| `migrate` | Migrations apply cleanly |
| `collectstatic` | Static assets collect without error |
| `cauldron_site_build` | Frontend build succeeds (skipped if Node.js absent) |
| `server startup` | Dev server starts and responds within 30 s |
| `health` | `/accounts/login/` and CSS tokens asset return 200 |

### Package tests vs distribution verification

Package tests (run with `pytest` inside each `packages/*/tests/`) verify individual module logic in isolation.  `verify-update` verifies the full distribution: install sequence, migration chain, and HTTP surface.  Both are required before shipping.

### CI

`verify-update` runs in the `distribution-smoke` GitHub Actions workflow on every push and pull request to `main`.  The `distribution-smoke / upgrade` job is a **required branch-protection check** — PRs cannot be merged until upgrade-path verification passes.

## Stopping the server

```bash
./stop
```

## Configuration

All configuration lives in `config.env`. See `config.env.example` for available options including port, debug mode, and AI provider settings.

Your content lives in `content/`, schemas in `schemas/`, and CSS overrides in `overrides/`. These are yours — Cauldron updates will never touch them.
