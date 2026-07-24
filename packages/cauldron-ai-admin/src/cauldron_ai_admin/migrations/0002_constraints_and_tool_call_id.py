"""Add check/unique constraints, widen tool_call_id, and add per-invocation
snapshot fields for required_permission and correlation_id."""
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("cauldron_ai_admin", "0001_initial"),
    ]

    operations = [
        # -------- widen tool_call_id and add new snapshot columns --------
        migrations.AlterField(
            model_name="adminaitoolinvocation",
            name="tool_call_id",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
        migrations.AddField(
            model_name="adminaitoolinvocation",
            name="required_permission",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
        migrations.AddField(
            model_name="adminaitoolinvocation",
            name="correlation_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        # -------- constraints --------
        migrations.AddConstraint(
            model_name="adminairun",
            constraint=models.CheckConstraint(
                condition=~Q(provider_name=""),
                name="adminairun_provider_name_nonempty",
            ),
        ),
        migrations.AddConstraint(
            model_name="adminairun",
            constraint=models.CheckConstraint(
                condition=Q(version__gt=0),
                name="adminairun_version_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="adminairun",
            constraint=models.CheckConstraint(
                condition=Q(status__in=[
                    "created",
                    "running",
                    "waiting_for_approval",
                    "completed",
                    "failed",
                    "cancelled",
                ]),
                name="adminairun_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="adminaitoolinvocation",
            constraint=models.CheckConstraint(
                condition=~Q(tool_name=""),
                name="adminaitoolinvocation_tool_name_nonempty",
            ),
        ),
        migrations.AddConstraint(
            model_name="adminaitoolinvocation",
            constraint=models.UniqueConstraint(
                fields=["run", "tool_call_id"],
                condition=~Q(tool_call_id=""),
                name="adminaitoolinvocation_tool_call_id_unique",
            ),
        ),
    ]
