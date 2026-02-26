from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Max


def backfill_project_and_numbers(apps, schema_editor):
    Sale = apps.get_model("sales", "Sale")
    HouseType = apps.get_model("inventory", "HouseType")

    sales = Sale.objects.select_related("house_type").order_by("date_created", "id")
    for sale in sales:
        if sale.house_type_id and not sale.project_id:
            sale.project_id = sale.house_type.project_id
        sale.save(update_fields=["project"])

    projects = Sale.objects.values_list("project_id", flat=True).distinct()
    for project_id in projects:
        if not project_id:
            continue
        qs = Sale.objects.filter(project_id=project_id).order_by("date_created", "id")
        current = qs.aggregate(max_number=Max("contract_number")).get("max_number")
        current = current or 0
        for sale in qs.filter(contract_number__isnull=True):
            current += 1
            sale.contract_number = current
            sale.save(update_fields=["contract_number"])


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("sales", "0009_merge_0008_contractparty_phone_0008_payment_schedule"),
        ("inventory", "0004_project_payment_params"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="project",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sales", to="inventory.project"),
        ),
        migrations.AddField(
            model_name="sale",
            name="contract_number",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="Número de contrato"),
        ),
        migrations.RunPython(backfill_project_and_numbers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="sale",
            name="project",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sales", to="inventory.project"),
        ),
        migrations.AddConstraint(
            model_name="sale",
            constraint=models.UniqueConstraint(condition=models.Q(("contract_number__isnull", False)), fields=("project", "contract_number"), name="unique_contract_number_per_project"),
        ),
    ]
