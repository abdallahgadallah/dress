from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def move_existing_sales_customers(apps, schema_editor):
    SalesCustomer = apps.get_model("dresses", "SalesCustomer")
    SalesOrder = apps.get_model("dresses", "SalesOrder")

    for sales_order in SalesOrder.objects.exclude(legacy_customer__isnull=True).select_related("legacy_customer"):
        legacy_customer = sales_order.legacy_customer
        sales_customer, _ = SalesCustomer.objects.get_or_create(
            name=legacy_customer.name,
            phone=legacy_customer.phone,
            defaults={"notes": legacy_customer.notes},
        )
        sales_order.customer = sales_customer
        sales_order.save(update_fields=["customer"])


class Migration(migrations.Migration):
    dependencies = [
        ("dresses", "0015_salesorder_payment_sales_order_salesorderitem"),
    ]

    operations = [
        migrations.CreateModel(
            name="SalesCustomer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("phone", models.CharField(blank=True, max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.RenameField(
            model_name="salesorder",
            old_name="customer",
            new_name="legacy_customer",
        ),
        migrations.AddField(
            model_name="salesorder",
            name="customer",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sales_orders", to="dresses.salescustomer"),
        ),
        migrations.RunPython(move_existing_sales_customers, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="salesorder",
            name="legacy_customer",
        ),
    ]
