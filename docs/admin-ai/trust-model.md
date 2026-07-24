# Admin AI Trust Model

## Who the AI acts as

Every Admin AI run executes on behalf of an authenticated Django user
(the "actor"). The AI never gains additional authority: it can only
invoke tools the actor is already permitted to invoke, subject to the
same Django permission checks that a human running the same tool would
face. There is no service account, no elevated bot identity, and no
impersonation.

## Entry gate

`AdminAIService.run(actor, request_text)` refuses any actor that lacks
`cauldron_ai_admin.use_admin_ai` and raises `PermissionDenied` **before**
any row is written. Web views additionally require `login_required` +
CSRF middleware; direct programmatic calls (e.g. from Django shell or
a management command) hit the same check.

## What the AI can do

The AI can invoke tools declared through the shared registry. Each tool
carries:

* `required_permission` — a Django permission the actor must hold.
* `risk_level` — determines the enforcement path.
* `argument_schema` — a JSON Schema validated end-to-end before the
  handler runs.
* `owning_module` — the code that ships the tool.

Risk-level policy:

| Risk        | Behaviour                                                                              |
|-------------|----------------------------------------------------------------------------------------|
| READ_ONLY   | Execute if the actor has permission.                                                   |
| PROPOSE     | Execute if permitted; may only create non-canonical proposals (change-requests).       |
| MAINTENANCE | Refuse and record `approval_required`; run finalizes as `waiting_for_approval`.        |
| PRIVILEGED  | Refuse and record `restricted`; run finalizes as `waiting_for_approval`.               |

## What the AI cannot do

* Skip the actor permission check.
* Call any content-operations method other than `create_change_request`
  from a PROPOSE handler (see `PROPOSAL_ALLOWED_METHODS`).
* Access filesystem paths, environment variables, or the process
  registry outside the injected content service.
* Execute after the run deadline has elapsed (each tool call checks
  `context.deadline_remaining_seconds()`).
* Persist raw exception text or secrets — every string is passed through
  `redact()` or `redact_exception()` and bounded to a byte budget.

## Audit trail

Every run produces an `AdminAIRun` row; every tool attempt (including
denials) produces an `AdminAIToolInvocation` row. These are read-only in
the Django admin and cannot be deleted through the API.

Custom permissions:

* `use_admin_ai` — invoke the assistant.
* `view_admin_ai_runs` — see `AdminAIRun` in the Django admin.
* `view_admin_ai_audit` — see `AdminAIToolInvocation` in the admin.
