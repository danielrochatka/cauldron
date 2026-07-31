"""Forms for the Admin AI settings page."""
from __future__ import annotations

import os
from typing import Any

from django import forms

from cauldron_ai.provider_configuration import (
    FIELD_TYPE_BOOLEAN,
    FIELD_TYPE_INTEGER,
    FIELD_TYPE_PASSWORD,
    FIELD_TYPE_TEXT,
    FIELD_TYPE_URL,
    AIProviderConfigurationField,
    AIProviderConfigurationSpec,
)


# ---------------------------------------------------------------------------
# Credential state
# ---------------------------------------------------------------------------

CREDENTIAL_STATE_MANAGED = "managed_storage"
CREDENTIAL_STATE_ENVIRONMENT = "environment"
CREDENTIAL_STATE_NOT_CONFIGURED = "not_configured"

_CREDENTIAL_STATE_LABELS = {
    CREDENTIAL_STATE_MANAGED: "Configured in managed storage",
    CREDENTIAL_STATE_ENVIRONMENT: "Configured by environment variable",
    CREDENTIAL_STATE_NOT_CONFIGURED: "Not configured",
}


def credential_state_label(state: str) -> str:
    """Return a human-readable label for a credential-state value."""
    return _CREDENTIAL_STATE_LABELS.get(state, "Unknown")


def get_credential_state(
    provider_name: str,
    field_name: str,
    store: Any,
    env_var: str | None,
) -> str:
    """Return the current storage location of a credential.

    Precedence mirrors runtime: managed-storage first, then environment,
    then not-configured.  Never touches the value itself — only whether
    one exists.
    """
    try:
        if store is not None and store.get_secret(provider_name, field_name):
            return CREDENTIAL_STATE_MANAGED
    except Exception:
        # Store read failures are opaque to the form; treat as unknown so
        # the operator can still submit fresh credentials.
        pass
    if env_var and os.environ.get(env_var, "").strip():
        return CREDENTIAL_STATE_ENVIRONMENT
    return CREDENTIAL_STATE_NOT_CONFIGURED


# ---------------------------------------------------------------------------
# Provider selection form
# ---------------------------------------------------------------------------

class ProviderSelectForm(forms.Form):
    """Select which AI provider to use."""

    provider = forms.ChoiceField(
        label="AI Provider",
        choices=[],
        required=True,
    )

    def __init__(
        self,
        *args: Any,
        available_providers: list[str],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        choices = [(name, name) for name in sorted(available_providers)]
        self.fields["provider"].choices = choices


# ---------------------------------------------------------------------------
# Dynamic provider configuration form
# ---------------------------------------------------------------------------

def _django_field_for(spec_field: AIProviderConfigurationField) -> forms.Field:
    """Convert an ``AIProviderConfigurationField`` to a Django form field."""
    common: dict[str, Any] = {
        "label": spec_field.label,
        "help_text": spec_field.help_text or "",
        "initial": spec_field.default,
    }

    if spec_field.field_type == FIELD_TYPE_PASSWORD:
        # Password fields are ALWAYS required=False on the wire: an empty
        # submission means "leave the existing secret intact".  Original
        # ``required`` gets surfaced via the field's help text so the UI
        # can still tell the operator it is mandatory in practice.
        widget = forms.PasswordInput(render_value=False)
        return forms.CharField(
            label=spec_field.label,
            required=False,
            help_text=spec_field.help_text or "",
            max_length=spec_field.max_length or 1024,
            widget=widget,
        )

    if spec_field.field_type == FIELD_TYPE_URL:
        f = forms.URLField(
            **common,
            required=spec_field.required,
        )
        if spec_field.max_length:
            f.max_length = spec_field.max_length
        return f

    if spec_field.field_type == FIELD_TYPE_INTEGER:
        return forms.IntegerField(
            **common,
            required=spec_field.required,
        )

    if spec_field.field_type == FIELD_TYPE_BOOLEAN:
        # BooleanField.required=True means "must be checked"; for config
        # booleans we always want required=False (unchecked == False).
        return forms.BooleanField(
            label=spec_field.label,
            help_text=spec_field.help_text or "",
            required=False,
            initial=bool(spec_field.default),
        )

    # Default: text
    return forms.CharField(
        **common,
        required=spec_field.required,
        max_length=spec_field.max_length or 1024,
    )


class ProviderConfigForm(forms.Form):
    """Dynamically generated form driven by ``AIProviderConfigurationSpec``.

    Fields are added in spec order, split into standard and advanced groups.
    Password fields are never pre-populated (``render_value=False``) and are
    always ``required=False`` at the form level so blank submissions leave
    stored credentials untouched.
    """

    def __init__(
        self,
        *args: Any,
        spec: AIProviderConfigurationSpec,
        current_config: dict[str, Any] | None = None,
        credential_states: dict[str, str] | None = None,
        clear_keys: bool = False,
        **kwargs: Any,
    ) -> None:
        # We need initial values for non-password fields before calling super().
        # Password fields must not receive initial values.
        if "initial" not in kwargs:
            initial: dict[str, Any] = {}
            for f in spec.fields:
                if f.field_type != FIELD_TYPE_PASSWORD:
                    stored = (current_config or {}).get(f.name)
                    initial[f.name] = stored if stored is not None else f.default
            kwargs["initial"] = initial

        super().__init__(*args, **kwargs)
        self._spec = spec
        self._credential_states = dict(credential_states or {})
        self._clear_keys_enabled = bool(clear_keys)
        self._clear_field_names: list[str] = []

        for spec_field in spec.fields:
            self.fields[spec_field.name] = _django_field_for(spec_field)
            if (
                spec_field.field_type == FIELD_TYPE_PASSWORD
                and spec_field.name in self._credential_states
            ):
                # Extend the help text with the current credential state so
                # the operator knows whether a blank submission will retain
                # a value, and if so, where that value lives.
                state = self._credential_states[spec_field.name]
                label = credential_state_label(state)
                addendum = (
                    f"Current state: {label}. "
                    "Leave blank to keep the existing value."
                )
                existing = self.fields[spec_field.name].help_text or ""
                self.fields[spec_field.name].help_text = (
                    f"{existing} {addendum}".strip()
                )
            if (
                self._clear_keys_enabled
                and spec_field.field_type == FIELD_TYPE_PASSWORD
            ):
                clear_name = f"clear_{spec_field.name}"
                self.fields[clear_name] = forms.BooleanField(
                    label=f"Clear saved {spec_field.label}",
                    required=False,
                    help_text=(
                        "Delete the currently stored value on save. "
                        "Overrides any new value submitted in this form."
                    ),
                )
                self._clear_field_names.append(clear_name)

    @property
    def standard_fields(self) -> list[forms.BoundField]:
        """Fields not marked as advanced (including per-field clear boxes)."""
        result: list[forms.BoundField] = []
        for f in self._spec.fields:
            if f.advanced:
                continue
            result.append(self[f.name])
            clear_name = f"clear_{f.name}"
            if clear_name in self.fields:
                result.append(self[clear_name])
        return result

    @property
    def advanced_fields(self) -> list[forms.BoundField]:
        """Fields marked as advanced (including per-field clear boxes)."""
        result: list[forms.BoundField] = []
        for f in self._spec.fields:
            if not f.advanced:
                continue
            result.append(self[f.name])
            clear_name = f"clear_{f.name}"
            if clear_name in self.fields:
                result.append(self[clear_name])
        return result

    def credential_state_for(self, field_name: str) -> str:
        return self._credential_states.get(
            field_name, CREDENTIAL_STATE_NOT_CONFIGURED,
        )

    def clear_flags(self) -> dict[str, bool]:
        """Return the ``clear_<field>`` values from cleaned_data, if present."""
        out: dict[str, bool] = {}
        for name in self._clear_field_names:
            spec_field_name = name[len("clear_"):]
            out[spec_field_name] = bool(self.cleaned_data.get(name))
        return out

    def split_config_and_secrets(self) -> tuple[dict[str, Any], dict[str, str]]:
        """Split cleaned_data into (config, secrets).

        Password fields go into ``secrets``; everything else goes into
        ``config``.  Empty/None password values are omitted from secrets so
        that an existing stored secret is not accidentally overwritten.
        ``clear_<field>`` checkboxes are handled by the view — they are
        NOT reflected in the returned secrets dict.
        """
        config: dict[str, Any] = {}
        secrets: dict[str, str] = {}
        for f in self._spec.fields:
            value = self.cleaned_data.get(f.name)
            if f.field_type == FIELD_TYPE_PASSWORD:
                if value:  # non-empty → update secret
                    secrets[f.name] = str(value)
                # empty → leave existing secret intact (handled by caller)
            elif f.field_type == FIELD_TYPE_BOOLEAN:
                # Always include booleans (False is a legitimate value)
                config[f.name] = bool(value)
            else:
                if value is not None and value != "":
                    config[f.name] = value
        return config, secrets


# ---------------------------------------------------------------------------
# Runtime settings form
# ---------------------------------------------------------------------------

class RuntimeSettingsForm(forms.Form):
    """Runtime-tuning knobs for the AdminAIService.

    All values have safety bounds — anything outside these ranges either
    breaks the service (too small) or opens footguns (too large).  The
    cross-field validator enforces the tool < run timeout invariant that
    the service depends on to guarantee bounded run times.
    """

    max_model_turns = forms.IntegerField(
        label="Max model turns",
        min_value=1, max_value=20, initial=8,
        help_text="Maximum tool-call rounds per run.",
    )
    max_tool_calls = forms.IntegerField(
        label="Max tool calls",
        min_value=1, max_value=50, initial=12,
        help_text="Maximum tool invocations across all rounds.",
    )
    tool_timeout_seconds = forms.FloatField(
        label="Tool timeout (seconds)",
        min_value=1.0, max_value=300.0, initial=10.0,
        help_text="Per-tool deadline.",
    )
    run_timeout_seconds = forms.FloatField(
        label="Run timeout (seconds)",
        min_value=10.0, max_value=600.0, initial=120.0,
        help_text="End-to-end deadline for a single request.",
    )
    max_argument_bytes = forms.IntegerField(
        label="Max argument bytes",
        min_value=1024, max_value=1048576, initial=131072,
        help_text="Reject tool calls with argument payloads above this size.",
    )
    max_result_bytes = forms.IntegerField(
        label="Max result bytes",
        min_value=1024, max_value=1048576, initial=262144,
        help_text="Reject provider responses and tool call arguments above this size.",
    )
    include_content_tools = forms.BooleanField(
        label="Enable content tools",
        required=False, initial=True,
        help_text=(
            "When enabled, the assistant can propose content changes via "
            "cauldron.admin.content."
        ),
    )

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        tool_t = data.get("tool_timeout_seconds")
        run_t = data.get("run_timeout_seconds")
        if tool_t is not None and run_t is not None:
            from .service_factory import ExecutionBudgetError, coerce_execution_budget
            try:
                coerce_execution_budget({
                    "tool_timeout_seconds": tool_t,
                    "run_timeout_seconds": run_t,
                })
            except ExecutionBudgetError:
                self.add_error(
                    "tool_timeout_seconds",
                    "Tool timeout must be less than run timeout.",
                )
        return data
