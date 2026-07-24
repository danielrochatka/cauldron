# Cauldron

Cauldron is an AI-powered CMS — create, edit, and publish content with built-in AI assistance.

## Prerequisites

- Python 3.11 or newer ([python.org/downloads](https://www.python.org/downloads/))
- Git

## Setup

**1. Clone the repository**

```bash
git clone https://github.com/your-org/cauldron.git
cd cauldron/cauldron-app
```

**2. Configure your site**

```bash
cp config.env.example config.env
```

Open `config.env` and set a `SECRET_KEY`. Generate one with:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

**3. Start Cauldron**

```bash
./start
```

This will set up the environment, apply database migrations, and start the server.

**4. Open your browser**

Visit [http://localhost:8000](http://localhost:8000)

**5. Create your admin account**

```bash
source .venv/bin/activate
python manage.py createsuperuser
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
