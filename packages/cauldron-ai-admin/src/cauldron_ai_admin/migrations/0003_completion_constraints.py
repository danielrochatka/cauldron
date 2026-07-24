"""Enumerate invocation status/risk values and enforce completion timestamps.

Adds four safety-net check constraints to the existing audit tables so
malformed rows cannot land even if a service-layer bug tries to write
them.
"""
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("cauldron_ai_admin", "0002_constraints_and_tool_call_id"),
    ]

    operations = [
        # -------- AdminAIToolInvocation status enumeration
        migrations.AddConstraint(
            model_name="adminaitoolinvocation",
            constraint=models.CheckConstraint(
                condition=Q(status__in=[
                    "requested",
                    "authorized",
                    "running",
                    "completed",
                    "denied",
                    "failed",
                    "timed_out",
                ]),
                name="adminaitoolinvocation_status_valid",
            ),
        ),
        # -------- AdminAIToolInvocation risk-level enumeration
        migrations.AddConstraint(
            model_name="adminaitoolinvocation",
            constraint=models.CheckConstraint(
                condition=Q(risk_level__in=[
                    "READ_ONLY",
                    "PROPOSE",
                    "MAINTENANCE",
                    "PRIVILEGED",
                ]),
                name="adminaitoolinvocation_risk_level_valid",
            ),
        ),
        # -------- AdminAIRun: no completed_at while active
        migrations.AddConstraint(
            model_name="adminairun",
            constraint=models.CheckConstraint(
                condition=(
                    ~Q(status__in=["created", "running"])
                    | Q(completed_at__isnull=True)
                ),
                name="adminairun_no_completed_at_when_active",
            ),
        ),
        # -------- AdminAIRun: completed_at required when terminal
        migrations.AddConstraint(
            model_name="adminairun",
            constraint=models.CheckConstraint(
                condition=(
                    ~Q(status__in=[
                        "waiting_for_approval",
                        "completed",
                        "failed",
                        "cancelled",
                    ])
                    | Q(completed_at__isnull=False)
                ),
                name="adminairun_completed_at_when_terminal",
            ),
        ),
        # -------- AdminAIToolInvocation: no completed_at while active
        migrations.AddConstraint(
            model_name="adminaitoolinvocation",
            constraint=models.CheckConstraint(
                condition=(
                    ~Q(status__in=["requested", "authorized", "running"])
                    | Q(completed_at__isnull=True)
                ),
                name="adminaitoolinvocation_no_completed_at_when_active",
            ),
        ),
        # -------- AdminAIToolInvocation: completed_at required when terminal
        migrations.AddConstraint(
            model_name="adminaitoolinvocation",
            constraint=models.CheckConstraint(
                condition=(
                    ~Q(status__in=[
                        "completed",
                        "denied",
                        "failed",
                        "timed_out",
                    ])
                    | Q(completed_at__isnull=False)
                ),
                name="adminaitoolinvocation_completed_at_when_terminal",
            ),
        ),
    ]
