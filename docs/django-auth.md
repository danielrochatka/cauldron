# cauldron.django.auth

The `cauldron.django.auth` module provides the identity and authentication layer for the Cauldron Django module stack. It installs the Django auth, sessions, and contenttypes apps, configures the required middleware and context processors, and mounts the standard auth URL patterns.

**Requires:** `cauldron.django.state`

## Installation

```bash
pip install cauldron-django-auth
```

## Settings

Enable both the state and auth modules in `CAULDRON_MODULES`:

```python
CAULDRON_MODULES = {
    "cauldron.django.state": {},
    "cauldron.django.auth": {},
}
```

Then use `compose_django_settings()` to build `INSTALLED_APPS`, `MIDDLEWARE`, and context processors automatically:

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
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": list(plan.context_processors)},
}]
```

This module contributes:

- **Apps:** `django.contrib.contenttypes`, `django.contrib.auth`, `django.contrib.sessions`, `cauldron_django_auth`
- **Middleware:** `SessionMiddleware`, `AuthenticationMiddleware`
- **Context processors:** `django.contrib.auth.context_processors.auth`

## URL mounting

Mount the auth URLs in your project's `urls.py`:

```python
from django.urls import include, path

urlpatterns = [
    path("auth/", include("cauldron_django_auth.urls", namespace="cauldron_auth")),
]
```

This provides 8 URL patterns:

| Name | URL | View |
|---|---|---|
| `cauldron_auth:login` | `auth/login/` | Login form |
| `cauldron_auth:logout` | `auth/logout/` | Logout |
| `cauldron_auth:password_change` | `auth/password-change/` | Change password |
| `cauldron_auth:password_change_done` | `auth/password-change/done/` | Password changed confirmation |
| `cauldron_auth:password_reset` | `auth/password-reset/` | Request password reset |
| `cauldron_auth:password_reset_done` | `auth/password-reset/sent/` | Reset email sent confirmation |
| `cauldron_auth:password_reset_confirm` | `auth/password-reset/<uidb64>/<token>/` | Set new password |
| `cauldron_auth:password_reset_complete` | `auth/password-reset/complete/` | Reset complete |

## Password reset with console email

For development, use the console email backend in settings:

```python
EMAIL_BACKEND = "django.core.mail.backends.console.ConsoleEmailBackend"
```

Password reset emails will print to the terminal running `runserver`. Copy the link from the output.

## Sessions

Sessions use `django.contrib.sessions`. Configure the session engine in settings:

```python
SESSION_ENGINE = "django.contrib.sessions.backends.db"
```

## Groups and permissions

The standard `django.contrib.auth` groups and permissions system is available. Use `user.groups.add(group)` and `user.user_permissions.add(perm)` as usual.

## Provides capabilities

| Capability | Description |
|---|---|
| `identity.users` | User model available |
| `identity.roles` | Groups/roles available |
| `identity.permissions` | Permission system available |
| `identity.sessions` | Session management available |
| `identity.authentication` | Authentication backends available |
| `identity.password.reset` | Password reset flow available |

## System check IDs

| ID | Level | Description |
|---|---|---|
| `cauldron.auth.I001` | Info | Auth configuration looks healthy |
| `cauldron.auth.E200` | Error | `AUTH_USER_MODEL` is not in `app_label.ModelName` format |
| `cauldron.auth.E201` | Error | Required Django app missing from `INSTALLED_APPS` |
| `cauldron.auth.E202` | Error | Required middleware missing from `MIDDLEWARE` |
| `cauldron.auth.E203` | Error | Required context processor missing from `TEMPLATES` |
