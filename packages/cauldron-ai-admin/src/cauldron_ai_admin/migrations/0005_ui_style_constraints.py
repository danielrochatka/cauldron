"""Migration 0005: Add DB CheckConstraints to UIStyleChangeRequest and UIStyleAuditEvent."""
from django.db import migrations, models


_SHA256_RE = r"^[0-9a-f]{64}$"
_UI_STYLE_STATUS_VALUES = ["proposed", "approved", "applied", "rejected", "conflicted"]
_UI_STYLE_EVENT_TYPES = ["proposed", "approved", "rejected", "applied", "conflict", "failed"]


class Migration(migrations.Migration):
    dependencies = [
        ("cauldron_ai_admin", "0004_ui_style_change_request"),
    ]

    operations = [
        # UIStyleChangeRequest constraints
        migrations.AddConstraint(
            model_name="uistylechangerequest",
            constraint=models.CheckConstraint(
                condition=models.Q(status__in=_UI_STYLE_STATUS_VALUES),
                name="uiscr_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="uistylechangerequest",
            constraint=models.CheckConstraint(
                condition=models.Q(scope__in=["admin", "pages"]),
                name="uiscr_scope_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="uistylechangerequest",
            constraint=models.CheckConstraint(
                condition=models.Q(proposed_hash="") | models.Q(proposed_hash__regex=_SHA256_RE),
                name="uiscr_proposed_hash_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="uistylechangerequest",
            constraint=models.CheckConstraint(
                condition=models.Q(base_hash="") | models.Q(base_hash__regex=_SHA256_RE),
                name="uiscr_base_hash_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="uistylechangerequest",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=["approved", "rejected", "conflicted"])
                    | models.Q(reviewed_at__isnull=False)
                ),
                name="uiscr_reviewed_at_when_terminal",
            ),
        ),
        migrations.AddConstraint(
            model_name="uistylechangerequest",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(status="applied")
                    | models.Q(applied_at__isnull=False)
                ),
                name="uiscr_applied_at_when_applied",
            ),
        ),
        # UIStyleAuditEvent constraints
        migrations.AddConstraint(
            model_name="uistyleauditevent",
            constraint=models.CheckConstraint(
                condition=models.Q(event_type__in=_UI_STYLE_EVENT_TYPES),
                name="uisae_event_type_valid",
            ),
        ),
    ]
