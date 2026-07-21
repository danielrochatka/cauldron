# cauldron-content-operations

The Cauldron content operations module provides the permissioned content control plane: a single `ContentOperationService` that API layers, the Django Admin, and AI agents all use to propose, validate, approve, apply, and roll back content changes.

## Features

- Lifecycle state machine for content change requests (proposed → validated → approved → applying → applied)
- Permission-based authorization (propose, validate, approve, reject, apply, rollback)
- Optimistic concurrency via content hashes and request version numbers
- Idempotency keys to prevent duplicate proposals
- Append-only audit log for all state transitions
- Reconciliation command for interrupted change requests
- Self-approval prevention (configurable)
- Configurable approval requirement

## Configuration

```python
CAULDRON_MODULES = {
    "cauldron.content.operations": {
        "require_approval": True,
        "allow_self_approval": False,
        "max_operations_per_change_set": 100,
    },
}
```
