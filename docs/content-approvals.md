# Content Approvals

The approval workflow controls who can apply content changes to the repository.

## Configuration

```python
CAULDRON_MODULES = {
    "cauldron.content.operations": {
        "require_approval": False,      # default
        "max_operations_per_change_set": 100,
    },
}
```

## require_approval

When `False` (default), a `VALIDATED` change request can be applied directly:

```
PROPOSED → VALIDATED → APPLYING → APPLIED
```

When `True`, a change request must reach `APPROVED` state before it can be applied:

```
PROPOSED → VALIDATED → APPROVED → APPLYING → APPLIED
```

## Who may approve

Authorization is determined solely by Django permissions. A user with the
`approve_content_changes` permission may approve any change request, regardless
of who created it. Use Django Groups to assign this permission to the appropriate
set of reviewers.

## Audit trail

Every approval is recorded in the `ContentAuditEvent` table with event type
`approval.granted`, along with the actor's user ID and a correlation ID.

## Bypassing approval in automation

For automated pipelines (CI/CD, AI agents) where a human review step is not
needed, leave `require_approval` at its default of `False`. Any user or service
account with `apply_content_changes` permission can apply a validated change
request directly.
