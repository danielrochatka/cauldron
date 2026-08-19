"""Django system checks for cauldron_ai_web."""
from __future__ import annotations

from django.core.checks import Warning, register


@register()
def check_requests_not_used(app_configs, **kwargs):
    """No-op check. cauldron-ai-web uses stdlib urllib only — no requests dependency."""
    return []
