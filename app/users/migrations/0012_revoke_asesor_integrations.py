from django.db import migrations


def revoke_asesor_integrations(apps, schema_editor):
    """Las integraciones exponen API keys: el rol ASESOR no debe acceder."""
    RolePermission = apps.get_model("users", "RolePermission")
    RolePermission.objects.filter(
        role_code="ASESOR",
        permission_key="users:integrations",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0011_gemini_integration"),
    ]

    operations = [
        migrations.RunPython(revoke_asesor_integrations, migrations.RunPython.noop),
    ]
