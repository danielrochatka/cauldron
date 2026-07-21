"""Management command: cauldron_content_operations_status."""
import json

from django.core.management.base import BaseCommand

from cauldron_content_operations.lifecycle import LifecycleState
from cauldron_content_operations.models import ContentChangeRequest


class Command(BaseCommand):
    help = "Display the status of the Cauldron content operations module."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="output_json", help="Output as JSON.")

    def handle(self, *args, **options):
        from django.conf import settings

        modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
        enabled = "cauldron.content.operations" in modules

        counts = {}
        for state in LifecycleState:
            counts[state.value] = ContentChangeRequest.objects.filter(lifecycle_state=state.value).count()

        # Provider info
        providers = []
        default_provider = ""
        try:
            from cauldron_content.registry import registry
            providers = list(registry.names())
        except Exception:
            pass

        try:
            content_modules = modules.get("cauldron.content") or {}
            routing = content_modules.get("routing") or {}
            default_provider = routing.get("default_provider", "")
        except Exception:
            pass

        workspace_available = False
        try:
            from cauldron_workspace_flatfile.config import WorkspaceConfig
            ops_cfg = modules.get("cauldron.content.operations") or {}
            ws_cfg = modules.get("cauldron.workspace.flatfile") or {}
            wp = ws_cfg.get("workspace_root", "") or ops_cfg.get("workspace_root", "")
            if wp:
                from pathlib import Path
                workspace_available = Path(wp).exists()
        except Exception:
            pass

        status = {
            "module": "cauldron.content.operations",
            "enabled": enabled,
            "registered_providers": providers,
            "default_provider": default_provider,
            "workspace_available": workspace_available,
            "change_requests": counts,
        }

        if options["output_json"]:
            self.stdout.write(json.dumps(status, indent=2))
        else:
            self.stdout.write("Cauldron Content Operations Status")
            self.stdout.write("=" * 40)
            self.stdout.write(f"  Module enabled:        {enabled}")
            self.stdout.write(f"  Registered providers:  {', '.join(providers) or '(none)'}")
            self.stdout.write(f"  Default provider:      {default_provider or '(none)'}")
            self.stdout.write(f"  Workspace available:   {workspace_available}")
            self.stdout.write("")
            self.stdout.write("Change Request Counts:")
            for state, count in counts.items():
                self.stdout.write(f"  {state:<30} {count}")
