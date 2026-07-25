# Cauldron

Cauldron is an AI-powered CMS — create, edit, and publish content with built-in AI assistance.

## Prerequisites

- Python 3.11 or newer ([python.org/downloads](https://www.python.org/downloads/))
- Git

## Setup

**1. Clone the repository**

```bash
git clone https://github.com/danielrochatka/cauldron.git
cd cauldron/cauldron-app
```

**2. Start Cauldron**

```bash
./start
```

On first launch, `./start` creates `config.env`, generates a secure secret key, sets up the environment, applies database migrations, and starts the server. No manual configuration is needed.

**3. Open your browser**

Visit [http://localhost:8000](http://localhost:8000)

**4. Create your admin account**

```bash
./manage createsuperuser
```

Then log in at [http://localhost:8000/accounts/login/](http://localhost:8000/accounts/login/)

---

## Updating

To update Cauldron to the latest version, run:

```bash
./update
```

This will stop the server, back up your database, pull the latest code, apply any new migrations, and restart.

## Stopping the server

```bash
./stop
```

## Configuration

All configuration lives in `config.env`. See `config.env.example` for available options including port, debug mode, and AI provider settings.

Your content lives in `content/`, schemas in `schemas/`, and CSS overrides in `overrides/`. These are yours — Cauldron updates will never touch them.
