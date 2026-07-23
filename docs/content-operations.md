# Content Operations

The content control plane provides a permissioned, audited workflow for proposing, validating, approving, and applying content changes. All operations flow through a single application service — `ContentOperationService` — regardless of whether the caller is the Django Admin, the HTTP API, an AI agent, or a management command.

## Why a single service?

API layers, Admin views, and AI agents share one `ContentOperationService` instance. This means:

- Authorization is enforced in one place.
- Audit events are generated for every mutation regardless of caller.
- Business rules (require_approval, allow_self_approval, idempotency) apply uniformly.
- CMS provider independence: the service delegates to whichever `ContentRepository` the `ContentRouter` resolves — flatfile, database, or remote API.

## Permission codenames

All permissions live on the `ContentPermissionProxy` model in the `cauldron_content_operations` app:

| Codename | Description |
|---|---|
| `view_published_content` | Can view published content |
| `view_draft_content` | Can view draft content |
| `propose_content_changes` | Can propose content changes |
| `validate_content_changes` | Can validate content changes |
| `approve_content_changes` | Can approve content changes |
| `reject_content_changes` | Can reject content changes |
| `apply_content_changes` | Can apply content changes |
| `rollback_content_changes` | Can roll back content changes |
| `view_content_audit` | Can view content audit history |

## Suggested group configurations

- **Content Viewer**: `view_published_content`, `view_draft_content`
- **Content Editor**: above + `propose_content_changes`, `validate_content_changes`
- **Content Approver**: above + `approve_content_changes`, `reject_content_changes`
- **Content Publisher**: above + `apply_content_changes`, `rollback_content_changes`
- **Content Administrator**: all above + `view_content_audit`

## Lifecycle states

```
PROPOSED → VALIDATED → APPROVED → APPLYING → APPLIED
         ↘ REJECTED                         ↘ APPLY_FAILED
                                             ↓
                                          ROLLING_BACK → ROLLED_BACK
                                                       ↘ ROLLBACK_FAILED
                              RECONCILIATION_REQUIRED ←──── (any transitional state)
```

Terminal states: `APPLIED`, `REJECTED`, `ROLLED_BACK`.

Transitional states: `APPLYING`, `ROLLING_BACK`. These require reconciliation if interrupted.

## Approval configuration

```python
CAULDRON_MODULES = {
    "cauldron.content.operations": {
        "require_approval": True,       # VALIDATED must reach APPROVED before applying
        "allow_self_approval": False,   # The proposer cannot also approve
        "max_operations_per_change_set": 100,
    },
}
```

When `require_approval` is `False`, a `VALIDATED` change request may be applied directly.

## Optimistic concurrency

Every `ContentChangeRequest` has a `request_version` integer that increments on each state transition. Pass `expected_version` to any mutation method to detect races:

```python
result = service.approve_change_request(request_id, user=user, expected_version=3)
if not result.ok and result.error.code == "conflict.version":
    # Someone else mutated the request; reload and retry.
```

Content hashes (`payload_hash`, `expected_hash` on operations) protect against applying stale operations to changed content.

## Idempotency

Pass `idempotency_key` when creating a change request to prevent duplicates:

```python
result = service.create_change_request(
    user=user,
    operations=[...],
    provider_name="flatfile",
    idempotency_key="deploy-2026-01-15-home-page",
)
# A second call with the same key returns the original request.
assert result.meta.get("idempotent")
```

## Applying and rolling back

`apply_change_request` is idempotent: if the request is already `APPLIED`, it returns success with `meta["idempotent"] = True`.

`rollback_change_request` delegates to `SnapshotService`. If no snapshot was taken (workspace not configured or flatfile content not snapshotted), rollback will fail gracefully.

## Cross-resource transaction limitations

The service uses Django's `select_for_update()` inside `transaction.atomic()` to prevent concurrent mutations to the same change request. However, there is no distributed transaction between the Django database and the content repository. If the server crashes between committing `APPLYING` and completing the repository write, the request stays in `APPLYING`. Run reconciliation to resolve this:

```bash
python manage.py cauldron_content_reconcile --dry-run
python manage.py cauldron_content_reconcile
```

## Reconciliation behavior

Reconciliation inspects requests in `APPLYING`, `ROLLING_BACK`, and `RECONCILIATION_REQUIRED` states. If a workspace result file exists, the request is finalized as `APPLIED`. Otherwise, it is left as ambiguous and flagged for manual review.
