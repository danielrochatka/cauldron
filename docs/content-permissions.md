# Content Permissions

All content operation permissions are defined on `ContentPermissionProxy` (a `managed=False` Django model) in the `cauldron_content_operations` app. They are created by the initial migration.

## Permission codenames

| Codename | Full string | Description |
|---|---|---|
| `view_published_content` | `cauldron_content_operations.view_published_content` | View published content items |
| `view_draft_content` | `cauldron_content_operations.view_draft_content` | View draft content items |
| `propose_content_changes` | `cauldron_content_operations.propose_content_changes` | Create change requests |
| `validate_content_changes` | `cauldron_content_operations.validate_content_changes` | Move to validated state |
| `approve_content_changes` | `cauldron_content_operations.approve_content_changes` | Approve for application |
| `reject_content_changes` | `cauldron_content_operations.reject_content_changes` | Reject a change request |
| `apply_content_changes` | `cauldron_content_operations.apply_content_changes` | Apply changes to the repository |
| `rollback_content_changes` | `cauldron_content_operations.rollback_content_changes` | Roll back applied changes |
| `view_content_audit` | `cauldron_content_operations.view_content_audit` | View audit history |

## Group configurations

Create these groups in the Django Admin and assign the permissions listed:

### Content Viewer
- `view_published_content`
- `view_draft_content`

### Content Editor
- `view_published_content`, `view_draft_content`
- `propose_content_changes`
- `validate_content_changes`

### Content Approver
- all Content Editor permissions
- `approve_content_changes`
- `reject_content_changes`

### Content Publisher
- all Content Approver permissions
- `apply_content_changes`
- `rollback_content_changes`

### Content Administrator
- all Content Publisher permissions
- `view_content_audit`

## Assigning permissions

```python
from django.contrib.auth.models import Group, Permission

group = Group.objects.create(name="Content Editor")
perms = Permission.objects.filter(
    codename__in=["view_published_content", "propose_content_changes"],
    content_type__app_label="cauldron_content_operations",
)
group.permissions.set(perms)
user.groups.add(group)
```

Superusers bypass all permission checks.
