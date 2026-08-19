"""Django system checks for cauldron_ai_attachments."""
from __future__ import annotations

from django.core.checks import Error, Warning, register


@register()
def check_pypdf_available(app_configs, **kwargs):
    errors = []
    try:
        import pypdf  # noqa: F401
    except ImportError:
        errors.append(
            Error(
                "pypdf is not installed. PDF extraction will not work.",
                hint="Install it with: pip install pypdf>=4.0",
                id="cauldron_ai_attachments.E001",
            )
        )
    return errors


@register()
def check_python_docx_available(app_configs, **kwargs):
    errors = []
    try:
        import docx  # noqa: F401
    except ImportError:
        errors.append(
            Error(
                "python-docx is not installed. DOCX extraction will not work.",
                hint="Install it with: pip install python-docx>=1.1",
                id="cauldron_ai_attachments.E002",
            )
        )
    return errors
