"""Migration 0007: Add applying status, applying event type, and apply_lease field.

* Adds ``UIStyleChangeRequest.apply_lease`` (blank CharField, default="").
* Updates the ``uiscr_status_valid`` constraint to include "applying".
* Updates the ``uisae_event_type_valid`` constraint to include "applying".
"""
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("cauldron_ai_admin", "0006_ui_style_base_exists"),
    ]

    operations = [
        # Add the apply_lease field
        migrations.AddField(
            model_name="uistylechangerequest",
            name="apply_lease",
            field=models.CharField(blank=True, default="", max_length=36),
        ),
        # Update the status CHECK constraint to include "applying"
        migrations.RemoveConstraint(
            model_name="uistylechangerequest",
            name="uiscr_status_valid",
        ),
        migrations.AddConstraint(
            model_name="uistylechangerequest",
            constraint=models.CheckConstraint(
                condition=Q(status__in=[
                    "proposed", "approved", "applying",
                    "applied", "rejected", "conflicted",
                ]),
                name="uiscr_status_valid",
            ),
        ),
        # Update the event_type CHECK constraint to include "applying"
        migrations.RemoveConstraint(
            model_name="uistyleauditevent",
            name="uisae_event_type_valid",
        ),
        migrations.AddConstraint(
            model_name="uistyleauditevent",
            constraint=models.CheckConstraint(
                condition=Q(event_type__in=[
                    "proposed", "approved", "rejected",
                    "applying", "applied", "conflict", "failed",
                ]),
                name="uisae_event_type_valid",
            ),
        ),
    ]
