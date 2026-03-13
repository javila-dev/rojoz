from django.db import migrations, models


def copy_required_categories_to_house_types(apps, schema_editor):
    HouseType = apps.get_model("inventory", "HouseType")
    FinishCategory = apps.get_model("inventory", "FinishCategory")

    required_categories_by_project = {}
    for category in FinishCategory.objects.filter(is_required=True).values("id", "project_id"):
        required_categories_by_project.setdefault(category["project_id"], []).append(category["id"])

    for house_type in HouseType.objects.all().iterator():
        category_ids = required_categories_by_project.get(house_type.project_id, [])
        if category_ids:
            house_type.required_finish_categories.add(*category_ids)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0013_housetype_max_discount_percent"),
    ]

    operations = [
        migrations.AddField(
            model_name="housetype",
            name="required_finish_categories",
            field=models.ManyToManyField(
                blank=True,
                related_name="required_by_house_types",
                to="inventory.finishcategory",
                verbose_name="Categorías de acabado obligatorias",
            ),
        ),
        migrations.RunPython(copy_required_categories_to_house_types, noop_reverse),
    ]
