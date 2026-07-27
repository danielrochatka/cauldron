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

canonical_content_changed = Signal()
canonical_content_changed.__doc__ = """Sent after any operation that durably changes canonical content.

Fires after:
- Successful apply (lifecycle → APPLIED)
- Successful rollback (lifecycle → ROLLED_BACK)

Does NOT fire after:
- Proposal, validation, approval, rejection
- Failed apply or failed rollback
- Reconciliation-required transitions

Keyword arguments passed to receivers:
    sender        -- ContentOperationService class
    change_type   -- str, "apply" or "rollback"
    change_id     -- str, the change request ID
    provider_name -- str, the content provider (e.g. "flatfile")
    changed_by    -- the user object (or None)

Send via send_robust() so receiver failures are isolated.
"""
