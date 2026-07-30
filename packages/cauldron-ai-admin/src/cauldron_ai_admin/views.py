"""Django views for the Admin AI page."""
from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from .models import AdminAIRun
from .style_service import get_style_service


logger = logging.getLogger(__name__)


ADMIN_AI_PERMISSION = "cauldron_ai_admin.use_admin_ai"
MANAGE_AI_SETTINGS_PERMISSION = "cauldron_ai_admin.manage_admin_ai_settings"


def _get_service():
    from .service_factory import get_admin_ai_service
    return get_admin_ai_service()


def _get_provider_display() -> tuple[str, str, bool]:
    """Return ``(display_name, status, store_invalid)`` for the current provider.

    Resolution mirrors ``service_factory.get_admin_ai_service`` so the
    status header, provider-config form, and Django system checks agree
    on which provider is active:

    1. If the config store has a saved selection, use it.
    2. Otherwise fall back to ``CAULDRON_MODULES['cauldron.ai.admin']['provider']``
       via ``_resolve_provider`` (which surfaces E001/E002/E003).
    3. Never invoke ``factory.build()`` — the display path must stay pure
       so a misconfigured provider does not break the settings page.

    ``store_invalid`` is ``True`` when the config store cannot be read
    safely (any ``AIProviderStoreError`` subclass — malformed JSON,
    unsupported version, symlink, non-regular file, oversized file). In
    that case CAULDRON_MODULES is *not* used as a fallback for the
    status; the corrupt-store status wins so the operator is not misled
    into believing a provider is active while the store is unreadable.

    Always safe to call: exceptions become opaque status strings so the
    page still renders when the config store is unreadable.
    """
    # Import here so a missing/misconfigured cauldron_ai package doesn't
    # crash the outer view import chain.
    try:
        from .provider_config import AIProviderStoreError, get_store
    except Exception:
        return "Unknown", "Provider status unavailable", False

    # Probe the store first — a corrupt/unsafe store trumps every other
    # signal so we never render "Active" over a broken configuration.
    try:
        store = get_store()
        store.load()  # raises AIProviderStoreError on any structural fault
    except AIProviderStoreError:
        return "Unknown", "Configuration store invalid", True
    except Exception:
        # Anything else (unexpected OS error, etc.) — degrade to a safe
        # status without claiming the store is invalid.
        return "Unknown", "Provider status unavailable", False

    try:
        from cauldron_ai.providers import (
            descriptor_for,
            factory_names,
            provider_names,
        )

        from .checks import _admin_ai_config, _resolve_provider
        from .provider_config import resolve_provider_name

        cfg = _admin_ai_config()
        static_names = set(provider_names())
        factory_only = set(factory_names())
        all_names = sorted(static_names | factory_only)

        try:
            selected = resolve_provider_name(store)
        except AIProviderStoreError:
            # Store went bad between the load() above and now — treat as
            # invalid rather than falling back silently.
            return "Unknown", "Configuration store invalid", True
        except Exception:
            selected = ""

        if not selected:
            # No saved selection — reuse the E001/E002/E003 resolver so
            # the display agrees with system checks.
            provider, err_id = _resolve_provider(cfg, all_names)
            if err_id == "admin_ai.E001":
                return "None", "No AI provider registered", False
            if err_id == "admin_ai.E002":
                configured = cfg.get("provider", "") or "Unknown"
                return configured, "Provider not found — check AI settings", False
            if err_id == "admin_ai.E003":
                return (
                    "Ambiguous",
                    f"Multiple providers registered: {', '.join(all_names)}",
                    False,
                )
            if provider is None:
                return "Unknown", "Provider could not be resolved", False
            selected = getattr(provider, "name", "") or ""

        if not selected:
            return "None", "No AI provider registered", False

        if selected not in (static_names | factory_only):
            return selected, "Provider not found — check AI settings", False

        try:
            desc = descriptor_for(selected)
            display_name = desc.display_name or selected
        except Exception:
            display_name = selected

        return display_name, "Active", False
    except Exception:
        return "Unknown", "Provider status unavailable", False


def _get_available_providers() -> list[str]:
    """Return all provider names that can be selected (instances + factories)."""
    from cauldron_ai.providers import factory_names, provider_names
    return sorted(set(provider_names()) | set(factory_names()))


def _get_provider_spec(provider_name: str):
    """Return ``AIProviderConfigurationSpec`` for ``provider_name``, or None."""
    from cauldron_ai.providers import factory_names, get_configuration_spec
    if provider_name in factory_names():
        try:
            return get_configuration_spec(provider_name)
        except Exception:
            pass
    return None


def _credential_states_for(spec, provider_name: str, store) -> dict[str, str]:
    """Return a ``{field_name: state}`` mapping for password fields in *spec*."""
    from cauldron_ai.provider_configuration import FIELD_TYPE_PASSWORD
    from .forms import get_credential_state

    result: dict[str, str] = {}
    if spec is None:
        return result
    for f in spec.fields:
        if f.field_type != FIELD_TYPE_PASSWORD:
            continue
        result[f.name] = get_credential_state(
            provider_name, f.name, store, f.environment_variable,
        )
    return result


_SETTINGS_TEST_CACHE_KEY = "cauldron_ai_admin.settings.connection_test"
_SETTINGS_TEST_THROTTLE_SECONDS = 30


@method_decorator([
    login_required,
    permission_required(MANAGE_AI_SETTINGS_PERMISSION, raise_exception=True),
], name="dispatch")
class AdminAISettingsView(View):
    """Settings page for the Admin AI module.

    GET renders the provider selector, provider configuration form, runtime
    tuning form, and (when applicable) the result of the most recent
    connection test.  Password fields are never pre-populated.

    POST supports five actions and follows POST/Redirect/GET for every
    write.  Only ``action=test`` renders directly so the operator can see
    the outcome without a page reload:

    * ``select_provider`` — persist the chosen provider name, redirect.
    * ``save`` — validate + persist provider config/secrets, redirect.
    * ``save_runtime`` — validate + persist runtime settings, redirect.
    * ``clear_credential`` — delete one stored secret, redirect.
    * ``test`` — throttled connection test, renders in place.
    """

    template_name = "cauldron_ai_admin/settings.html"

    # ------------------------------------------------------------------
    # Context builder
    # ------------------------------------------------------------------

    def _context(
        self,
        request: HttpRequest,
        *,
        form=None,
        runtime_form=None,
        test_result=None,
        error_message: str = "",
    ) -> dict:
        from .provider_config import get_store, resolve_provider_name
        from .forms import (
            ProviderConfigForm,
            ProviderSelectForm,
            RuntimeSettingsForm,
        )

        from .provider_config import AIProviderStoreError

        provider_name_display, provider_status, store_invalid = (
            _get_provider_display()
        )

        # When the store is invalid we render a *degraded* page: the
        # module section and the status header still show, but every
        # control that would touch the store is suppressed. We must not
        # call ``get_store().<anything>`` on the corrupt path because
        # each accessor would re-raise ``AIProviderStoreError``.
        if store_invalid:
            return {
                "provider_name": provider_name_display,
                "provider_status": provider_status,
                "current_provider": "",
                "available_providers": _get_available_providers(),
                "spec": None,
                "form": None,
                "runtime_form": None,
                "select_form": None,
                "test_result": None,
                "error_message": error_message,
                "credential_states": {},
                "supports_connection_test": False,
                "store_invalid": True,
                "breadcrumbs": [
                    {"label": "AI Assistant", "url": reverse("cauldron_ai_admin:ai-page")},
                    {"label": "Settings", "url": ""},
                ],
            }

        store = get_store()
        available = _get_available_providers()
        try:
            current_provider = resolve_provider_name(store)
        except AIProviderStoreError:
            current_provider = ""
        spec = _get_provider_spec(current_provider) if current_provider else None

        credential_states = _credential_states_for(spec, current_provider, store)

        if form is None and spec is not None:
            try:
                current_config = store.get_config(current_provider)
            except AIProviderStoreError:
                current_config = {}
            form = ProviderConfigForm(
                spec=spec,
                current_config=current_config,
                credential_states=credential_states,
                clear_keys=True,
            )

        select_form = ProviderSelectForm(
            available_providers=available,
            initial={"provider": current_provider},
        )

        if runtime_form is None:
            from .checks import _admin_ai_config as _get_module_cfg
            from .service_factory import resolve_runtime_settings
            initial = resolve_runtime_settings(store, _get_module_cfg())
            runtime_form = RuntimeSettingsForm(initial=initial)

        return {
            "provider_name": provider_name_display,
            "provider_status": provider_status,
            "current_provider": current_provider,
            "available_providers": available,
            "spec": spec,
            "form": form,
            "runtime_form": runtime_form,
            "select_form": select_form,
            "test_result": test_result,
            "error_message": error_message,
            "credential_states": credential_states,
            "supports_connection_test": (
                spec.supports_connection_test if spec else False
            ),
            "store_invalid": False,
            "breadcrumbs": [
                {"label": "AI Assistant", "url": reverse("cauldron_ai_admin:ai-page")},
                {"label": "Settings", "url": ""},
            ],
        }

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self._context(request))

    def post(self, request: HttpRequest) -> HttpResponse:
        from .forms import (
            ProviderConfigForm,
            ProviderSelectForm,
            RuntimeSettingsForm,
        )
        from .provider_config import get_store, resolve_provider_name

        settings_url = reverse("cauldron_ai_admin:settings")

        # Guard every write path against a corrupt/unsafe store. Touching
        # the store in that state would re-raise ``AIProviderStoreError``
        # and (worse) could partially overwrite a file we can't fully
        # read.
        _, _, store_invalid = _get_provider_display()
        if store_invalid:
            messages.error(
                request,
                "AI settings cannot be saved: the configuration store is invalid.",
            )
            return redirect(settings_url)

        store = get_store()
        action = request.POST.get("action", "save")

        if action == "select_provider":
            return self._handle_select_provider(request, store, settings_url)

        if action == "save_runtime":
            return self._handle_save_runtime(request, store, settings_url)

        if action == "clear_credential":
            return self._handle_clear_credential(request, store, settings_url)

        current_provider = resolve_provider_name(store)
        spec = _get_provider_spec(current_provider) if current_provider else None

        if spec is None:
            return render(
                request, self.template_name,
                self._context(
                    request,
                    error_message="No configurable provider is selected.",
                ),
            )

        current_config = store.get_config(current_provider)
        credential_states = _credential_states_for(spec, current_provider, store)
        form = ProviderConfigForm(
            request.POST,
            spec=spec,
            current_config=current_config,
            credential_states=credential_states,
            clear_keys=True,
        )

        if action == "test":
            return self._handle_test(
                request, form, spec, store, current_provider,
            )

        # action == "save" (or fall-through)
        if not form.is_valid():
            return render(
                request, self.template_name,
                self._context(request, form=form),
            )

        config, secrets = form.split_config_and_secrets()
        clear_flags = form.clear_flags()

        store.set_config(current_provider, config)
        for key, value in secrets.items():
            if clear_flags.get(key):
                # Explicit clear beats an accidentally-submitted new value.
                continue
            store.set_secret(current_provider, key, value)
        for key, wants_clear in clear_flags.items():
            if wants_clear:
                store.clear_secret(current_provider, key)

        messages.success(request, "AI settings saved.")
        return redirect(settings_url)

    # ------------------------------------------------------------------
    # Action helpers
    # ------------------------------------------------------------------

    def _handle_select_provider(
        self, request: HttpRequest, store, settings_url: str,
    ) -> HttpResponse:
        from .forms import ProviderSelectForm
        available = _get_available_providers()
        select_form = ProviderSelectForm(
            request.POST, available_providers=available,
        )
        if select_form.is_valid():
            new_provider = select_form.cleaned_data["provider"]
            store.set_selected_provider(new_provider)
            messages.success(
                request, f"Provider changed to {new_provider!r}.",
            )
        else:
            messages.error(request, "Invalid provider selection.")
        return redirect(settings_url)

    def _handle_save_runtime(
        self, request: HttpRequest, store, settings_url: str,
    ) -> HttpResponse:
        from .forms import RuntimeSettingsForm
        runtime_form = RuntimeSettingsForm(request.POST)
        if not runtime_form.is_valid():
            return render(
                request, self.template_name,
                self._context(request, runtime_form=runtime_form),
            )
        cleaned = {
            "max_model_turns": runtime_form.cleaned_data["max_model_turns"],
            "max_tool_calls": runtime_form.cleaned_data["max_tool_calls"],
            "tool_timeout_seconds": runtime_form.cleaned_data["tool_timeout_seconds"],
            "run_timeout_seconds": runtime_form.cleaned_data["run_timeout_seconds"],
            "max_argument_bytes": runtime_form.cleaned_data["max_argument_bytes"],
            "max_result_bytes": runtime_form.cleaned_data["max_result_bytes"],
            "include_content_tools": bool(
                runtime_form.cleaned_data.get("include_content_tools")
            ),
        }
        store.set_runtime(cleaned)
        messages.success(request, "Runtime settings saved.")
        return redirect(settings_url)

    def _handle_clear_credential(
        self, request: HttpRequest, store, settings_url: str,
    ) -> HttpResponse:
        from .provider_config import resolve_provider_name
        provider_name = resolve_provider_name(store)
        field = request.POST.get("field", "").strip()
        spec = _get_provider_spec(provider_name) if provider_name else None
        # Validate the field belongs to the current provider's password fields
        # so a spoofed action can't nuke arbitrary keys.
        from cauldron_ai.provider_configuration import FIELD_TYPE_PASSWORD
        allowed = set()
        if spec is not None:
            allowed = {
                f.name for f in spec.fields
                if f.field_type == FIELD_TYPE_PASSWORD
            }
        if not provider_name or field not in allowed:
            messages.error(request, "Cannot clear that credential.")
            return redirect(settings_url)
        store.clear_secret(provider_name, field)
        messages.success(request, f"Cleared saved credential {field!r}.")
        return redirect(settings_url)

    def _handle_test(
        self,
        request: HttpRequest,
        form,
        spec,
        store,
        current_provider: str,
    ) -> HttpResponse:
        from django.core.cache import cache
        from cauldron_ai.providers import run_provider_connection_test

        if not spec.supports_connection_test:
            return render(
                request, self.template_name,
                self._context(
                    request, form=form,
                    error_message=(
                        "This provider does not support connection testing."
                    ),
                ),
            )

        # Throttle: one test per user per 30 s.
        cache_key = f"{_SETTINGS_TEST_CACHE_KEY}.{request.user.pk}"
        if cache.get(cache_key):
            return render(
                request, self.template_name,
                self._context(
                    request, form=form,
                    error_message=(
                        "Connection test throttled. "
                        f"Please wait {_SETTINGS_TEST_THROTTLE_SECONDS} seconds."
                    ),
                ),
            )
        cache.set(cache_key, True, _SETTINGS_TEST_THROTTLE_SECONDS)

        # Use submitted form values for the test; fall back to stored secrets
        # for password fields not re-submitted (render_value=False).
        if form.is_valid():
            config, submitted_secrets = form.split_config_and_secrets()
        else:
            config = store.get_config(current_provider)
            submitted_secrets = {}

        # Merge: submitted secrets take precedence, then stored secrets.
        stored_secrets = store.get_secrets(current_provider)
        merged_secrets = {**stored_secrets, **submitted_secrets}

        try:
            result = run_provider_connection_test(
                current_provider, config, merged_secrets,
            )
        except Exception:
            from cauldron_ai.provider_configuration import AIProviderConnectionResult
            # Never embed raw exception messages — vendor SDKs echo request
            # metadata (and occasionally headers) in exception strings.
            logger.exception(
                "Admin AI connection test raised an unexpected exception"
            )
            result = AIProviderConnectionResult(
                success=False,
                status="error",
                message=(
                    "Connection test failed with an unexpected error. "
                    "See server logs for details."
                ),
            )

        return render(
            request, self.template_name,
            self._context(request, form=form, test_result=result),
        )


class AdminAIPageView(View):
    """Render the Admin AI console and accept POSTed requests.

    GET returns an HTML page showing:
      * a text area for the natural-language request;
      * a hint listing the tools the current user can invoke;
      * the caller's most recent runs.

    POST is JSON-in / JSON-out. CSRF is required (Django enforces this
    against the default middleware). The view calls
    ``AdminAIService.run()`` and returns a summary of the resulting run.

    Auth and permission are enforced in dispatch() so that POST requests
    from the browser-side AI console receive structured JSON errors (401/403)
    rather than HTML login-redirect pages that would cause a client-side
    JSON parse failure.
    """

    template_name = "cauldron_ai_admin/ai_page.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            if request.method == "POST":
                return JsonResponse(
                    {
                        "ok": False,
                        "error": {
                            "code": "auth_required",
                            "message": "Authentication required. Please refresh the page and log in.",
                        },
                    },
                    status=401,
                )
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        if not request.user.has_perm(ADMIN_AI_PERMISSION):
            if request.method == "POST":
                return JsonResponse(
                    {
                        "ok": False,
                        "error": {
                            "code": "permission_denied",
                            "message": "You do not have permission to use Admin AI.",
                        },
                    },
                    status=403,
                )
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request: HttpRequest) -> HttpResponse:
        from .tools import get_tool_registry
        allowed_tools = get_tool_registry().list_for_actor(request.user)
        recent = list(
            AdminAIRun.objects.filter(actor=request.user).order_by("-created_at")[:10]
        )
        return render(request, self.template_name, {
            "allowed_tools": [
                {
                    "name": t.name,
                    "risk_level": t.risk_level.value,
                    "description": t.description,
                }
                for t in allowed_tools
            ],
            "recent_runs": [
                {
                    "run_id": str(r.run_id),
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                    "user_request": r.user_request[:200],
                }
                for r in recent
            ],
        })

    def post(self, request: HttpRequest) -> HttpResponse:
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return JsonResponse(
                {"ok": False, "error": {"code": "bad_request", "message": str(exc)}},
                status=400,
            )
        request_text = payload.get("request", "")
        correlation_id = payload.get("correlation_id", "")
        if not isinstance(request_text, str) or not request_text.strip():
            return JsonResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "bad_request",
                        "message": "Field 'request' must be a non-empty string.",
                    },
                },
                status=400,
            )
        try:
            service = _get_service()
        except Exception:
            logger.exception("Admin AI service is not configured")
            return JsonResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "service_unavailable",
                        "message": "Admin AI is not available. Contact your administrator.",
                    },
                },
                status=503,
            )
        try:
            run = service.run(request.user, request_text, correlation_id=correlation_id)
        except (PermissionDenied, PermissionError) as exc:
            return JsonResponse(
                {
                    "ok": False,
                    "error": {"code": "permission_denied", "message": str(exc)},
                },
                status=403,
            )
        except ValueError as exc:
            return JsonResponse(
                {"ok": False, "error": {"code": "bad_request", "message": str(exc)}},
                status=400,
            )
        except Exception:
            logger.exception("Admin AI run raised an unexpected exception")
            return JsonResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "server_error",
                        "message": "Admin AI run failed. See server logs.",
                    },
                },
                status=500,
            )
        return JsonResponse({"ok": True, **_serialize_run(run)})


@method_decorator([
    login_required,
    permission_required("cauldron_ai_admin.view_admin_ai_runs", raise_exception=True),
], name="dispatch")
class AdminAIRunListView(View):
    template_name = "cauldron_ai_admin/run_list.html"

    def get(self, request):
        # view_admin_ai_runs is an admin-visibility permission: any user who
        # holds it can see the full run history, not just their own runs.
        runs = AdminAIRun.objects.all().order_by("-created_at")[:100]
        return render(request, self.template_name, {
            "runs": runs,
            "breadcrumbs": [
                {"label": "Admin AI", "url": reverse("cauldron_ai_admin:ai-page")},
                {"label": "Runs", "url": ""},
            ],
        })


@method_decorator([
    login_required,
    permission_required("cauldron_ai_admin.view_admin_ai_runs", raise_exception=True),
], name="dispatch")
class AdminAIRunDetailView(View):
    template_name = "cauldron_ai_admin/run_detail.html"

    def get(self, request, run_id):
        from django.shortcuts import get_object_or_404
        run = get_object_or_404(AdminAIRun, run_id=run_id)
        show_invocations = request.user.has_perm(
            "cauldron_ai_admin.view_admin_ai_audit"
        )
        invocations = (
            list(run.invocations.order_by("created_at"))
            if show_invocations else []
        )
        return render(request, self.template_name, {
            "run": run,
            "invocations": invocations,
            "show_invocations": show_invocations,
            "can_view_audit": show_invocations,
            "breadcrumbs": [
                {"label": "Admin AI", "url": reverse("cauldron_ai_admin:ai-page")},
                {"label": "Runs", "url": reverse("cauldron_ai_admin:run-list")},
                {"label": str(run.run_id)[:8] + "…", "url": ""},
            ],
        })


@method_decorator([
    login_required,
    permission_required("cauldron_ai_admin.view_ui_styles", raise_exception=True),
], name="dispatch")
class UIStyleChangeListView(View):
    template_name = "cauldron_ai_admin/style_list.html"

    def get(self, request):
        from .models import UIStyleChangeRequest
        proposals = UIStyleChangeRequest.objects.all().order_by("-created_at")[:50]
        return render(request, self.template_name, {
            "proposals": proposals,
            "breadcrumbs": [{"label": "Style Proposals", "url": ""}],
        })


@method_decorator([
    login_required,
    permission_required("cauldron_ai_admin.view_ui_styles", raise_exception=True),
], name="dispatch")
class UIStyleChangeDetailView(View):
    template_name = "cauldron_ai_admin/style_detail.html"

    # Maximum bytes of unified-diff text we render into the page.
    _DIFF_LIMIT_BYTES = 32_000

    def get(self, request, request_id):
        import difflib

        from django.shortcuts import get_object_or_404
        from .models import UIStyleChangeRequest

        proposal = get_object_or_404(UIStyleChangeRequest, request_id=request_id)

        can_view_audit = request.user.has_perm(
            "cauldron_ai_admin.view_ui_style_audit"
        )
        audit_events = (
            list(proposal.audit_events.order_by("sequence"))
            if can_view_audit else []
        )

        # Best-effort read of the current on-disk content so we can render
        # a real diff view. Failures fall back to a "new file" summary.
        current_content: str | None = None
        current_label = "new file"
        if proposal.base_exists:
            try:
                from cauldron_django_admin.override_views import _get_override_root
                from cauldron_django_admin.override_store import UIOverrideStore
                root = _get_override_root()
                if root is not None and root.is_dir():
                    store = UIOverrideStore(root)
                    current_content = store.read_file(
                        proposal.scope, proposal.target_path,
                    )
                    current_label = f"{proposal.scope}/{proposal.target_path}"
            except Exception:
                current_content = None

        # Unified diff — bounded so a large proposal cannot blow up the
        # response body.
        diff_lines: list[str] = []
        if current_content is not None:
            diff_lines = list(difflib.unified_diff(
                current_content.splitlines(keepends=True),
                proposal.proposed_content.splitlines(keepends=True),
                fromfile=f"current: {proposal.scope}/{proposal.target_path}",
                tofile=f"proposed: {proposal.scope}/{proposal.target_path}",
                lineterm="",
            ))
        elif not proposal.base_exists:
            diff_lines = list(difflib.unified_diff(
                [],
                proposal.proposed_content.splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=f"new: {proposal.scope}/{proposal.target_path}",
                lineterm="",
            ))
        unified_diff = "".join(diff_lines)[: self._DIFF_LIMIT_BYTES]

        return render(request, self.template_name, {
            "proposal": proposal,
            "audit_events": audit_events,
            "can_approve": request.user.has_perm(
                "cauldron_ai_admin.approve_ui_style_changes"
            ),
            "can_view_audit": can_view_audit,
            "current_content": current_content,
            "current_label": current_label,
            "unified_diff": unified_diff,
            "breadcrumbs": [
                {"label": "Style Proposals", "url": reverse("cauldron_ai_admin:style-list")},
                {"label": str(proposal.request_id)[:8] + "…", "url": ""},
            ],
        })

    def post(self, request, request_id):
        """Handle approve/reject/apply actions."""
        from django.shortcuts import get_object_or_404, redirect
        from .models import UIStyleChangeRequest
        from cauldron_django_admin.override_store import HashConflictError, OverrideStoreError
        if not request.user.has_perm("cauldron_ai_admin.approve_ui_style_changes"):
            raise PermissionDenied
        proposal = get_object_or_404(UIStyleChangeRequest, request_id=request_id)
        action = request.POST.get("action", "")
        service = get_style_service()
        try:
            if action == "approve" and proposal.status == "proposed":
                service.approve(proposal, reviewed_by=request.user)
                messages.success(request, "Proposal approved.")
            elif action == "reject" and proposal.status == "proposed":
                service.reject(proposal, reviewed_by=request.user)
                messages.success(request, "Proposal rejected.")
            elif action == "apply" and proposal.status == "approved":
                try:
                    service.apply(proposal, applied_by=request.user)
                    messages.success(request, "Style change applied successfully.")
                except HashConflictError:
                    messages.error(
                        request,
                        "Conflict: the target file was modified. Proposal marked as conflicted.",
                    )
                except OverrideStoreError:
                    messages.error(request, "Failed to apply style change. See audit for details.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("cauldron_ai_admin:style-detail", request_id=request_id)


@method_decorator([
    login_required,
    permission_required("cauldron_ai_admin.view_admin_ai_audit", raise_exception=True),
], name="dispatch")
class AdminAIInvocationDetailView(View):
    template_name = "cauldron_ai_admin/invocation_detail.html"

    def get(self, request, run_id, invocation_id):
        from django.shortcuts import get_object_or_404
        from .models import AdminAIToolInvocation
        inv = get_object_or_404(
            AdminAIToolInvocation,
            invocation_id=invocation_id,
            run__run_id=run_id,
        )
        return render(request, self.template_name, {
            "invocation": inv,
            "run": inv.run,
            "breadcrumbs": [
                {"label": "Admin AI", "url": reverse("cauldron_ai_admin:ai-page")},
                {"label": "Runs", "url": reverse("cauldron_ai_admin:run-list")},
                {"label": str(run_id)[:8] + "…", "url": reverse("cauldron_ai_admin:run-detail", kwargs={"run_id": run_id})},
                {"label": str(invocation_id)[:8] + "…", "url": ""},
            ],
        })


def _parse_json_body(request: HttpRequest) -> dict[str, Any]:
    ct = (request.META.get("CONTENT_TYPE") or "").split(";", 1)[0].strip().lower()
    if ct != "application/json":
        raise ValueError("Content-Type must be application/json")
    try:
        return json.loads(request.body.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc


def _serialize_run(run: AdminAIRun) -> dict[str, Any]:
    invocations = list(run.invocations.order_by("created_at"))
    return {
        "run_id": str(run.run_id),
        "status": run.status,
        "final_response": run.final_response,
        "error_code": run.error_code,
        "error_summary": run.error_summary,
        "tool_call_count": run.tool_call_count,
        "tool_invocations": [
            {
                "invocation_id": str(inv.invocation_id),
                "tool_name": inv.tool_name,
                "risk_level": inv.risk_level,
                "status": inv.status,
                "error_code": inv.error_code,
                "duration_ms": inv.duration_ms,
            }
            for inv in invocations
        ],
    }
