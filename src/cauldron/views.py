"""Small HTTP views exposed by the Cauldron foundation."""

from django.http import JsonResponse
from django.views import View

from . import __version__


def health(request):
    """Return minimal runtime health information for integration tests."""

    return JsonResponse({"status": "ok", "package": "cauldron", "version": __version__})


class _ModuleInventoryView(View):
    def get(self, request):
        from .modules.registry import registry

        states = registry.module_states()
        record_by_slug = {r.slug: r for r in registry._discovery_records}

        lc_errors_by_slug: dict[str, list[dict]] = {}
        for err in registry.lifecycle_errors():
            lc_errors_by_slug.setdefault(err.module_slug, []).append({
                "phase": err.phase,
                "exception_type": type(err.exception).__name__,
            })

        discovery_errors_by_slug: dict[str, list[dict]] = {}
        for err in registry.discovery_errors():
            if err.candidate_slug:
                discovery_errors_by_slug.setdefault(err.candidate_slug, []).append({
                    "phase": "discovery",
                    "message": err.message,
                })

        enabled = registry.enabled_slugs()
        modules_out = []

        for m in registry.all_discovered():
            slug = m.slug
            rec = record_by_slug.get(slug)
            if rec and rec.source_type == "project":
                source = rec.project_path
            elif rec:
                source = rec.package_name
            else:
                source = ""
            errors = (
                discovery_errors_by_slug.get(slug, [])
                + lc_errors_by_slug.get(slug, [])
            )
            modules_out.append({
                "slug": slug,
                "label": m.label,
                "version": m.manifest.version,
                "source_type": rec.source_type if rec else None,
                "source": source,
                "enabled": slug in enabled,
                "state": states.get(slug, "discovered"),
                "requires": [r.slug for r in m.manifest.requires],
                "provides": sorted(m.manifest.provides),
                "errors": errors,
            })

        for u in registry.unavailable_modules():
            slug = u.slug
            if slug in {m["slug"] for m in modules_out}:
                continue
            errors = []
            if u.discovery_error_message:
                errors.append({"phase": "discovery", "message": u.discovery_error_message})
            errors.extend(lc_errors_by_slug.get(slug, []))
            modules_out.append({
                "slug": slug,
                "label": slug,
                "version": "",
                "source_type": None,
                "source": "",
                "enabled": True,
                "state": "unavailable",
                "requires": [],
                "provides": [],
                "errors": errors,
            })

        modules_out.sort(key=lambda x: x["slug"])
        return JsonResponse({"modules": modules_out})


module_inventory = _ModuleInventoryView.as_view()
