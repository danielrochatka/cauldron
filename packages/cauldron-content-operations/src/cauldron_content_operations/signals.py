"""Django signals emitted by ContentOperationService."""
from django.dispatch import Signal

content_change_applied = Signal()
"""Sent after a content change request is successfully applied.

Keyword arguments passed to receivers:
    sender        -- ContentOperationService class
    request_id    -- str, the applied change request ID
    provider_name -- str, the content provider (e.g. "flatfile")
    applied_by    -- the user object (or None for system applies)

Receivers should be fault-tolerant. The signal is sent with send_robust()
so individual receiver failures are logged rather than propagated.
"""
