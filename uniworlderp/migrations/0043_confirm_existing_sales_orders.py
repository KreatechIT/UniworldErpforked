from django.db import migrations


def confirm_existing_orders(apps, schema_editor):
    SalesOrder = apps.get_model('uniworlderp', 'SalesOrder')
    SalesOrder.objects.filter(status='draft').update(status='confirmed')


def reverse_confirm_existing_orders(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('uniworlderp', '0042_alter_salesorder_status'),
    ]

    operations = [
        migrations.RunPython(confirm_existing_orders, reverse_confirm_existing_orders),
    ]
