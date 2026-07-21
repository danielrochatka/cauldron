"""Management command: cauldron_content_reconcile."""
import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Inspect and reconcile interrupted content change requests."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show actions without applying them.")
        parser.add_argument("--json", action="store_true", dest="output_json", help="Output as JSON.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        output_json = options["output_json"]

        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", {}) or {}

        # Build a minimal service for reconciliation
        try:
            from cauldron_content.registry import registry
            from cauldron_content.router import ContentRouter, RouterConfig
            from cauldron_content_operations.service import ContentOperationService
            from cauldron_content_operations.config import get_operations_config

            content_cfg = modules.get("cauldron.content") or {}
            routing = content_cfg.get("routing") or {}
            router_config = RouterConfig(
                default_provider=routing.get("default_provider", ""),
                collections=routing.get("collections", {}),
            )
            router = ContentRouter(registry, router_config)

            workspace = None
            snapshots = None
            ws_cfg_dict = modules.get("cauldron.workspace.flatfile") or {}
            wp = ws_cfg_dict.get("workspace_root", "")
            if wp:
                try:
                    from cauldron_workspace_flatfile.config import WorkspaceConfig
                    from cauldron_workspace_flatfile.store import ChangeSetStore
                    from cauldron_workspace_flatfile.snapshots import SnapshotService
                    workspace_config = WorkspaceConfig(workspace_root=wp)
                    workspace = ChangeSetStore(workspace_config)
                    snapshots = SnapshotService(workspace_config)
                except Exception:
                    pass

            service = ContentOperationService(
                router=router,
                workspace=workspace,
                snapshots=snapshots,
                config=get_operations_config(),
            )
        except Exception as exc:
            self.stderr.write(f"Failed to initialize service: {exc}")
            raise SystemExit(1)

        # Use a superuser-like sentinel for reconciliation
        class _ReconcileUser:
            pk = None
            is_active = True
            is_superuser = True
            def has_perm(self, perm):
                return True
            def get_username(self):
                return "reconciliation-command"

        try:
            results = service.reconcile(user=_ReconcileUser(), dry_run=dry_run)
        except Exception as exc:
            self.stderr.write(f"Reconciliation failed: {exc}")
            raise SystemExit(2)

        output = {
            "dry_run": dry_run,
            "results": results,
            "total": len(results),
        }

        if output_json:
            self.stdout.write(json.dumps(output, indent=2))
        else:
            prefix = "[DRY RUN] " if dry_run else ""
            self.stdout.write(f"{prefix}Content Reconciliation")
            self.stdout.write("=" * 40)
            if not results:
                self.stdout.write("  No transitional or reconciliation-required requests found.")
            else:
                for r in results:
                    self.stdout.write(f"  {r['request_id']}  state={r['current_state']}  action={r['action']}")
                    self.stdout.write(f"    Reason: {r['reason']}")
                    if not dry_run:
                        self.stdout.write(f"    Applied: {r['applied']}")
            self.stdout.write(f"\nTotal: {len(results)}")
