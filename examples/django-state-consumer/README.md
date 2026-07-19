# Cauldron Django State Consumer

A minimal example Django project that uses the Cauldron Django module stack:
`cauldron.django.state` → `cauldron.django.auth` → `cauldron.django.admin`.

## Installation

Install the Cauldron packages and their dependencies (from the repo root):

```bash
pip install -e ../../[dev]
pip install -e ../../packages/cauldron-django-state
pip install -e ../../packages/cauldron-django-auth
pip install -e ../../packages/cauldron-django-admin
```

## Run Checks

Verify the configuration is valid:

```bash
python manage.py check
```

## Migrate

Set up the database:

```bash
python manage.py migrate --noinput
```

## Create a Superuser

```bash
python manage.py createsuperuser
```

## Cauldron State Status

Check the database connection and migration state:

```bash
python manage.py cauldron_state_status
python manage.py cauldron_state_status --json
```

## Run the Development Server

```bash
python manage.py runserver
```

Then open http://localhost:8000/ in your browser.

- Admin: http://localhost:8000/admin/
- Login: http://localhost:8000/auth/login/

## Password Reset with Console Email Backend

The settings use `EMAIL_BACKEND = "django.core.mail.backends.console.ConsoleEmailBackend"`.

To test password reset:

1. Go to http://localhost:8000/auth/password-reset/
2. Enter your email address and submit.
3. The reset email will appear in the console/terminal where `runserver` is running.
4. Copy the reset link from the console output and open it in your browser.
5. Set a new password.

## Notes

- `db.sqlite3` is gitignored — never commit the database file.
- `SECRET_KEY` in settings.py is a placeholder for development only. Use a secure random key in production.
