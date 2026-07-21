# Content Approvals

The approval workflow controls who can apply content changes to the repository.

## Configuration

```python
CAULDRON_MODULES = {
    "cauldron.content.operations": {
        "require_approval": True,       # default
        "allow_self_approval": False,   # default
    },
}
```

## require_approval

When `True` (default), a change request must reach `APPROVED` state before it can be applied. The workflow is:

```
PROPOSED → VALIDATED → APPROVED → APPLYING → APPLIED
```

When `False`, a `VALIDATED` change request can be applied directly:

```
PROPOSED → VALIDATED → APPLYING → APPLIED
```

## allow_self_approval

When `False` (default), the user who created a change request (`created_by`) cannot also approve it. This enforces a four-eyes principle.

When `True`, self-approval is permitted. This is appropriate for single-operator or automated deployment scenarios.

## Audit trail

Every approval or denial is recorded in the `ContentAuditEvent` table with event type `approval.granted` or `approval.denied`, along with the actor's user ID and a correlation ID.

## Bypassing approval in automation

For automated pipelines (CI/CD, AI agents), configure `require_approval: False` or `allow_self_approval: True` to allow the pipeline user to propose and apply changes in sequence.
