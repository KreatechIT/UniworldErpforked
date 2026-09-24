from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('uniworlderp', '0043_confirm_existing_sales_orders'),
    ]

    operations = [
        migrations.AddField(
            model_name='arinvoice',
            name='discount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Discount amount to be subtracted from subtotal', max_digits=12),
        ),
        migrations.AddField(
            model_name='arinvoice',
            name='shipping',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Shipping amount to be added to subtotal after discount', max_digits=12),
        ),
    ]
