# Django Content Control Plane Example

This example demonstrates Cauldron's content control plane: a permissioned, audited workflow for proposing, approving, and applying content changes through the Django Admin and a JSON HTTP API.

## Prerequisites

- Python 3.11+
- All Cauldron packages installed (see `requirements.txt`)

## Setup

### 1. Install dependencies

From the repo root:

```bash
pip install -e '.[dev]'
pip install -e packages/cauldron-django-state
pip install -e packages/cauldron-django-auth
pip install -e packages/cauldron-django-admin
pip install -e packages/cauldron-content
pip install -e packages/cauldron-workspace-flatfile
pip install -e packages/cauldron-cms-flatfile
pip install -e packages/cauldron-content-operations
pip install -e packages/cauldron-content-api
pip install -e packages/cauldron-admin-content
```

### 2. Run migrations

```bash
cd examples/django-content-control-plane
python manage.py migrate
```

### 3. Create a superuser

```bash
python manage.py createsuperuser
```

### 4. Create permission groups (optional)

Create these groups in the Django Admin and assign permissions from `cauldron_content_operations`:

- **Content Viewer**: `view_published_content`, `view_draft_content`
- **Content Editor**: + `propose_content_changes`, `validate_content_changes`
- **Content Approver**: + `approve_content_changes`, `reject_content_changes`
- **Content Publisher**: + `apply_content_changes`, `rollback_content_changes`
- **Content Administrator**: all permissions above + `view_content_audit`

### 5. Check the operations module status

```bash
python manage.py cauldron_content_operations_status
python manage.py cauldron_content_operations_status --json
```

### 6. Start the development server

```bash
python manage.py runserver
```

## Usage

### Django Admin

1. Log in to `/admin/` with your superuser credentials.
2. Navigate to **Cauldron Content Operations > Content Change Requests**.
3. Use the lifecycle action endpoints to validate, approve, reject, apply, or roll back requests.
4. View append-only audit history under **Content Audit Events**.

### Creating a content proposal

Navigate to `/admin/cauldron/content-proposal/` (if using `cauldron_admin_content.urls`) or use the HTTP API:

```bash
curl -X POST http://localhost:8000/cauldron/api/v1/change-requests/ \
  -H "Content-Type: application/json" \
  -H "X-CSRFToken: <token>" \
  --cookie "sessionid=<session>" \
  -d '{
    "provider_name": "flatfile",
    "description": "Add a new page",
    "operations": [{
      "kind": "create",
      "collection": "pages",
      "item_id": "new-page",
      "slug": "new-page",
      "status": "draft",
      "schema": "pages",
      "data": {"title": "New Page"},
      "body": "# New Page\n\nContent here."
    }]
  }'
```

### Workflow

1. **Propose**: POST to `/cauldron/api/v1/change-requests/` — creates a change request in `proposed` state.
2. **Validate**: POST to `/cauldron/api/v1/change-requests/<id>/validate/` — validates structure and moves to `validated`.
3. **Approve**: POST to `/cauldron/api/v1/change-requests/<id>/approve/` — moves to `approved` (different user required by default).
4. **Apply**: POST to `/cauldron/api/v1/change-requests/<id>/apply/` — applies changes, moves to `applied`.
5. **Rollback** (if needed): POST to `/cauldron/api/v1/change-requests/<id>/rollback/` — rolls back to previous state.

### Reconciliation

If the server crashes during application, run:

```bash
python manage.py cauldron_content_reconcile --dry-run
python manage.py cauldron_content_reconcile
```

### Browsing content via API

```bash
curl http://localhost:8000/cauldron/api/v1/collections/
curl http://localhost:8000/cauldron/api/v1/collections/pages/items/
curl http://localhost:8000/cauldron/api/v1/collections/pages/items/home/
```
