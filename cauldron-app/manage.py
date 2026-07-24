#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path

# Ensure the cauldron-app directory is on the path so cauldron_site is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cauldron_site.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Make sure you have activated the virtualenv "
            "by running ./start, or source .venv/bin/activate first."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
