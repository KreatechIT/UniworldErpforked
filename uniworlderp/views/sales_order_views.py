


from django import forms
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from uniworlderp import models
from .common_imports import *
from uniworlderp.models import ReturnSales, ReturnSalesItem, SalesOrder, SalesOrderItem, Product,StockTransaction,SalesEmployee,CustomerVendor, ARInvoice, ARInvoiceItem
from uniworlderp.forms import ReturnSalesForm, ReturnSalesItemFormSet, SalesOrderForm, SalesOrderItemFormSet, get_return_sales_item_formset
from company.models import Company, Branch, ContactPerson


def _safe_redirect_back(request, fallback_url):
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(next_url)
    return redirect(fallback_url)


def get_stock_shortages(order):
    needed = {}
    for item in order.order_items.select_related('product'):
        needed[item.product_id] = needed.get(item.product_id, 0) + item.quantity

    shortages = []
    for product in Product.objects.filter(id__in=needed.keys()):
        qty_needed = needed[product.id]
        if product.stock_quantity < qty_needed:
            shortages.append(
                f"{product.name}: only {product.stock_quantity} in stock, but {qty_needed} requested"
            )
    return shortages


@transaction.atomic
def confirm_sales_order(order):
    shortages = get_stock_shortages(order)
    if shortages:
        raise ValidationError("Can't confirm - not enough stock: " + "; ".join(shortages))

    for item in order.order_items.all():
        StockTransaction.objects.create(
            product=item.product,
            transaction_type='OUT',
            quantity=item.quantity,
            reference=f"SO-{order.id}",
            owner=order.owner,
        )

    order.status = 'confirmed'
    order.save(update_fields=['status'])

    create_invoice_from_order(order)


def create_invoice_from_order(order):
    if ARInvoice.objects.filter(sales_order=order).exists():
        return

    order_date = order.order_date
    if hasattr(order_date, 'date'):
        order_date = order_date.date()

    invoice = ARInvoice.objects.create(
        customer=order.customer,
        sales_employee=order.sales_employee,
        sales_order=order,
        invoice_date=order_date,
        due_date=order_date + timedelta(days=30),
        discount=order.discount,
        shipping=order.shipping,
        owner=order.owner,
    )

    for item in order.order_items.all():
        effective_unit_price = (item.total / item.quantity) if item.quantity else item.unit_price
        ARInvoiceItem.objects.create(
            ar_invoice=invoice,
            product=item.product,
            unit_price=effective_unit_price,
            quantity=item.quantity,
        )

    invoice.save()
    return invoice

class SalesOrderListView(ListView):
    model = SalesOrder
    template_name = 'sales_order/list.html'
    context_object_name = 'sales_orders'
    paginate_by = 20

    def get_queryset(self):
        self.is_admin = self.request.user.is_superuser
        self.linked_employee = getattr(self.request.user, 'sales_employee', None) if not self.is_admin else None

        search_query = self.request.GET.get('search', '')
        status = self.request.GET.get('status', '')
        delivery_status = self.request.GET.get('delivery_status', '')
        customer_id = self.request.GET.get('customer', '')
        sales_employee_id = self.request.GET.get('sales_employee', '')
        start_date = self.request.GET.get('start_date', '')
        end_date = self.request.GET.get('end_date', '')

        queryset = SalesOrder.objects.all().order_by('-id')

        if not self.is_admin:
            queryset = queryset.filter(sales_employee=self.linked_employee) if self.linked_employee else queryset.none()

        if search_query:
            queryset = queryset.filter(
                Q(id__icontains=search_query) |
                Q(customer__name__icontains=search_query) |
                Q(sales_employee__user__username__icontains=search_query) |
                Q(order_date__icontains=search_query)
            )
        if status:
            queryset = queryset.filter(status=status)
        if delivery_status:
            queryset = queryset.filter(delivery_status=delivery_status)
        if customer_id:
            queryset = queryset.filter(customer_id=customer_id)
        if sales_employee_id and self.is_admin:
            queryset = queryset.filter(sales_employee_id=sales_employee_id)
        if start_date:
            queryset = queryset.filter(order_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(order_date__lte=end_date)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        context['selected_status'] = self.request.GET.get('status', '')
        context['selected_delivery_status'] = self.request.GET.get('delivery_status', '')
        context['selected_customer'] = self.request.GET.get('customer', '')
        context['selected_sales_employee'] = self.request.GET.get('sales_employee', '')
        context['status_choices'] = SalesOrder.STATUS_CHOICES
        context['delivery_status_choices'] = SalesOrder.DELIVERY_STATUS_CHOICES

        if self.is_admin:
            context['customers'] = CustomerVendor.objects.filter(entity_type='customer').order_by('name')
            context['sales_employees'] = SalesEmployee.objects.order_by('full_name')
        else:
            context['customers'] = CustomerVendor.objects.filter(sales_employee=self.linked_employee).order_by('name') if self.linked_employee else CustomerVendor.objects.none()
            context['sales_employees'] = SalesEmployee.objects.filter(pk=self.linked_employee.pk) if self.linked_employee else SalesEmployee.objects.none()

        return context
class SalesOrderItemDetailedListView(ListView):
    model = SalesOrderItem
    template_name = 'sales_order/detailed_list.html'
    context_object_name = 'order_items'

    def get_queryset(self):
        queryset = SalesOrderItem.objects.select_related(
            'sales_order__customer',
            'sales_order__sales_employee',
            'sales_order__invoice',
            'product'
        ).annotate(
            total_amount=F('quantity') * F('unit_price')
        ).order_by('-sales_order__order_date', 'sales_order__id')

        search_query = self.request.GET.get('search', '')
        if search_query:
            queryset = queryset.filter(
                Q(sales_order__id__icontains=search_query) |
                Q(sales_order__customer__name__icontains=search_query) |
                Q(sales_order__sales_employee__full_name__icontains=search_query) |
                Q(product__name__icontains=search_query)
            ).distinct()

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        return context
    
class SalesOrderCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = SalesOrder
    form_class = SalesOrderForm
    template_name = 'sales_order/form.html'
    success_url = reverse_lazy('customer_vendor:sales_order_list')
    permission_required = 'uniworlderp.add_salesorder'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['formset'] = SalesOrderItemFormSet(self.request.POST)
        else:
            context['formset'] = SalesOrderItemFormSet()
        context.update(self.get_common_context())
        context['action'] = 'Add'
        context['products'] = Product.objects.all().order_by('name')
        return context

    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data()
        formset = context['formset']
        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    self.object = form.save(commit=False)
                    self.object.owner = self.request.user
                    self.object.save()
                    formset.instance = self.object
                    formset.save()
                    if self.request.POST.get('action') == 'confirm':
                        try:
                            confirm_sales_order(self.object)
                            messages.success(self.request, 'Sales Order created and confirmed successfully.')
                        except ValidationError as e:
                            messages.error(self.request, ', '.join(e.messages))
                            messages.success(self.request, 'Sales Order saved as draft instead.')
                    else:
                        messages.success(self.request, 'Sales Order saved as draft.')
                return super().form_valid(form)
            except Exception as e:
                messages.error(self.request, f'Error creating Sales Order: {str(e)}')
                return self.form_invalid(form)
        else:
            return self.form_invalid(form)

    def form_invalid(self, form):
        context = self.get_context_data()
        formset = context['formset']
        if not form.is_valid():
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(self.request, f"{field}: {error}")
        if not formset.is_valid():
            for i, form_errors in enumerate(formset.errors):
                if form_errors:
                    for field, errors in form_errors.items():
                        for error in errors:
                            messages.error(self.request, f"Item {i+1} - {field}: {error}")
        return super().form_invalid(form)

    def get_common_context(self):
        sales_orders = SalesOrder.objects.all().order_by('id')
        return {
            'model_name': self.model._meta.verbose_name.title(),
            'can_add': self.request.user.has_perm('uniworlderp.add_salesorder'),
            'can_edit': self.request.user.has_perm('uniworlderp.change_salesorder'),
            'can_view': self.request.user.has_perm('uniworlderp.view_salesorder'),
            'list_url': reverse_lazy('customer_vendor:sales_order_list'),
            'create_url': reverse_lazy('customer_vendor:sales_order_create'),
            'edit_url_name': 'customer_vendor:sales_order_update',
            'view_url_name': 'customer_vendor:sales_order_view',
            'print_url_name': 'customer_vendor:sales_order_print',
            'search_url': reverse_lazy('customer_vendor:sales_order_search'),
            'first_id': sales_orders.first().id if sales_orders.exists() else None,
            'last_id': sales_orders.last().id if sales_orders.exists() else None,
            'prev_id': None,
            'next_id': None,
            'current_id': None,
        }

class SalesOrderUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = SalesOrder
    form_class = SalesOrderForm
    template_name = 'sales_order/form.html'
    success_url = reverse_lazy('customer_vendor:sales_order_list')
    permission_required = 'uniworlderp.change_salesorder'

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.status != 'draft':
            messages.error(request, f"This order is {self.object.get_status_display().lower()} and locked. It can't be edited.")
            return redirect('customer_vendor:sales_order_view', pk=self.object.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['formset'] = SalesOrderItemFormSet(self.request.POST, instance=self.object)
        else:
            context['formset'] = SalesOrderItemFormSet(instance=self.object)
        context.update(self.get_common_context())
        context['action'] = 'Edit'
        context['products'] = Product.objects.all().order_by('name')         
        context['can_create_invoice'] = self.request.user.has_perm('uniworlderp.add_arinvoice')
        context['create_invoice_url'] = reverse('customer_vendor:invoice_create_from_sales_order', kwargs={'sales_order_id': self.object.id})
        return context

    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data()
        formset = context['formset']

        if formset.is_valid():
            self.object = form.save()
            formset.instance = self.object
            items = formset.save(commit=False)

            for item in formset.deleted_objects:
                if item.pk:
                    item.delete()

            for item in items:
                item.save()

            if self.request.POST.get('action') == 'confirm':
                try:
                    confirm_sales_order(self.object)
                    messages.success(self.request, 'Sales Order confirmed successfully.')
                except ValidationError as e:
                    messages.error(self.request, ', '.join(e.messages))
                    messages.success(self.request, 'Sales Order saved as draft instead.')
            else:
                messages.success(self.request, 'Sales Order saved as draft.')

            return super().form_valid(form)
        else:
            return self.form_invalid(form)

    def get_common_context(self):
        sales_orders = SalesOrder.objects.all().order_by('id')
        current_order = self.object
        return {
            'model_name': self.model._meta.verbose_name.title(),
            'can_add': self.request.user.has_perm('uniworlderp.add_salesorder'),
            'can_edit': self.request.user.has_perm('uniworlderp.change_salesorder'),
            'can_view': self.request.user.has_perm('uniworlderp.view_salesorder'),
            'list_url': reverse_lazy('customer_vendor:sales_order_list'),
            'create_url': reverse_lazy('customer_vendor:sales_order_create'),
            'edit_url_name': 'customer_vendor:sales_order_update',
            'view_url_name': 'customer_vendor:sales_order_view',
            'print_url_name': 'customer_vendor:sales_order_print',
            'search_url': reverse_lazy('customer_vendor:sales_order_search'),
            'first_id': sales_orders.first().id if sales_orders.exists() else None,
            'last_id': sales_orders.last().id if sales_orders.exists() else None,
            'prev_id': sales_orders.filter(id__lt=current_order.id).last().id if sales_orders.filter(id__lt=current_order.id).exists() else None,
            'next_id': sales_orders.filter(id__gt=current_order.id).first().id if sales_orders.filter(id__gt=current_order.id).exists() else None,
            'current_id': current_order.id,
        }
class SalesOrderDetailView(PermissionRequiredMixin, DetailView):
    model = SalesOrder
    template_name = 'sales_order/form.html'
    permission_required = 'uniworlderp.view_salesorder'

    def handle_no_permission(self):
        messages.error(self.request, "You do not have permission to view this sales order.")
        return redirect('customer_vendor:sales_order_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_common_context())
        context['action'] = 'View'
        context['form'] = SalesOrderForm(instance=self.object)
        context['formset'] = SalesOrderItemFormSet(instance=self.object)
        for form in context['formset']:
            for field in form.fields.values():
                field.widget.attrs['disabled'] = 'disabled'
        for field in context['form'].fields.values():
            field.widget.attrs['disabled'] = 'disabled'
        context['can_create_invoice'] = self.request.user.has_perm('uniworlderp.add_arinvoice')
        context['create_invoice_url'] = reverse('customer_vendor:invoice_create_from_sales_order', kwargs={'sales_order_id': self.object.id})
                    
        return context

    def get_common_context(self):
        sales_orders = SalesOrder.objects.all().order_by('id')
        current_order = self.object
        return {
            'model_name': self.model._meta.verbose_name.title(),
            'can_add': self.request.user.has_perm('uniworlderp.add_salesorder'),
            'can_edit': self.request.user.has_perm('uniworlderp.change_salesorder'),
            'can_view': self.request.user.has_perm('uniworlderp.view_salesorder'),
            'list_url': reverse_lazy('customer_vendor:sales_order_list'),
            'create_url': reverse_lazy('customer_vendor:sales_order_create'),
            'edit_url_name': 'customer_vendor:sales_order_update',
            'view_url_name': 'customer_vendor:sales_order_view',
            'print_url_name': 'customer_vendor:sales_order_print',
            'first_id': sales_orders.first().id if sales_orders.exists() else None,
            'last_id': sales_orders.last().id if sales_orders.exists() else None,
            'prev_id': sales_orders.filter(id__lt=current_order.id).last().id if sales_orders.filter(id__lt=current_order.id).exists() else None,
            'next_id': sales_orders.filter(id__gt=current_order.id).first().id if sales_orders.filter(id__gt=current_order.id).exists() else None,
            'current_id': current_order.id,
        }

class SalesOrderDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = SalesOrder
    template_name = 'confirm_delete.html'
    success_url = reverse_lazy('customer_vendor:sales_order_list')
    success_message = "Sales order deleted successfully!"
    permission_required = 'uniworlderp.delete_salesorder'

    def handle_no_permission(self):
        messages.error(self.request, "You do not have permission to delete this sales order.")
        return redirect('customer_vendor:sales_order_list')

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.status != 'draft':
            messages.error(request, f"This order is {self.object.get_status_display().lower()} and locked. It can't be deleted.")
            return redirect('customer_vendor:sales_order_view', pk=self.object.pk)
        return super().dispatch(request, *args, **kwargs)

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        
        for item in self.object.order_items.all():
            item.delete()

        return super(SalesOrderDeleteView, self).delete(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['model_name'] = self.model._meta.verbose_name.title()
        context['cancel_url'] = reverse_lazy('customer_vendor:sales_order_list')
        return context

@method_decorator(require_POST, name='dispatch')
class SalesOrderCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'uniworlderp.change_salesorder'

    def handle_no_permission(self):
        messages.error(self.request, "You do not have permission to cancel this sales order.")
        return redirect('customer_vendor:sales_order_list')

    @transaction.atomic
    def post(self, request, pk):
        order = get_object_or_404(SalesOrder, pk=pk)
        fallback = reverse('customer_vendor:sales_order_list')
        if order.status != 'confirmed':
            messages.error(request, "Only a confirmed order can be cancelled.")
            return _safe_redirect_back(request, fallback)

        for item in order.order_items.all():
            StockTransaction.objects.create(
                product=item.product,
                transaction_type='RET',
                quantity=item.quantity,
                reference=f"SO-{order.id}-Cancelled",
                owner=order.owner,
            )

        invoice = getattr(order, 'invoice', None)
        if invoice is not None:
            invoice.delete()

        order.status = 'cancelled'
        order.save(update_fields=['status'])
        messages.success(request, f"Sales Order #{order.id} has been cancelled, its stock returned, and its invoice removed.")
        return _safe_redirect_back(request, fallback)

@method_decorator(require_POST, name='dispatch')
class SalesOrderConfirmView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'uniworlderp.change_salesorder'

    def handle_no_permission(self):
        messages.error(self.request, "You do not have permission to confirm this sales order.")
        return redirect('customer_vendor:sales_order_list')

    @transaction.atomic
    def post(self, request, pk):
        order = get_object_or_404(SalesOrder, pk=pk)
        if order.status != 'draft':
            messages.error(request, "Only a draft order can be confirmed.")
            return redirect('customer_vendor:sales_order_view', pk=order.pk)

        try:
            confirm_sales_order(order)
            messages.success(request, f"Sales Order #{order.id} confirmed successfully.")
        except ValidationError as e:
            messages.error(request, ', '.join(e.messages))
        return redirect('customer_vendor:sales_order_view', pk=order.pk)

class SalesOrderPrintView(LoginRequiredMixin, DetailView):
    model = SalesOrder
    template_name = 'sales_order/print.html'
    context_object_name = 'sales_order'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = Company.objects.first()
        items = self.object.order_items.all()
        subtotal = sum(item.total for item in items)
        discount = self.object.discount
        shipping = self.object.shipping
        tax = subtotal * Decimal('0.0')
        total = subtotal - discount + shipping + tax

        context.update({
            'company': company,
            'items': items,
            'subtotal': subtotal,
            'discount': discount,
            'shipping': shipping,
            'tax': tax,
            'total': total,
        })
        return context


class ReturnSalesCreateView(LoginRequiredMixin,PermissionRequiredMixin, CreateView):
    model = ReturnSales
    form_class = ReturnSalesForm
    template_name = 'sales_order/return.html'
    permission_required = 'uniworlderp.add_salesorder'
    
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.sales_order_id = kwargs.get('sales_order_id')
        self.sales_order = get_object_or_404(SalesOrder, id=self.sales_order_id)

    def dispatch(self, request, *args, **kwargs):
        if self.sales_order.status != 'confirmed':
            messages.error(request, "Only a confirmed order can have a return created.")
            return redirect('customer_vendor:sales_order_view', pk=self.sales_order.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy('customer_vendor:stock_transfer_detailed_list')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['formset'] = ReturnSalesItemFormSet(self.request.POST, instance=self.object)
        else:
            initial_data = []
            for item in self.sales_order.order_items.all():
                previously_returned = ReturnSalesItem.objects.filter(
                    sales_order_item=item
                ).aggregate(total=Sum('quantity'))['total'] or 0
            
                max_returnable = item.quantity - previously_returned
            
                initial_data.append({
                    'sales_order_item': item.id,
                    'unit_price': item.unit_price,
                    'quantity': 0,
                    'sales_quantity': item.quantity,
                    'max_returnable': max_returnable,
                    'product_name': item.product.name,
                })
        
            context['formset'] = get_return_sales_item_formset(
                sales_order=self.sales_order,
                queryset=ReturnSalesItem.objects.none(),
                initial=initial_data
            )

            for form in context['formset'].forms:
                form.fields['sales_order_item'].queryset = SalesOrderItem.objects.filter(sales_order=self.sales_order)

        context['sales_order'] = self.sales_order
        context['action'] = 'Create'
        context['title'] = f'Create Return for Sales Order #{self.sales_order.id}'
        return context
        
    @transaction.atomic
    def form_valid(self, form):
        context = self.get_context_data()
        formset = context['formset']
        
        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    self.object = form.save(commit=False)
                    self.object.sales_order = self.sales_order
                    self.object.owner = self.request.user
                    self.object.save()  
                    
                    formset.instance = self.object  
                    formset_items = formset.save(commit=False)
                    
                    has_items = False
                    for item in formset_items:
                        if item.quantity > 0:
                            has_items = True
                            item.total = Decimal(item.quantity) * item.unit_price  
                            item.return_sales = self.object  
                            item.save()  
                    
                    for form in formset.deleted_forms:
                        if form.instance.pk:
                            form.instance.delete()
                    
                    if not has_items:
                        raise ValidationError("You must return at least one item.")
                    
                    self.object.update_total_amount()
                    
                    messages.success(self.request, 'Sales return created successfully.')
                    return HttpResponseRedirect(self.get_success_url())
            except ValidationError as e:
                messages.error(self.request, str(e))
                return self.form_invalid(form)
            except Exception as e:
                messages.error(self.request, f'Error creating sales return: {str(e)}')
                return self.form_invalid(form)
        else:
            return self.form_invalid(form)
    
    def form_invalid(self, form):
        context = self.get_context_data()
        formset = context['formset']
        if not form.is_valid():
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(self.request, f"{field}: {error}")
        if not formset.is_valid():
            for i, form_errors in enumerate(formset.errors):
                if form_errors:
                    for field, errors in form_errors.items():
                        for error in errors:
                            messages.error(self.request, f"Item {i+1} - {field}: {error}")
        return super().form_invalid(form)


class ReturnSalesDetailView(LoginRequiredMixin, DetailView):
    model = ReturnSales
    template_name = 'sales_order/return_view.html'
    context_object_name = 'return_sales'
    permission_required = 'uniworlderp.view_returnsales'
    
    def get_object(self, queryset=None):
        sales_order_id = self.kwargs.get('sales_order_id')
        a = get_object_or_404(ReturnSales, sales_order=sales_order_id)
        print(a)
        print(a.return_employee)
        return a
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return_sales = self.object
        context['sales_order'] = return_sales.sales_order
        context['return_items'] = ReturnSalesItem.objects.filter(return_sales=return_sales)
        
        return context

