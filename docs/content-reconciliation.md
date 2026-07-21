# Content Reconciliation

When a server process is interrupted while a change request is in the `APPLYING` or `ROLLING_BACK` state, the request is left in a transitional state. Reconciliation inspects these requests and finalizes them where possible.

## Running reconciliation

```bash
# Inspect without making changes
python manage.py cauldron_content_reconcile --dry-run

# Inspect and apply corrections
python manage.py cauldron_content_reconcile

# JSON output
python manage.py cauldron_content_reconcile --dry-run --json
```

## How it works

For each request in `APPLYING`, `ROLLING_BACK`, or `RECONCILIATION_REQUIRED`:

1. The reconciler reads the workspace result file (`result.json`) for the change set.
2. If the result file exists, the application likely completed. The request is moved to `APPLIED` and an audit event `reconciliation.completed` is recorded.
3. If no result file exists, the outcome is ambiguous. The request is left as-is and flagged with action `leave_ambiguous` for manual review.

## Ambiguous cases

An ambiguous case means the change set file was written (application started) but no result file was saved (application may or may not have completed). Manual steps:

1. Check the content repository directly to see if the changes are present.
2. If changes are present: manually update the request to `APPLIED` via `RECONCILIATION_REQUIRED` state.
3. If changes are not present: manually update the request to `APPLY_FAILED` and re-attempt.

## System checks

Two database-touching Django system checks run at startup:

- `cauldron.content.operations.W700`: warns if any requests are stuck in transitional states.
- `cauldron.content.operations.W701`: warns if any requests require reconciliation.

These are warnings (not errors) so the server starts regardless. Run reconciliation to clear them.

## JSON output format

```json
{
  "dry_run": true,
  "results": [
    {
      "request_id": "...",
      "current_state": "applying",
      "action": "finalize_applied",
      "reason": "Workspace result file found; application likely completed.",
      "applied": false
    }
  ],
  "total": 1
}
```

When `dry_run` is `false`, `applied` will be `true` for finalized requests.
