from django.db import migrations


OLD_CONTRACT_KEYS = (
    "sales:contract_project_select",
    "sales:contract_list_pending",
    "sales:contract_list_approved",
)


def grant_contract_list(apps, schema_editor):
    """La lista única de contratos reemplaza el selector proyecto → estado → lista.

    Todo rol que podía entrar por las rutas antiguas recibe el nuevo permiso.
    """
    RolePermission = apps.get_model("users", "RolePermission")
    roles = set(
        RolePermission.objects.filter(permission_key__in=OLD_CONTRACT_KEYS, allowed=True)
        .values_list("role_code", flat=True)
    )
    for role_code in roles:
        RolePermission.objects.update_or_create(
            role_code=role_code,
            permission_key="sales:contract_list",
            defaults={"allowed": True, "label": "Ver lista de contratos", "path": "ventas/contratos/"},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0012_revoke_asesor_integrations"),
    ]

    operations = [
        migrations.RunPython(grant_contract_list, migrations.RunPython.noop),
    ]
