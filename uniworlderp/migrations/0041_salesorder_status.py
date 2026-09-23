from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('uniworlderp', '0040_customervendor_sales_employee'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesorder',
            name='status',
            field=models.CharField(choices=[('draft', 'Draft'), ('confirmed', 'Confirmed')], db_index=True, default='draft', max_length=10),
        ),
    ]
