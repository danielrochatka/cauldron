"""Management command: cauldron_state_status."""
from __future__ import annotations

import json
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Show the current Cauldron Django State status."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Output as JSON.",
        )

    def handle(self, *args, **options):
        from django.conf import settings
        from django.db import connections

        modules_setting = getattr(settings, "CAULDRON_MODULES", {})
        state_cfg = modules_setting.get("cauldron.django.state", {})
        if not isinstance(state_cfg, dict):
            state_cfg = {}

        database_alias = state_cfg.get("database_alias", "default")
        databases = getattr(settings, "DATABASES", {})
        db_entry = databases.get(database_alias, {})
        engine = db_entry.get("ENGINE", "unknown")
        db_name = db_entry.get("NAME", "")

        # Try to connect.
        vendor = "unknown"
        available = False
        try:
            conn = connections[database_alias]
            conn.ensure_connection()
            vendor = conn.vendor
            available = True
        except Exception:
            pass

        # Migration state.
        migration_state: dict | str
        try:
            from django.db.migrations.executor import MigrationExecutor

            executor = MigrationExecutor(connections[database_alias])
            targets = executor.loader.graph.leaf_nodes()
            plan = executor.migration_plan(targets)
            if plan:
                unapplied: dict[str, list[str]] = {}
                for migration, _ in plan:
                    unapplied.setdefault(migration.app_label, []).append(migration.name)
                migration_state = unapplied
            else:
                migration_state = {}
        except Exception as exc:
            migration_state = str(exc)

        status = {
            "database_alias": database_alias,
            "engine": engine,
            "vendor": vendor,
            "available": available,
            "name": str(db_name),
            "migration_state": migration_state,
        }

        if options["as_json"]:
            self.stdout.write(json.dumps(status, indent=2))
        else:
            self.stdout.write("Cauldron State Status")
            self.stdout.write("=====================")
            self.stdout.write(f"Database alias:   {database_alias}")
            self.stdout.write(f"Engine:           {engine}")
            self.stdout.write(f"Vendor:           {vendor}")
            self.stdout.write(f"Available:        {'yes' if available else 'no'}")
            self.stdout.write(f"Name:             {db_name}")
            if isinstance(migration_state, str):
                self.stdout.write(f"Migrations:       error: {migration_state}")
            elif migration_state:
                self.stdout.write("Migrations:       unapplied:")
                for app, names in sorted(migration_state.items()):
                    for name in names:
                        self.stdout.write(f"  {app}: {name}")
            else:
                self.stdout.write("Migrations:       all applied")

        if not available:
            sys.exit(1)
