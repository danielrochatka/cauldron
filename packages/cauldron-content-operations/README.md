# cauldron-content-operations

The Cauldron content operations module provides the permissioned content control plane: a single `ContentOperationService` that API layers, the Django Admin, and AI agents all use to propose, validate, approve, apply, and roll back content changes.

## Features

- Lifecycle state machine for content change requests (proposed → validated → [approved →] applying → applied)
- Permission-based authorization (propose, validate, approve, reject, apply, rollback)
- Optimistic concurrency via content hashes and request version numbers
- Idempotency keys to prevent duplicate proposals
- Append-only audit log for all state transitions
- Reconciliation command for interrupted change requests
- Configurable approval requirement (opt-in; disabled by default)

## Configuration

```python
CAULDRON_MODULES = {
    "cauldron.content.operations": {
        "require_approval": False,      # default; set True to require an approval step
        "max_operations_per_change_set": 100,
    },
}
```
