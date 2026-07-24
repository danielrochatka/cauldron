# Admin AI Permissions

`cauldron_ai_admin` ships three custom Django permissions plus per-tool
permissions.

## Custom permissions

| Codename                         | Grants                                                                            |
|----------------------------------|-----------------------------------------------------------------------------------|
| `cauldron_ai_admin.use_admin_ai` | Invoke the assistant (call `AdminAIService.run` or POST the admin AI view).       |
| `cauldron_ai_admin.view_admin_ai_runs`  | View `AdminAIRun` in Django admin.                                          |
| `cauldron_ai_admin.view_admin_ai_audit` | View `AdminAIToolInvocation` in Django admin.                               |

The Django admin registrations override `has_module_permission` and
`has_view_permission` so a user holding only `use_admin_ai` cannot see
the audit history. `has_add_permission`, `has_change_permission`, and
`has_delete_permission` always return `False`.

## Per-tool permissions

Every `AdminAIToolDefinition.required_permission` is enforced twice:

* On listing — `AdminAIToolRegistry.list_for_actor(actor)` filters the
  visible tool set. The AI never receives a definition it cannot use.
* On execution — the service re-checks before running the handler and
  records `tool.permission_denied` if it fails.

## Proposal boundary

`content.create_proposal` is a PROPOSE-level tool. Even when the actor
holds `cauldron_content_operations.propose_content_changes`, the
handler is only ever allowed to call `create_change_request` on the
content service — never `apply_change_request`, `approve_change_request`,
or `rollback_change_request`. The invariant is asserted in
`PROPOSAL_ALLOWED_METHODS` and verified by unit tests.

## Recommended role split

| Role                | Permissions                                                                         |
|---------------------|-------------------------------------------------------------------------------------|
| AI operator         | `use_admin_ai` + per-tool permissions the operator should hold as a human.           |
| Auditor             | `view_admin_ai_runs`, `view_admin_ai_audit`.                                        |
| Approver            | `cauldron_content_operations.approve_content_change_requests` (unrelated to AI).    |

Never grant `use_admin_ai` to unauthenticated users or service accounts
without human oversight.
