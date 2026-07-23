# Content Audit

Every state transition on a `ContentChangeRequest` produces an append-only `ContentAuditEvent` record. The audit log cannot be edited or deleted through the Django Admin (both `has_change_permission` and `has_delete_permission` return `False`).

## Event types

| Event type | Description |
|---|---|
| `proposal.created` | A new change request was proposed |
| `validation.requested` | Validation was initiated |
| `validation.succeeded` | Structural validation passed |
| `validation.failed` | Structural validation failed |
| `approval.granted` | A change request was approved |
| `approval.denied` | Approval was denied (e.g. self-approval) |
| `proposal.rejected` | A change request was rejected |
| `application.started` | Application to the repository began |
| `application.succeeded` | Changes were successfully applied |
| `application.failed` | Application failed (conflicts or exception) |
| `rollback.started` | Rollback to snapshot began |
| `rollback.succeeded` | Rollback completed |
| `rollback.failed` | Rollback failed |
| `reconciliation.started` | Reconciliation scan started |
| `reconciliation.completed` | Reconciliation finalized a request |
| `reconciliation.failed` | Reconciliation encountered an error |
| `authorization.denied` | A permission check failed |
| `content.viewed` | Content was read (not emitted by default) |

## What is NOT audited

- Passwords and authentication tokens
- Session data
- Django admin login/logout (use Django's own auth logging for that)
- Read-only content browsing (unless you call `append_audit_event` manually with `CONTENT_VIEWED`)

## Viewing audit history via the service

```python
events = service.get_audit_history(request_id, user=user)
for event in events:
    print(event.event_type, event.occurred_at, event.actor_id)
```

## Viewing audit history via the API

```
GET /cauldron/api/v1/change-requests/<id>/audit/
```

Response:
```json
{
  "data": {
    "events": [
      {
        "event_id": "...",
        "sequence": 1,
        "event_type": "proposal.created",
        "actor_id": 1,
        "occurred_at": "2026-01-15T10:00:00+00:00",
        "previous_state": "",
        "resulting_state": "proposed"
      }
    ]
  }
}
```

## Sequence numbers

Each event has a monotonically increasing `sequence` integer scoped to its `ContentChangeRequest`. A unique constraint on `(change_request, sequence)` prevents duplicate sequence numbers.
