from django.contrib.auth.hashers import make_password
from django.db import migrations


def seed_users_and_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    manager_group, _ = Group.objects.get_or_create(name="manager")
    employee_group, _ = Group.objects.get_or_create(name="employee")

    admin_user, created = User.objects.get_or_create(
        username="admin",
        defaults={
            "is_staff": True,
            "is_superuser": True,
            "password": make_password("Admin@123"),
        },
    )
    if not created:
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.password = make_password("Admin@123")
        admin_user.save(update_fields=["is_staff", "is_superuser", "password"])

    manager_user, created = User.objects.get_or_create(
        username="manager",
        defaults={
            "is_staff": True,
            "password": make_password("Manager@123"),
        },
    )
    if not created:
        manager_user.is_staff = True
        manager_user.password = make_password("Manager@123")
        manager_user.save(update_fields=["is_staff", "password"])
    manager_user.groups.add(manager_group)

    employee_user, created = User.objects.get_or_create(
        username="employee",
        defaults={
            "password": make_password("Employee@123"),
        },
    )
    if not created:
        employee_user.password = make_password("Employee@123")
        employee_user.save(update_fields=["password"])
    employee_user.groups.add(employee_group)


class Migration(migrations.Migration):

    dependencies = [
        ("dresses", "0005_transaction_rent_start_date"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(seed_users_and_groups, migrations.RunPython.noop),
    ]
