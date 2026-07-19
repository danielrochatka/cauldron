# cauldron.django.admin

The `cauldron.django.admin` module provides the Django administration interface for the Cauldron Django module stack. It builds on `cauldron.django.auth` to add the admin app, messages middleware, and message context processor.

**Requires:** `cauldron.django.auth` (which in turn requires `cauldron.django.state`)

## Installation

```bash
pip install cauldron-django-admin
```

## Settings

Enable the full stack in `CAULDRON_MODULES`:

```python
CAULDRON_MODULES = {
    "cauldron.django.state": {},
    "cauldron.django.auth": {},
    "cauldron.django.admin": {},
}
```

Then use `compose_django_settings()`:

```python
from cauldron.django import compose_django_settings

plan = compose_django_settings(
    installed_apps=["django.contrib.contenttypes", "cauldron"],
    middleware=[
        "django.middleware.security.SecurityMiddleware",
        "django.middleware.common.CommonMiddleware",
    ],
    context_processors=["django.template.context_processors.request"],
    module_settings=CAULDRON_MODULES,
)

INSTALLED_APPS = list(plan.installed_apps)
MIDDLEWARE = list(plan.middleware)
```

This module contributes:

- **Apps:** `django.contrib.messages`, `django.contrib.staticfiles`, `django.contrib.admin`, `cauldron_django_admin`
- **Middleware:** `MessageMiddleware`
- **Context processors:** `django.contrib.messages.context_processors.messages`

## URL mounting

Mount the admin URL in your project's `urls.py`:

```python
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
```

Or use the helper:

```python
from cauldron_django_admin.urls import get_admin_urls
from django.urls import path

urlpatterns = get_admin_urls()
```

## Creating a superuser

After running migrations:

```bash
python manage.py createsuperuser
```

Then log into the admin at `/admin/`.

## User and group admin

The `django.contrib.auth` admin is registered automatically, providing:

- `/admin/auth/user/` — User management (create, edit, change password, assign permissions)
- `/admin/auth/group/` — Group management

## Static files

`django.contrib.staticfiles` is included. Run `collectstatic` in production:

```bash
python manage.py collectstatic
```

## Provides capabilities

| Capability | Description |
|---|---|
| `admin.interface` | Admin UI available |
| `admin.users` | User management in admin |
| `admin.roles` | Group/role management in admin |
| `admin.permissions` | Permission management in admin |

## System check IDs

| ID | Level | Description |
|---|---|---|
| `cauldron.admin.I001` | Info | Admin configuration looks healthy |
| `cauldron.admin.E300` | Error | Required Django app missing from `INSTALLED_APPS` |
| `cauldron.admin.E301` | Error | Required middleware missing from `MIDDLEWARE` |
| `cauldron.admin.E302` | Error | Required context processor missing from `TEMPLATES` |
