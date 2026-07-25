"""Forms for the Admin AI settings page."""
from __future__ import annotations

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
# Provider selection form
# ---------------------------------------------------------------------------

class ProviderSelectForm(forms.Form):
    """Select which AI provider to use."""

    provider = forms.ChoiceField(
        label="AI Provider",
        choices=[],
        required=True,
    )

    def __init__(self, *args: Any, available_providers: list[str], **kwargs: Any) -> None:
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
        "required": spec_field.required,
        "help_text": spec_field.help_text or "",
        "initial": spec_field.default,
    }

    if spec_field.field_type == FIELD_TYPE_PASSWORD:
        # Never pre-populate password fields; render_value=False ensures the
        # current value is never sent back to the browser.
        widget = forms.PasswordInput(render_value=False)
        return forms.CharField(
            **common,
            max_length=spec_field.max_length or 1024,
            widget=widget,
        )

    if spec_field.field_type == FIELD_TYPE_URL:
        f = forms.URLField(**common)
        if spec_field.max_length:
            f.max_length = spec_field.max_length
        return f

    if spec_field.field_type == FIELD_TYPE_INTEGER:
        return forms.IntegerField(**common)

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
        max_length=spec_field.max_length or 1024,
    )


class ProviderConfigForm(forms.Form):
    """Dynamically generated form driven by ``AIProviderConfigurationSpec``.

    Fields are added in spec order, split into standard and advanced groups.
    Password fields are never pre-populated (``render_value=False``).
    """

    def __init__(
        self,
        *args: Any,
        spec: AIProviderConfigurationSpec,
        current_config: dict[str, Any] | None = None,
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

        for spec_field in spec.fields:
            self.fields[spec_field.name] = _django_field_for(spec_field)

    @property
    def standard_fields(self) -> list[forms.BoundField]:
        """Fields not marked as advanced."""
        return [
            self[f.name]
            for f in self._spec.fields
            if not f.advanced
        ]

    @property
    def advanced_fields(self) -> list[forms.BoundField]:
        """Fields marked as advanced."""
        return [
            self[f.name]
            for f in self._spec.fields
            if f.advanced
        ]

    def split_config_and_secrets(self) -> tuple[dict[str, Any], dict[str, str]]:
        """Split cleaned_data into (config, secrets).

        Password fields go into ``secrets``; everything else goes into
        ``config``.  Empty/None password values are omitted from secrets so
        that an existing stored secret is not accidentally overwritten.
        """
        config: dict[str, Any] = {}
        secrets: dict[str, str] = {}
        for f in self._spec.fields:
            value = self.cleaned_data.get(f.name)
            if f.field_type == FIELD_TYPE_PASSWORD:
                if value:  # non-empty → update secret
                    secrets[f.name] = str(value)
                # empty → leave existing secret intact (handled by caller)
            else:
                if value is not None and value != "":
                    config[f.name] = value
        return config, secrets
