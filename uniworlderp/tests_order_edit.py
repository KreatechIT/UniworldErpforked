from decimal import Decimal

from django.urls import reverse

from uniworlderp.models import ARInvoice, Payment, PaymentMethod, SalesOrder, SalesOrderItem
from uniworlderp.tests import CustomerEmployeeAssignmentBase
from uniworlderp.views.sales_order_views import confirm_sales_order


class ConfirmedOrderEditTests(CustomerEmployeeAssignmentBase):
    """A confirmed order stays editable until a payment exists; edits flow to stock and the invoice."""

    def make_confirmed(self, quantity=4, unit_price=Decimal('100.00')):
        order = SalesOrder.objects.create(customer=self.c1a, sales_employee=self.emp1, owner=self.admin)
        item = SalesOrderItem.objects.create(
            sales_order=order, product=self.product, unit_price=unit_price, quantity=quantity,
        )
        confirm_sales_order(order)
        order.refresh_from_db()
        return order, item

    def edit(self, order, item, quantity, unit_price):
        data = self.order_post_data(self.c1a, self.emp1, quantity=quantity, item_id=item.pk, initial_forms=1)
        data['order_items-0-unit_price'] = unit_price
        data['action'] = 'save'
        self.client.force_login(self.admin)
        return self.client.post(reverse('customer_vendor:sales_order_update', args=[order.pk]), data)

    def pay(self, order, amount):
        method = PaymentMethod.objects.create(name='Cash-t', kind='cash')
        Payment.objects.create(
            invoice=order.invoice, customer=order.customer, amount=amount, method=method, created_by=self.admin,
        )

    def stock(self):
        self.product.refresh_from_db()
        return self.product.stock_quantity

    def test_unpaid_confirmed_order_is_editable(self):
        order, _ = self.make_confirmed()
        self.assertTrue(order.is_editable)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('customer_vendor:sales_order_update', args=[order.pk])).status_code, 200)

    def test_edit_updates_order_invoice_and_stock_and_keeps_custom_price(self):
        order, item = self.make_confirmed(quantity=4)
        self.assertEqual(self.stock(), 46)

        resp = self.edit(order, item, quantity=6, unit_price='85.00')
        self.assertEqual(resp.status_code, 302)

        item.refresh_from_db()
        self.assertEqual((item.quantity, item.unit_price), (6, Decimal('85.00')))
        self.assertEqual(self.stock(), 44)  # 2 more units out

        order.refresh_from_db()
        invoice = ARInvoice.objects.get(sales_order=order)
        self.assertEqual(invoice.invoice_items.count(), 1)
        inv_item = invoice.invoice_items.get()
        self.assertEqual((inv_item.quantity, inv_item.unit_price), (6, Decimal('85.00')))
        self.assertEqual(invoice.total_amount, order.total_amount)
        self.assertEqual(invoice.total_amount, Decimal('510.00'))

    def test_reducing_quantity_returns_stock(self):
        order, item = self.make_confirmed(quantity=4)
        self.edit(order, item, quantity=1, unit_price='100.00')
        self.assertEqual(self.stock(), 49)
        self.assertEqual(order.invoice.invoice_items.get().quantity, 1)

    def test_edit_beyond_stock_is_rejected_and_nothing_changes(self):
        order, item = self.make_confirmed(quantity=4)
        resp = self.edit(order, item, quantity=500, unit_price='100.00')
        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 4)
        self.assertEqual(self.stock(), 46)
        self.assertEqual(ARInvoice.objects.get(sales_order=order).total_amount, Decimal('400.00'))

    def test_any_payment_locks_the_order(self):
        order, item = self.make_confirmed()
        self.pay(order, Decimal('10.00'))  # partial payment is enough
        self.assertFalse(order.is_editable)

        resp = self.edit(order, item, quantity=9, unit_price='1.00')
        self.assertRedirects(resp, reverse('customer_vendor:sales_order_view', args=[order.pk]))
        item.refresh_from_db()
        self.assertEqual((item.quantity, item.unit_price), (4, Decimal('100.00')))
        self.assertEqual(self.stock(), 46)

    def test_list_shows_edit_only_while_editable(self):
        order, _ = self.make_confirmed()
        url = reverse('customer_vendor:sales_order_update', args=[order.pk])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('customer_vendor:sales_order_list')), url)
        self.pay(order, Decimal('10.00'))
        self.assertNotContains(self.client.get(reverse('customer_vendor:sales_order_list')), url)

    def test_invoice_from_order_cannot_be_edited_directly(self):
        order, _ = self.make_confirmed()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:invoice_update', args=[order.invoice.pk]))
        self.assertRedirects(resp, reverse('customer_vendor:invoice_view', args=[order.invoice.pk]))

    def test_edit_page_keeps_saved_price_in_form(self):
        order, _ = self.make_confirmed(unit_price=Decimal('77.00'))
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:sales_order_update', args=[order.pk]))
        self.assertContains(resp, 'value="77.00"')
