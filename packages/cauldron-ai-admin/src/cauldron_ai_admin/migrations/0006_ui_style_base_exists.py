"""Migration 0006: add base_exists field, replace __absent__ sentinel usage.

* Adds ``UIStyleChangeRequest.base_exists`` (default False).
* Replaces ``uiscr_base_hash_format`` with two constraints keyed by
  ``base_exists`` so the on-disk sentinel value no longer needs to leak into
  the ``base_hash`` column.
* Data-migrates existing rows:
    * ``base_hash == "__absent__"`` → ``base_exists=False, base_hash=""``
    * ``base_hash != ""`` and not the sentinel → ``base_exists=True``
* Tightens ``uiscr_proposed_hash_format`` to require a 64-char hex digest.
"""
from django.db import migrations, models


_SHA256_RE = r"^[0-9a-f]{64}$"
_ABSENT_SENTINEL = "__absent__"


def _migrate_absent_forward(apps, schema_editor):
    UIStyleChangeRequest = apps.get_model(
        "cauldron_ai_admin", "UIStyleChangeRequest"
    )
    # Rows that previously stored the sentinel: the file didn't exist.
    UIStyleChangeRequest.objects.filter(base_hash=_ABSENT_SENTINEL).update(
        base_exists=False, base_hash="",
    )
    # Rows with an actual hash: the file existed.
    UIStyleChangeRequest.objects.exclude(base_hash="").exclude(
        base_hash=_ABSENT_SENTINEL,
    ).update(base_exists=True)
    # Rows with an empty base_hash keep base_exists=False (the model default).


def _migrate_absent_backward(apps, schema_editor):
    # Restore the __absent__ sentinel for rows where base_exists=False and
    # the base_hash column is empty. This is a best-effort reverse — rows
    # created after migration 0006 where base_exists=False also had an empty
    # base_hash, so we cannot distinguish them from pre-0006 sentinel rows.
    UIStyleChangeRequest = apps.get_model(
        "cauldron_ai_admin", "UIStyleChangeRequest"
    )
    UIStyleChangeRequest.objects.filter(base_exists=False, base_hash="").update(
        base_hash=_ABSENT_SENTINEL,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("cauldron_ai_admin", "0005_ui_style_constraints"),
    ]

    operations = [
        # 1. Drop the old base_hash-format constraint before we widen the
        #    column semantics.
        migrations.RemoveConstraint(
            model_name="uistylechangerequest",
            name="uiscr_base_hash_format",
        ),
        # 2. Drop the loose proposed_hash constraint before tightening it.
        migrations.RemoveConstraint(
            model_name="uistylechangerequest",
            name="uiscr_proposed_hash_format",
        ),
        # 3. Add the new column so the data migration can populate it.
        migrations.AddField(
            model_name="uistylechangerequest",
            name="base_exists",
            field=models.BooleanField(default=False),
        ),
        # 4. Data migration: interpret existing rows and clear the sentinel.
        migrations.RunPython(
            _migrate_absent_forward, _migrate_absent_backward,
        ),
        # 5. Add the new pair of constraints keyed by base_exists.
        migrations.AddConstraint(
            model_name="uistylechangerequest",
            constraint=models.CheckConstraint(
                condition=models.Q(base_exists=True) | models.Q(base_hash=""),
                name="uiscr_base_exists_false_hash_empty",
            ),
        ),
        migrations.AddConstraint(
            model_name="uistylechangerequest",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(base_exists=False)
                    | models.Q(base_hash__regex=_SHA256_RE)
                ),
                name="uiscr_base_exists_true_hash_valid",
            ),
        ),
        # 6. Reinstate proposed_hash — now required to be a full digest.
        migrations.AddConstraint(
            model_name="uistylechangerequest",
            constraint=models.CheckConstraint(
                condition=models.Q(proposed_hash__regex=_SHA256_RE),
                name="uiscr_proposed_hash_format",
            ),
        ),
    ]
