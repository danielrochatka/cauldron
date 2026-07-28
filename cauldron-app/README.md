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

`./update` stops the server, backs up the database, pulls the latest code, upgrades all Python and frontend dependencies, applies migrations, rebuilds the public site, runs system checks, and restarts.

## Stopping the server

```bash
./stop
```

## Configuration

All configuration lives in `config.env`. See `config.env.example` for available options including port, debug mode, and AI provider settings.

Your content lives in `content/`, schemas in `schemas/`, and CSS overrides in `overrides/`. These are yours — Cauldron updates will never touch them.
