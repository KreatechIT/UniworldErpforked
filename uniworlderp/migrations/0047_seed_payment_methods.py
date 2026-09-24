from django.db import migrations


def seed_payment_methods(apps, schema_editor):
    PaymentMethod = apps.get_model('uniworlderp', 'PaymentMethod')
    starter_methods = [
        ('Cash', 'cash'),
        ('Bank Transfer', 'bank'),
        ('Cheque', 'cheque'),
        ('bKash', 'mobile'),
        ('Nagad', 'mobile'),
    ]
    for name, kind in starter_methods:
        PaymentMethod.objects.get_or_create(name=name, defaults={'kind': kind, 'is_active': True})


def reverse_seed_payment_methods(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('uniworlderp', '0046_payments'),
    ]

    operations = [
        migrations.RunPython(seed_payment_methods, reverse_seed_payment_methods),
    ]
