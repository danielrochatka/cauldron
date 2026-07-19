# cauldron.django.state

The `cauldron.django.state` module provides the foundational database layer for the Cauldron Django module stack. It installs the `cauldron_django_state` Django app and exposes capabilities consumed by `cauldron.django.auth` and higher-level modules.

## Installation

```bash
pip install cauldron-django-state
```

## Settings

Enable the module in `CAULDRON_MODULES`:

```python
CAULDRON_MODULES = {
    "cauldron.django.state": {
        "database_alias": "default",  # optional; defaults to "default"
    },
}
```

## SQLite setup helper

```python
from cauldron_django_state.config import sqlite_database

DATABASES = {
    "default": sqlite_database("/path/to/db.sqlite3"),
}
```

`sqlite_database()` returns a new dict each call and does not mutate its input.

## Using compose_django_settings

The recommended way to configure your Django project with this module:

```python
from cauldron.django import compose_django_settings

CAULDRON_MODULES = {"cauldron.django.state": {}}

plan = compose_django_settings(
    installed_apps=["django.contrib.contenttypes", "cauldron"],
    module_settings=CAULDRON_MODULES,
)
INSTALLED_APPS = list(plan.installed_apps)
```

## Management command

### `cauldron_state_status`

Check the current database connection and migration state:

```bash
python manage.py cauldron_state_status          # human-readable
python manage.py cauldron_state_status --json   # machine-readable JSON
```

**Human-readable output:**

```
Cauldron State Status
=====================
Database alias:   default
Engine:           django.db.backends.sqlite3
Vendor:           sqlite3
Available:        yes
Name:             /path/to/db.sqlite3
Migrations:       all applied
```

**JSON output fields:**

| Field | Type | Description |
|---|---|---|
| `database_alias` | string | Configured alias |
| `engine` | string | Django database engine class |
| `vendor` | string | Database vendor string (from connection) |
| `available` | boolean | Whether test connection succeeded |
| `name` | string | Database NAME from settings |
| `migration_state` | dict or string | Unapplied migrations by app, or error message |

Exit code is `0` if `available` is true, `1` otherwise.

## Provides capabilities

| Capability | Description |
|---|---|
| `django.state` | Core state/storage layer |
| `django.database` | Database connection available |
| `django.transactions` | Transaction support |
| `django.migrations` | Migration system available |

## System check IDs

| ID | Level | Description |
|---|---|---|
| `cauldron.state.I001` | Info | Database configuration looks healthy |
| `cauldron.state.E100` | Error | `database_alias` is not a non-empty string |
| `cauldron.state.E101` | Error | `database_alias` not found in `DATABASES` |
| `cauldron.state.E102` | Error | Database connection failure (when tested) |
