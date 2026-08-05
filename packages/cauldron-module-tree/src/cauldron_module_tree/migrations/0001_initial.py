"""Initial migration for cauldron_module_tree."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ModuleEnabledOverride",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("slug", models.CharField(db_index=True, max_length=200, unique=True)),
                ("enabled", models.BooleanField()),
                ("changed_at", models.DateTimeField(auto_now=True)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="module_overrides",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("reason", models.TextField(blank=True)),
            ],
            options={
                "verbose_name": "Module enabled override",
                "verbose_name_plural": "Module enabled overrides",
                "ordering": ["slug"],
                "permissions": [
                    ("view_module_tree", "Can view module dependency tree"),
                    ("change_module_state", "Can enable or disable modules"),
                ],
            },
        ),
    ]
