from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from uniworlderp.forms import CustomerVendorForm, SalesOrderForm
from uniworlderp.models import (
    ARInvoice, CustomerVendor, Payment, PaymentMethod, Product, SalesEmployee, SalesOrder, SalesOrderItem,
)
from uniworlderp.views.sales_order_views import confirm_sales_order


class CustomerEmployeeAssignmentBase(TestCase):
    """Shared fixture: two employees, customers assigned to each, one unassigned."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('admin', 'admin@example.com', 'pass')

        cls.clerk = User.objects.create_user('clerk', password='pass')
        cls.clerk.user_permissions.add(*Permission.objects.filter(codename__in=[
            'add_salesorder', 'change_salesorder', 'view_salesorder',
            'view_customervendor', 'add_customervendor', 'change_customervendor',
            'view_salesemployee', 'change_salesemployee', 'add_salesemployee',
        ]))

        cls.emp_user = User.objects.create_user('atiq', password='pass')
        cls.emp_user.user_permissions.add(*Permission.objects.filter(codename__in=[
            'add_salesorder', 'change_salesorder', 'view_salesorder',
        ]))

        cls.emp1 = SalesEmployee.objects.create(full_name='Atiq', user=cls.emp_user, owner=cls.admin)
        cls.emp2 = SalesEmployee.objects.create(full_name='Jabed', owner=cls.admin)
        cls.inactive_emp = SalesEmployee.objects.create(full_name='Old Staff', is_active=False, owner=cls.admin)

        def customer(name, employee):
            return CustomerVendor.objects.create(
                name=name, phone_number='01700000000', entity_type='customer',
                sales_employee=employee, owner=cls.admin,
            )

        cls.c1a = customer('Alpha Traders', cls.emp1)
        cls.c1b = customer('Beta Store', cls.emp1)
        cls.c2 = customer('Gamma Mart', cls.emp2)
        cls.unassigned = customer('Delta Shop', None)
        cls.vendor = CustomerVendor.objects.create(
            name='Vendor Co', phone_number='01800000000', entity_type='vendor', owner=cls.admin,
        )

        cls.product = Product.objects.create(
            name='Chemical X', sku='SKU-TEST1', price=Decimal('100.00'),
            stock_quantity=50, owner=cls.admin,
        )

    def order_post_data(self, customer, employee=None, quantity=2, item_id=None, initial_forms=0):
        data = {
            'customer': customer.pk if customer else '',
            'sales_employee': employee.pk if employee else '',
            'delivery_status': 'P',
            'order_date': '2026-09-23',
            'discount': '0',
            'shipping': '0',
            'notes': '',
            'order_items-TOTAL_FORMS': '1',
            'order_items-INITIAL_FORMS': str(initial_forms),
            'order_items-MIN_NUM_FORMS': '0',
            'order_items-MAX_NUM_FORMS': '1000',
            'order_items-0-product': str(self.product.pk),
            'order_items-0-quantity': str(quantity),
            'order_items-0-unit_price': '100.00',
            'order_items-0-stock_quantity': str(self.product.stock_quantity),
            'order_items-0-display_total': '',
            'order_items-0-total_discount': '0',
        }
        if item_id:
            data['order_items-0-id'] = str(item_id)
            data['order_items-0-sales_order'] = ''
        return data


class CustomerAssignmentTests(CustomerEmployeeAssignmentBase):

    def test_migrations_are_up_to_date(self):
        out = StringIO()
        call_command('makemigrations', 'uniworlderp', '--check', '--dry-run', stdout=out)

    def test_customer_form_saves_sales_employee(self):
        form = CustomerVendorForm(data={
            'name': 'New Customer', 'phone_number': '01711111111', 'business_type': 'retailer',
            'entity_type': 'customer', 'sales_employee': self.emp2.pk,
        })
        self.assertTrue(form.is_valid(), form.errors)
        form.instance.owner = self.admin
        customer = form.save()
        self.assertEqual(customer.sales_employee, self.emp2)
        self.assertIn(customer, self.emp2.customers.all())

    def test_customer_form_employee_is_optional(self):
        form = CustomerVendorForm(data={
            'name': 'No Emp', 'phone_number': '01711111111', 'business_type': 'retailer',
            'entity_type': 'customer',
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_customer_form_shows_inactive_employees_too(self):
        choices = CustomerVendorForm().fields['sales_employee'].queryset
        self.assertIn(self.inactive_emp, choices)

    def test_deleting_employee_unassigns_customers(self):
        self.emp2.delete()
        self.c2.refresh_from_db()
        self.assertIsNone(self.c2.sales_employee)

    def test_create_customer_view_with_employee(self):
        self.client.force_login(self.clerk)
        resp = self.client.post(reverse('customer_vendor:customer_create'), {
            'name': 'Via View', 'phone_number': '01722222222', 'business_type': 'wholesaler',
            'entity_type': 'customer', 'sales_employee': self.emp1.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(CustomerVendor.objects.get(name='Via View').sales_employee, self.emp1)

    def test_list_shows_employee_column_and_filters(self):
        self.client.force_login(self.clerk)
        url = reverse('customer_vendor:customer_list')

        resp = self.client.get(url)
        self.assertContains(resp, 'Sales Employee')
        self.assertContains(resp, 'All Employees')

        resp = self.client.get(url, {'sales_employee': self.emp1.pk})
        self.assertEqual(set(resp.context['customers']), {self.c1a, self.c1b})

        resp = self.client.get(url, {'sales_employee': 'none'})
        self.assertEqual(set(resp.context['customers']), {self.unassigned, self.vendor})

        resp = self.client.get(url, {'sales_employee': 'abc'})
        self.assertEqual(resp.status_code, 200)


class SalesOrderFormRestrictionTests(CustomerEmployeeAssignmentBase):

    def test_admin_sees_all_customers_and_employee_optional(self):
        form = SalesOrderForm(user=self.admin)
        self.assertFalse(form.restrict_customers)
        self.assertEqual(set(form.fields['customer'].queryset), {self.c1a, self.c1b, self.c2, self.unassigned})
        self.assertFalse(form.fields['sales_employee'].required)

    def test_no_user_behaves_like_admin(self):
        self.assertFalse(SalesOrderForm().restrict_customers)

    def test_clerk_must_pick_employee_first(self):
        form = SalesOrderForm(user=self.clerk)
        self.assertTrue(form.restrict_customers)
        self.assertTrue(form.fields['sales_employee'].required)
        self.assertEqual(list(form.fields)[:2], ['sales_employee', 'customer'])
        self.assertNotIn(self.unassigned, form.fields['customer'].queryset)
        self.assertNotIn(self.vendor, form.fields['customer'].queryset)

    def test_linked_employee_sees_only_own_customers(self):
        form = SalesOrderForm(user=self.emp_user)
        self.assertEqual(list(form.fields['sales_employee'].queryset), [self.emp1])
        self.assertEqual(set(form.fields['customer'].queryset), {self.c1a, self.c1b})
        self.assertEqual(form.initial['sales_employee'], self.emp1.pk)

    def test_clerk_matching_pair_valid(self):
        form = SalesOrderForm(self.order_post_data(self.c2, self.emp2), user=self.clerk)
        self.assertTrue(form.is_valid(), form.errors)

    def test_clerk_mismatched_pair_invalid(self):
        form = SalesOrderForm(self.order_post_data(self.c2, self.emp1), user=self.clerk)
        self.assertFalse(form.is_valid())
        self.assertIn('not assigned to', str(form.errors['customer']))

    def test_clerk_unassigned_customer_invalid(self):
        form = SalesOrderForm(self.order_post_data(self.unassigned, self.emp1), user=self.clerk)
        self.assertFalse(form.is_valid())
        self.assertIn('customer', form.errors)

    def test_clerk_without_employee_invalid(self):
        form = SalesOrderForm(self.order_post_data(self.c1a, None), user=self.clerk)
        self.assertFalse(form.is_valid())
        self.assertIn('sales_employee', form.errors)

    def test_admin_any_pair_valid(self):
        for customer, employee in [(self.c2, self.emp1), (self.unassigned, None), (self.unassigned, self.emp2)]:
            form = SalesOrderForm(self.order_post_data(customer, employee), user=self.admin)
            self.assertTrue(form.is_valid(), form.errors)

    def test_linked_employee_cannot_use_other_employee(self):
        form = SalesOrderForm(self.order_post_data(self.c2, self.emp2), user=self.emp_user)
        self.assertFalse(form.is_valid())


class SalesOrderViewFlowTests(CustomerEmployeeAssignmentBase):

    def create_url(self):
        return reverse('customer_vendor:sales_order_create')

    def test_create_page_renders_for_clerk_with_restriction(self):
        self.client.force_login(self.clerk)
        resp = self.client.get(self.create_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-restrict-customers="true"')
        self.assertContains(resp, f'data-employee="{self.emp1.pk}"')
        self.assertContains(resp, f'data-employee="{self.emp2.pk}"')
        self.assertNotContains(resp, 'Delta Shop')

    def test_create_page_renders_for_admin_without_restriction(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.create_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-restrict-customers="false"')
        self.assertContains(resp, 'Delta Shop')

    def test_create_page_for_linked_employee_preselects_self(self):
        self.client.force_login(self.emp_user)
        resp = self.client.get(self.create_url())
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Gamma Mart')
        self.assertContains(resp, 'Alpha Traders')
        self.assertRegex(resp.content.decode(), rf'<option value="{self.emp1.pk}"\s+selected')

    def test_clerk_creates_draft_order_without_moving_stock(self):
        self.client.force_login(self.clerk)
        resp = self.client.post(self.create_url(), self.order_post_data(self.c1a, self.emp1, quantity=3))
        self.assertEqual(resp.status_code, 302)
        order = SalesOrder.objects.get()
        self.assertEqual((order.customer, order.sales_employee, order.owner), (self.c1a, self.emp1, self.clerk))
        self.assertEqual(order.total_amount, Decimal('300.00'))
        self.assertEqual(order.status, 'draft')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 50)

    def test_clerk_creates_and_confirms_order_moves_stock_and_creates_invoice(self):
        self.client.force_login(self.clerk)
        data = self.order_post_data(self.c1a, self.emp1, quantity=3)
        data['action'] = 'confirm'
        resp = self.client.post(self.create_url(), data)
        self.assertEqual(resp.status_code, 302)
        order = SalesOrder.objects.get()
        self.assertEqual(order.status, 'confirmed')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 47)
        invoice = order.invoice
        self.assertEqual(invoice.total_amount, Decimal('300.00'))
        self.assertEqual(invoice.customer, self.c1a)
        self.assertEqual(invoice.sales_employee, self.emp1)

    def test_clerk_mismatch_creates_nothing(self):
        self.client.force_login(self.clerk)
        resp = self.client.post(self.create_url(), self.order_post_data(self.c2, self.emp1))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SalesOrder.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 50)

    def test_linked_employee_creates_order_for_own_customer(self):
        self.client.force_login(self.emp_user)
        resp = self.client.post(self.create_url(), self.order_post_data(self.c1b, self.emp1))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SalesOrder.objects.get().customer, self.c1b)

    def test_linked_employee_cannot_order_for_other_customer(self):
        self.client.force_login(self.emp_user)
        resp = self.client.post(self.create_url(), self.order_post_data(self.c2, self.emp1))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SalesOrder.objects.count(), 0)

    def test_admin_creates_order_for_any_customer(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.create_url(), self.order_post_data(self.unassigned, None))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SalesOrder.objects.get().customer, self.unassigned)

    def make_order(self, customer, employee):
        order = SalesOrder.objects.create(customer=customer, sales_employee=employee, owner=self.admin)
        item = SalesOrderItem.objects.create(
            sales_order=order, product=self.product, unit_price=Decimal('100.00'), quantity=2,
        )
        self.product.refresh_from_db()
        return order, item

    def test_edit_keeps_customer_after_reassignment(self):
        order, item = self.make_order(self.c2, self.emp2)
        self.c2.sales_employee = self.emp1
        self.c2.save()

        self.client.force_login(self.clerk)
        resp = self.client.post(
            reverse('customer_vendor:sales_order_update', args=[order.pk]),
            self.order_post_data(self.c2, self.emp2, quantity=4, item_id=item.pk, initial_forms=1),
        )
        self.assertEqual(resp.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 4)

    def test_edit_cannot_switch_to_other_employees_customer(self):
        order, item = self.make_order(self.c1a, self.emp1)
        self.client.force_login(self.clerk)
        resp = self.client.post(
            reverse('customer_vendor:sales_order_update', args=[order.pk]),
            self.order_post_data(self.c2, self.emp1, item_id=item.pk, initial_forms=1),
        )
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.customer, self.c1a)

    def test_detail_view_still_renders(self):
        order, _ = self.make_order(self.c1a, self.emp1)
        self.client.force_login(self.clerk)
        resp = self.client.get(reverse('customer_vendor:sales_order_view', args=[order.pk]))
        self.assertEqual(resp.status_code, 200)


class SalesEmployeeCustomerPageTests(CustomerEmployeeAssignmentBase):

    def test_employee_customer_page_lists_only_own_customers(self):
        self.client.force_login(self.clerk)
        resp = self.client.get(reverse('customer_vendor:sales_employee_customers', args=[self.emp1.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.context['customers']), {self.c1a, self.c1b})
        self.assertContains(resp, 'Add Customer')

    def test_add_customer_button_prefills_employee_and_returns_to_employee_page(self):
        self.client.force_login(self.clerk)
        create_url = reverse('customer_vendor:customer_create') + f'?sales_employee={self.emp2.pk}'

        resp = self.client.get(create_url)
        self.assertEqual(resp.status_code, 200)
        self.assertRegex(resp.content.decode(), rf'<option value="{self.emp2.pk}"\s+selected')

        resp = self.client.post(create_url, {
            'name': 'From Employee Page', 'phone_number': '01733333333', 'business_type': 'retailer',
            'entity_type': 'customer',
        })
        self.assertRedirects(resp, reverse('customer_vendor:sales_employee_customers', args=[self.emp2.pk]))
        self.assertEqual(CustomerVendor.objects.get(name='From Employee Page').sales_employee, self.emp2)

    def test_plain_add_customer_still_goes_to_customer_list(self):
        self.client.force_login(self.clerk)
        resp = self.client.post(reverse('customer_vendor:customer_create'), {
            'name': 'Plain Add', 'phone_number': '01744444444', 'business_type': 'retailer',
            'entity_type': 'customer',
        })
        self.assertRedirects(resp, reverse('customer_vendor:customer_list'))

    def test_employee_customers_page_requires_permission(self):
        no_perm_user = User.objects.create_user('noperm', password='pass')
        self.client.force_login(no_perm_user)
        resp = self.client.get(reverse('customer_vendor:sales_employee_customers', args=[self.emp1.pk]))
        self.assertEqual(resp.status_code, 302)


class SalesEmployeeInlineCustomerManagementTests(CustomerEmployeeAssignmentBase):
    """The customer table + add/remove popup embedded on the employee page."""

    def edit_url(self, employee):
        return reverse('customer_vendor:sales_employee_edit', args=[employee.pk])

    def assign_url(self, employee):
        return reverse('customer_vendor:sales_employee_assign_customer', args=[employee.pk])

    def remove_url(self, employee, customer):
        return reverse('customer_vendor:sales_employee_remove_customer', args=[employee.pk, customer.pk])

    def test_edit_page_lists_assigned_customers_and_add_button(self):
        self.client.force_login(self.clerk)
        resp = self.client.get(self.edit_url(self.emp1))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Alpha Traders')
        self.assertContains(resp, 'Beta Store')
        self.assertNotContains(resp, 'Gamma Mart')
        self.assertContains(resp, 'Add Customer')
        self.assertContains(resp, 'id="addCustomerModal"')

    def test_create_page_shows_placeholder_instead_of_table(self):
        self.client.force_login(self.clerk)
        resp = self.client.get(reverse('customer_vendor:sales_employee_create'))
        self.assertContains(resp, 'Save this sales employee first')
        self.assertNotContains(resp, 'id="addCustomerModal"')

    def test_popup_only_offers_fully_unassigned_customers(self):
        self.client.force_login(self.clerk)
        resp = self.client.get(self.edit_url(self.emp1))
        available = set(resp.context['available_customers'])
        self.assertEqual(available, {self.unassigned})

    def test_assign_customer_success(self):
        self.client.force_login(self.clerk)
        resp = self.client.post(self.assign_url(self.emp1), {'customer': str(self.unassigned.pk), 'next': self.edit_url(self.emp1)})
        self.assertRedirects(resp, self.edit_url(self.emp1))
        self.unassigned.refresh_from_db()
        self.assertEqual(self.unassigned.sales_employee, self.emp1)

    def test_assign_customer_already_assigned_elsewhere_is_rejected(self):
        self.client.force_login(self.clerk)
        resp = self.client.post(self.assign_url(self.emp1), {'customer': str(self.c2.pk), 'next': self.edit_url(self.emp1)})
        self.assertEqual(resp.status_code, 302)
        self.c2.refresh_from_db()
        self.assertEqual(self.c2.sales_employee, self.emp2)

    def test_assign_customer_without_selection_is_a_noop(self):
        self.client.force_login(self.clerk)
        resp = self.client.post(self.assign_url(self.emp1), {'next': self.edit_url(self.emp1)}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'select a customer')
        self.assertEqual(set(self.emp1.customers.all()), {self.c1a, self.c1b})

    def test_assign_customer_requires_permission(self):
        viewer = User.objects.create_user('viewer', password='pass')
        viewer.user_permissions.add(*Permission.objects.filter(codename__in=['view_customervendor']))
        self.client.force_login(viewer)
        resp = self.client.post(self.assign_url(self.emp1), {'customer': str(self.unassigned.pk)})
        self.assertEqual(resp.status_code, 302)
        self.unassigned.refresh_from_db()
        self.assertIsNone(self.unassigned.sales_employee)

    def test_assign_customer_get_not_allowed(self):
        self.client.force_login(self.clerk)
        resp = self.client.get(self.assign_url(self.emp1))
        self.assertEqual(resp.status_code, 405)

    def test_remove_customer_success(self):
        self.client.force_login(self.clerk)
        resp = self.client.post(self.remove_url(self.emp1, self.c1a), {'next': self.edit_url(self.emp1)})
        self.assertRedirects(resp, self.edit_url(self.emp1))
        self.c1a.refresh_from_db()
        self.assertIsNone(self.c1a.sales_employee)

    def test_remove_customer_not_assigned_to_this_employee_is_rejected(self):
        self.client.force_login(self.clerk)
        resp = self.client.post(self.remove_url(self.emp1, self.c2), {'next': self.edit_url(self.emp1)})
        self.assertEqual(resp.status_code, 302)
        self.c2.refresh_from_db()
        self.assertEqual(self.c2.sales_employee, self.emp2)

    def test_remove_customer_requires_permission(self):
        viewer = User.objects.create_user('viewer2', password='pass')
        viewer.user_permissions.add(*Permission.objects.filter(codename__in=['view_customervendor']))
        self.client.force_login(viewer)
        resp = self.client.post(self.remove_url(self.emp1, self.c1a))
        self.assertEqual(resp.status_code, 302)
        self.c1a.refresh_from_db()
        self.assertEqual(self.c1a.sales_employee, self.emp1)

    def test_view_only_user_sees_table_without_manage_controls(self):
        view_only = User.objects.create_user('vieweronly', password='pass')
        view_only.user_permissions.add(*Permission.objects.filter(codename__in=['view_salesemployee', 'view_customervendor']))
        self.client.force_login(view_only)
        resp = self.client.get(reverse('customer_vendor:sales_employee_view', args=[self.emp1.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Alpha Traders')
        self.assertNotContains(resp, 'Add Customer')
        self.assertNotContains(resp, 'id="addCustomerModal"')


class AutoInvoiceOnConfirmTests(CustomerEmployeeAssignmentBase):

    def make_draft_order(self, quantity=2, unit_price=Decimal('100.00'), discount=Decimal('0'), shipping=Decimal('0')):
        order = SalesOrder.objects.create(
            customer=self.c1a, sales_employee=self.emp1, owner=self.admin,
            discount=discount, shipping=shipping,
        )
        SalesOrderItem.objects.create(
            sales_order=order, product=self.product, unit_price=unit_price, quantity=quantity,
        )
        return order

    def test_confirm_creates_invoice_with_matching_total(self):
        order = self.make_draft_order(quantity=3, discount=Decimal('10'), shipping=Decimal('20'))
        confirm_sales_order(order)

        order.refresh_from_db()
        self.assertEqual(order.status, 'confirmed')
        self.assertEqual(ARInvoice.objects.count(), 1)

        invoice = order.invoice
        self.assertEqual(invoice.customer, order.customer)
        self.assertEqual(invoice.sales_employee, order.sales_employee)
        self.assertEqual(invoice.discount, Decimal('10'))
        self.assertEqual(invoice.shipping, Decimal('20'))
        self.assertEqual(invoice.total_amount, order.total_amount)
        self.assertEqual(invoice.invoice_items.count(), 1)
        self.assertEqual(invoice.due_date.date(), order.order_date + timedelta(days=30))

    def test_confirm_invoice_date_is_a_plain_date_not_a_timestamp(self):
        order = self.make_draft_order()
        confirm_sales_order(order)

        invoice = ARInvoice.objects.get(sales_order=order)
        self.assertEqual(type(invoice.invoice_date), date)
        self.assertEqual(invoice.invoice_date.isoformat(), invoice.invoice_date.isoformat()[:10])

    def test_confirm_blocked_by_insufficient_stock_creates_no_invoice(self):
        order = self.make_draft_order(quantity=1000)
        with self.assertRaises(ValidationError):
            confirm_sales_order(order)

        order.refresh_from_db()
        self.assertEqual(order.status, 'draft')
        self.assertEqual(ARInvoice.objects.count(), 0)

    def test_confirm_is_idempotent_about_invoice_creation(self):
        order = self.make_draft_order()
        confirm_sales_order(order)
        first_invoice_id = order.invoice.pk

        from uniworlderp.views.sales_order_views import create_invoice_from_order
        create_invoice_from_order(order)
        self.assertEqual(ARInvoice.objects.count(), 1)
        order.refresh_from_db()
        self.assertEqual(order.invoice.pk, first_invoice_id)

    def test_cancel_confirmed_order_removes_invoice_and_returns_stock(self):
        order = self.make_draft_order(quantity=4)
        confirm_sales_order(order)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 46)
        self.assertTrue(ARInvoice.objects.filter(sales_order=order).exists())

        self.client.force_login(self.admin)
        resp = self.client.post(reverse('customer_vendor:sales_order_cancel', args=[order.pk]))
        self.assertEqual(resp.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertFalse(ARInvoice.objects.filter(sales_order=order).exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 50)

    def test_invoice_list_hides_edit_delete_for_invoice_from_order(self):
        order = self.make_draft_order()
        confirm_sales_order(order)
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:invoice_list'))
        self.assertNotContains(resp, reverse('customer_vendor:invoice_update', args=[order.invoice.pk]))
        self.assertNotContains(resp, reverse('customer_vendor:invoice_delete', args=[order.invoice.pk]))

    def test_invoice_notes_can_be_saved_on_locked_invoice(self):
        order = self.make_draft_order()
        confirm_sales_order(order)
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('customer_vendor:invoice_update_notes', args=[order.invoice.pk]),
            {'notes': 'Customer asked for delivery on Friday.'},
        )
        self.assertRedirects(resp, reverse('customer_vendor:invoice_view', args=[order.invoice.pk]))
        order.invoice.refresh_from_db()
        self.assertEqual(order.invoice.notes, 'Customer asked for delivery on Friday.')

    def test_invoice_list_status_badge_uses_model_choice_codes(self):
        order = self.make_draft_order()
        confirm_sales_order(order)
        self.assertEqual(order.invoice.payment_status, 'P')

        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:invoice_list'))
        self.assertContains(resp, 'Pending')
        self.assertRegex(resp.content.decode(), r'bg-red-800 text-red-100\s*">\s*Pending')

        order.invoice.payment_status = 'C'
        order.invoice.save(update_fields=['payment_status'])
        resp = self.client.get(reverse('customer_vendor:invoice_list'))
        self.assertRegex(resp.content.decode(), r'bg-green-800 text-green-100\s*">\s*Paid')

    def test_invoice_list_filters_by_customer_employee_status_and_date(self):
        order1 = self.make_draft_order()
        confirm_sales_order(order1)

        order2 = SalesOrder.objects.create(customer=self.c2, sales_employee=self.emp2, owner=self.admin)
        SalesOrderItem.objects.create(sales_order=order2, product=self.product, unit_price=Decimal('50.00'), quantity=1)
        confirm_sales_order(order2)

        self.client.force_login(self.admin)
        url = reverse('customer_vendor:invoice_list')

        resp = self.client.get(url, {'customer': self.c1a.pk})
        self.assertEqual(set(resp.context['invoices']), {order1.invoice})

        resp = self.client.get(url, {'sales_employee': self.emp2.pk})
        self.assertEqual(set(resp.context['invoices']), {order2.invoice})

        resp = self.client.get(url, {'payment_status': 'P'})
        self.assertEqual(set(resp.context['invoices']), {order1.invoice, order2.invoice})

        resp = self.client.get(url, {'payment_status': 'C'})
        self.assertEqual(set(resp.context['invoices']), set())

        today = ARInvoice.objects.get(sales_order=order1).invoice_date
        resp = self.client.get(url, {'start_date': today.isoformat(), 'end_date': today.isoformat()})
        self.assertEqual(set(resp.context['invoices']), {order1.invoice, order2.invoice})

        resp = self.client.get(url)
        self.assertContains(resp, 'All Payment Statuses')
        self.assertContains(resp, 'All Customers')
        self.assertContains(resp, 'All Sales Employees')


class PaymentTests(CustomerEmployeeAssignmentBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin.user_permissions.add(*Permission.objects.filter(codename__in=[
            'add_payment', 'change_payment', 'delete_payment', 'view_payment',
        ]))
        cls.cash, _ = PaymentMethod.objects.get_or_create(name='Cash', defaults={'kind': 'cash'})
        cls.bank = PaymentMethod.objects.create(name='City Bank A/C 1234', kind='bank')

    def make_confirmed_invoice(self, quantity=2, unit_price=Decimal('100.00')):
        order = SalesOrder.objects.create(customer=self.c1a, sales_employee=self.emp1, owner=self.admin)
        SalesOrderItem.objects.create(sales_order=order, product=self.product, unit_price=unit_price, quantity=quantity)
        confirm_sales_order(order)
        order.refresh_from_db()
        return order.invoice

    def test_full_payment_marks_invoice_paid(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('customer_vendor:payment_create', args=[invoice.pk]), {
            'payment_date': '2026-09-24', 'amount': '200.00', 'method': self.cash.pk,
            'received_by': 'Atiq',
        })
        self.assertRedirects(resp, reverse('customer_vendor:invoice_view', args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, 'C')
        self.assertEqual(invoice.paid_amount, Decimal('200.00'))
        self.assertEqual(invoice.balance_due, Decimal('0.00'))

    def test_partial_payment_marks_invoice_partial(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        self.client.post(reverse('customer_vendor:payment_create', args=[invoice.pk]), {
            'payment_date': '2026-09-24', 'amount': '50.00', 'method': self.cash.pk,
        })
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, 'PA')
        self.assertEqual(invoice.balance_due, Decimal('150.00'))

    def test_multiple_partial_payments_accumulate_to_paid(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        self.client.post(reverse('customer_vendor:payment_create', args=[invoice.pk]), {
            'payment_date': '2026-09-24', 'amount': '120.00', 'method': self.cash.pk,
        })
        self.client.post(reverse('customer_vendor:payment_create', args=[invoice.pk]), {
            'payment_date': '2026-09-25', 'amount': '80.00', 'method': self.bank.pk,
            'bank_name': 'City Bank', 'account_number': '1234',
        })
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, 'C')
        self.assertEqual(Payment.objects.filter(invoice=invoice).count(), 2)

    def test_overpayment_is_rejected(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('customer_vendor:payment_create', args=[invoice.pk]), {
            'payment_date': '2026-09-24', 'amount': '999.00', 'method': self.cash.pk,
        })
        self.assertRedirects(resp, reverse('customer_vendor:invoice_view', args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(Payment.objects.filter(invoice=invoice).count(), 0)
        self.assertEqual(invoice.payment_status, 'P')

    def test_zero_or_negative_amount_rejected(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('customer_vendor:payment_create', args=[invoice.pk]), {
            'payment_date': '2026-09-24', 'amount': '0', 'method': self.cash.pk,
        })
        self.assertEqual(Payment.objects.filter(invoice=invoice).count(), 0)

    def test_second_payment_capped_at_remaining_balance(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        self.client.post(reverse('customer_vendor:payment_create', args=[invoice.pk]), {
            'payment_date': '2026-09-24', 'amount': '150.00', 'method': self.cash.pk,
        })
        resp = self.client.post(reverse('customer_vendor:payment_create', args=[invoice.pk]), {
            'payment_date': '2026-09-24', 'amount': '100.00', 'method': self.cash.pk,
        })
        self.assertRedirects(resp, reverse('customer_vendor:invoice_view', args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal('150.00'))

    def test_deleting_payment_recomputes_status_back_to_pending(self):
        invoice = self.make_confirmed_invoice()
        payment = Payment.objects.create(
            invoice=invoice, customer=invoice.customer, sales_employee=invoice.sales_employee,
            amount=Decimal('200.00'), method=self.cash, created_by=self.admin,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, 'C')

        self.client.force_login(self.admin)
        resp = self.client.post(reverse('customer_vendor:payment_delete', args=[payment.pk]))
        self.assertRedirects(resp, reverse('customer_vendor:invoice_view', args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_status, 'P')
        self.assertEqual(invoice.paid_amount, Decimal('0.00'))

    def test_payment_requires_permission(self):
        invoice = self.make_confirmed_invoice()
        no_perm_user = User.objects.create_user('nopay', password='pass')
        self.client.force_login(no_perm_user)
        resp = self.client.post(reverse('customer_vendor:payment_create', args=[invoice.pk]), {
            'payment_date': '2026-09-24', 'amount': '50.00', 'method': self.cash.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Payment.objects.filter(invoice=invoice).count(), 0)

    def test_invoice_page_shows_add_payment_and_balance(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:invoice_view', args=[invoice.pk]))
        self.assertContains(resp, 'Add Payment')
        self.assertContains(resp, 'Balance Due')

    def test_add_payment_form_defaults_method_to_cash(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:invoice_view', args=[invoice.pk]))
        self.assertRegex(resp.content.decode(), r'<option value="' + str(self.cash.pk) + r'" selected>Cash</option>')

    def test_add_payment_form_received_by_lists_sales_employees(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:invoice_view', args=[invoice.pk]))
        content = resp.content.decode()
        self.assertIn('Atiq', content)
        self.assertIn('Jabed', content)
        self.assertIn('Other / Admin', content)

    def test_invoice_page_links_back_to_sales_order(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:invoice_view', args=[invoice.pk]))
        self.assertContains(resp, reverse('customer_vendor:sales_order_view', args=[invoice.sales_order_id]))

    def test_sales_order_page_links_to_invoice(self):
        invoice = self.make_confirmed_invoice()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:sales_order_view', args=[invoice.sales_order_id]))
        self.assertContains(resp, reverse('customer_vendor:invoice_view', args=[invoice.pk]))

    def test_payment_list_shows_recorded_payments_and_links(self):
        invoice = self.make_confirmed_invoice()
        Payment.objects.create(
            invoice=invoice, customer=invoice.customer, sales_employee=invoice.sales_employee,
            amount=Decimal('75.00'), method=self.cash, created_by=self.admin,
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:payment_list'))
        self.assertContains(resp, '75.00')
        self.assertContains(resp, reverse('customer_vendor:invoice_view', args=[invoice.pk]))

    def test_payment_list_filters_by_customer_and_method(self):
        invoice1 = self.make_confirmed_invoice()
        order2 = SalesOrder.objects.create(customer=self.c2, sales_employee=self.emp2, owner=self.admin)
        SalesOrderItem.objects.create(sales_order=order2, product=self.product, unit_price=Decimal('50.00'), quantity=1)
        confirm_sales_order(order2)
        order2.refresh_from_db()
        invoice2 = order2.invoice

        p1 = Payment.objects.create(
            invoice=invoice1, customer=invoice1.customer, sales_employee=invoice1.sales_employee,
            amount=Decimal('50.00'), method=self.cash, created_by=self.admin,
        )
        p2 = Payment.objects.create(
            invoice=invoice2, customer=invoice2.customer, sales_employee=invoice2.sales_employee,
            amount=Decimal('30.00'), method=self.bank, created_by=self.admin,
        )

        self.client.force_login(self.admin)
        url = reverse('customer_vendor:payment_list')

        resp = self.client.get(url, {'customer': self.c1a.pk})
        self.assertEqual(set(resp.context['payments']), {p1})

        resp = self.client.get(url, {'method': self.bank.pk})
        self.assertEqual(set(resp.context['payments']), {p2})

    def test_payment_list_shows_daily_collection_totals(self):
        invoice1 = self.make_confirmed_invoice()
        order2 = SalesOrder.objects.create(customer=self.c2, sales_employee=self.emp2, owner=self.admin)
        SalesOrderItem.objects.create(sales_order=order2, product=self.product, unit_price=Decimal('50.00'), quantity=1)
        confirm_sales_order(order2)
        order2.refresh_from_db()
        invoice2 = order2.invoice

        Payment.objects.create(
            invoice=invoice1, customer=invoice1.customer, sales_employee=invoice1.sales_employee,
            amount=Decimal('50.00'), method=self.cash, created_by=self.admin, payment_date='2026-09-20',
        )
        Payment.objects.create(
            invoice=invoice1, customer=invoice1.customer, sales_employee=invoice1.sales_employee,
            amount=Decimal('20.00'), method=self.cash, created_by=self.admin, payment_date='2026-09-20',
        )
        Payment.objects.create(
            invoice=invoice2, customer=invoice2.customer, sales_employee=invoice2.sales_employee,
            amount=Decimal('30.00'), method=self.bank, created_by=self.admin, payment_date='2026-09-21',
        )

        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:payment_list'))
        daily = {row['payment_date'].isoformat(): (row['total'], row['count']) for row in resp.context['daily_totals']}
        self.assertEqual(daily['2026-09-20'], (Decimal('70.00'), 2))
        self.assertEqual(daily['2026-09-21'], (Decimal('30.00'), 1))

    def test_payment_model_clean_rejects_overpayment_directly(self):
        invoice = self.make_confirmed_invoice()
        payment = Payment(
            invoice=invoice, customer=invoice.customer, amount=Decimal('999.00'),
            method=self.cash, created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            payment.save()

    def test_receipt_page_renders(self):
        invoice = self.make_confirmed_invoice()
        payment = Payment.objects.create(
            invoice=invoice, customer=invoice.customer, sales_employee=invoice.sales_employee,
            amount=Decimal('200.00'), method=self.cash, created_by=self.admin, received_by='Atiq',
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:payment_view', args=[payment.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'PAYMENT RECEIPT')


class LedgerTests(CustomerEmployeeAssignmentBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cash, _ = PaymentMethod.objects.get_or_create(name='Cash', defaults={'kind': 'cash'})

    def make_confirmed_invoice(self, customer, employee, quantity=2, unit_price=Decimal('100.00'), order_date=None):
        order = SalesOrder.objects.create(customer=customer, sales_employee=employee, owner=self.admin)
        if order_date:
            SalesOrder.objects.filter(pk=order.pk).update(order_date=order_date)
            order.refresh_from_db()
        SalesOrderItem.objects.create(sales_order=order, product=self.product, unit_price=unit_price, quantity=quantity)
        confirm_sales_order(order)
        order.refresh_from_db()
        invoice = order.invoice
        if order_date:
            ARInvoice.objects.filter(pk=invoice.pk).update(invoice_date=order_date)
            invoice.refresh_from_db()
        return invoice

    def test_ledger_requires_login(self):
        resp = self.client.get(reverse('customer_vendor:ledger'))
        self.assertEqual(resp.status_code, 302)

    def test_ledger_without_customer_shows_picker(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:ledger'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Select a customer')

    def test_ledger_running_balance_matches_invoices_and_payments(self):
        invoice = self.make_confirmed_invoice(self.c1a, self.emp1, quantity=2, unit_price=Decimal('100.00'), order_date=date(2026, 1, 5))
        Payment.objects.create(
            invoice=invoice, customer=invoice.customer, sales_employee=invoice.sales_employee,
            amount=Decimal('50.00'), method=self.cash, created_by=self.admin, payment_date=date(2026, 1, 10),
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:ledger'), {
            'customer': str(self.c1a.pk), 'preset': 'range',
            'start_date': '2026-01-01', 'end_date': '2026-01-31',
        })
        self.assertEqual(resp.status_code, 200)
        ledger = resp.context['ledger']
        self.assertEqual(ledger['brought_forward'], Decimal('0.00'))
        self.assertEqual(len(ledger['rows']), 2)
        self.assertEqual(ledger['rows'][0]['type'], 'Invoice')
        self.assertEqual(ledger['rows'][0]['balance'], Decimal('200.00'))
        self.assertEqual(ledger['rows'][1]['balance'], Decimal('150.00'))
        self.assertEqual(ledger['closing_balance'], Decimal('150.00'))

    def test_ledger_brought_forward_rolls_up_earlier_transactions(self):
        invoice = self.make_confirmed_invoice(self.c1a, self.emp1, quantity=1, unit_price=Decimal('100.00'), order_date=date(2026, 1, 5))
        Payment.objects.create(
            invoice=invoice, customer=invoice.customer, sales_employee=invoice.sales_employee,
            amount=Decimal('40.00'), method=self.cash, created_by=self.admin, payment_date=date(2026, 1, 6),
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:ledger'), {
            'customer': str(self.c1a.pk), 'preset': 'range',
            'start_date': '2026-02-01', 'end_date': '2026-02-28',
        })
        ledger = resp.context['ledger']
        self.assertEqual(ledger['brought_forward'], Decimal('60.00'))
        self.assertEqual(len(ledger['rows']), 0)
        self.assertEqual(ledger['closing_balance'], Decimal('60.00'))

    def test_ledger_drill_down_link_from_customer_list(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('customer_vendor:customer_list'))
        self.assertContains(resp, f"/erp/ledger/?customer={self.c1a.pk}")

    def test_ledger_print_and_excel_for_customer(self):
        invoice = self.make_confirmed_invoice(self.c1a, self.emp1)
        self.client.force_login(self.admin)
        print_resp = self.client.get(reverse('customer_vendor:ledger_print'), {'customer': str(self.c1a.pk)})
        self.assertEqual(print_resp.status_code, 200)
        excel_resp = self.client.get(reverse('customer_vendor:ledger_excel'), {'customer': str(self.c1a.pk)})
        self.assertEqual(excel_resp.status_code, 200)
        self.assertEqual(excel_resp['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_reports_page_includes_finance_tabs(self):
        self.make_confirmed_invoice(self.c1a, self.emp1, order_date=date(2026, 1, 5))
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('customer_vendor:sales_report'), {
            'start_date': '2020-01-01', 'end_date': '2026-12-31',
        })
        self.assertEqual(resp.status_code, 200)
        for marker in ('Collections', 'Finance Summary'):
            self.assertContains(resp, marker)

    def test_finance_summary_tab_shows_per_customer_outstanding(self):
        invoice = self.make_confirmed_invoice(self.c1a, self.emp1, order_date=date(2026, 1, 5))
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('customer_vendor:sales_report'), {
            'start_date': '2020-01-01', 'end_date': '2026-12-31',
        })
        summary = {row['customer']: row for row in resp.context['finance_customer_summary']}
        self.assertIn(self.c1a.name, summary)
        row = summary[self.c1a.name]
        self.assertEqual(row['invoiced'], invoice.total_amount)
        self.assertEqual(row['outstanding'], invoice.balance_due)

    def test_finance_report_print_and_excel(self):
        self.make_confirmed_invoice(self.c1a, self.emp1, order_date=date(2026, 1, 5))
        self.client.force_login(self.admin)
        print_resp = self.client.post(reverse('customer_vendor:finance_report_print'), {
            'start_date': '2020-01-01', 'end_date': '2026-12-31',
        })
        self.assertEqual(print_resp.status_code, 200)
        excel_resp = self.client.post(reverse('customer_vendor:finance_report_excel'), {
            'start_date': '2020-01-01', 'end_date': '2026-12-31',
        })
        self.assertEqual(excel_resp.status_code, 200)
        self.assertEqual(excel_resp['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
