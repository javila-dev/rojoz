from django.db import migrations, models

ROLE_CHOICES = [
    ("ADMIN", "Administrador"),
    ("DIRECTOR", "Director Comercial"),
    ("ASESOR", "Asesor Comercial"),
    ("CLIENTE", "Cliente Comprador"),
]


def create_roles(apps, schema_editor):
    UserRole = apps.get_model("users", "UserRole")
    User = apps.get_model("users", "User")

    role_map = {}
    for code, _label in ROLE_CHOICES:
        role_obj, _created = UserRole.objects.get_or_create(code=code)
        role_map[code] = role_obj

    for user in User.objects.all():
        code = getattr(user, "role", None)
        if code and code in role_map:
            user.roles.add(role_map[code])


def reverse_roles(apps, schema_editor):
    UserRole = apps.get_model("users", "UserRole")
    UserRole.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_integrationsettings"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserRole",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(choices=ROLE_CHOICES, max_length=20, unique=True, verbose_name="Código")),
            ],
        ),
        migrations.AddField(
            model_name="user",
            name="roles",
            field=models.ManyToManyField(blank=True, related_name="users", to="users.userrole"),
        ),
        migrations.RunPython(create_roles, reverse_roles),
    ]
