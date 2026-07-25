from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("cauldron_ai_admin", "0008_alter_uistylechangerequest_status"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="adminairun",
            options={
                "ordering": ["-created_at"],
                "permissions": [
                    ("use_admin_ai", "Can invoke the Admin AI assistant"),
                    ("view_admin_ai_runs", "Can view Admin AI run history"),
                    ("view_admin_ai_audit", "Can view Admin AI audit records"),
                    ("manage_admin_ai_settings", "Can manage Admin AI settings"),
                ],
            },
        ),
    ]
