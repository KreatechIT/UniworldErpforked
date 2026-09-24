from .common_imports import *
from uniworlderp.models import ARInvoice, CustomerVendor, Payment, PaymentMethod, SalesEmployee
from uniworlderp.forms import PaymentForm


class PaymentListView(ListView):
    model = Payment
    template_name = 'payment/list.html'
    context_object_name = 'payments'
    paginate_by = 100

    def get_queryset(self):
        search_query = self.request.GET.get('search', '')
        customer_id = self.request.GET.get('customer', '')
        sales_employee_id = self.request.GET.get('sales_employee', '')
        method_id = self.request.GET.get('method', '')
        received_by = self.request.GET.get('received_by', '')
        start_date = self.request.GET.get('start_date', '')
        end_date = self.request.GET.get('end_date', '')

        queryset = Payment.objects.select_related('invoice', 'customer', 'sales_employee', 'method').all()

        if search_query:
            queryset = queryset.filter(
                Q(id__icontains=search_query) |
                Q(invoice__id__icontains=search_query) |
                Q(invoice__sales_order__id__icontains=search_query) |
                Q(customer__name__icontains=search_query) |
                Q(notes__icontains=search_query)
            )
        if customer_id:
            queryset = queryset.filter(customer_id=customer_id)
        if sales_employee_id:
            queryset = queryset.filter(sales_employee_id=sales_employee_id)
        if method_id:
            queryset = queryset.filter(method_id=method_id)
        if received_by:
            queryset = queryset.filter(received_by__icontains=received_by)
        if start_date:
            queryset = queryset.filter(payment_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(payment_date__lte=end_date)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        context['selected_customer'] = self.request.GET.get('customer', '')
        context['selected_sales_employee'] = self.request.GET.get('sales_employee', '')
        context['selected_method'] = self.request.GET.get('method', '')
        context['selected_received_by'] = self.request.GET.get('received_by', '')
        context['customers'] = CustomerVendor.objects.filter(entity_type='customer').order_by('name')
        context['sales_employees'] = SalesEmployee.objects.order_by('full_name')
        context['methods'] = PaymentMethod.objects.filter(is_active=True)
        filtered = self.get_queryset()
        context['total_amount'] = filtered.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        context['daily_totals'] = (
            filtered.values('payment_date')
            .annotate(total=Sum('amount'), count=Count('id'))
            .order_by('-payment_date')
        )
        return context


class PaymentCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Payment
    form_class = PaymentForm
    permission_required = 'uniworlderp.add_payment'

    def handle_no_permission(self):
        messages.error(self.request, "You do not have permission to record payments.")
        return redirect('customer_vendor:invoice_view', pk=self.kwargs.get('invoice_id'))

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.invoice = get_object_or_404(ARInvoice, pk=kwargs.get('invoice_id'))

    def get_success_url(self):
        return reverse('customer_vendor:invoice_view', kwargs={'pk': self.invoice.pk})

    @transaction.atomic
    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.invoice = self.invoice
        self.object.customer = self.invoice.customer
        self.object.sales_employee = self.invoice.sales_employee
        self.object.created_by = self.request.user
        try:
            self.object.save()
        except ValidationError as e:
            messages.error(self.request, ' '.join(e.messages))
            return redirect('customer_vendor:invoice_view', pk=self.invoice.pk)
        messages.success(self.request, 'Payment recorded successfully.')
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(self.request, f"{field}: {error}")
        return redirect('customer_vendor:invoice_view', pk=self.invoice.pk)


class PaymentDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Payment
    template_name = 'payment/receipt.html'
    context_object_name = 'payment'
    permission_required = 'uniworlderp.view_payment'

    def handle_no_permission(self):
        messages.error(self.request, "You do not have permission to view this payment.")
        return redirect('customer_vendor:payment_list')


class PaymentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Payment
    form_class = PaymentForm
    template_name = 'payment/form.html'
    permission_required = 'uniworlderp.change_payment'

    def handle_no_permission(self):
        messages.error(self.request, "You do not have permission to edit this payment.")
        return redirect('customer_vendor:payment_list')

    def get_success_url(self):
        return reverse('customer_vendor:invoice_view', kwargs={'pk': self.object.invoice_id})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Payment updated successfully.')
        return response


class PaymentDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Payment
    template_name = 'confirm_delete.html'
    permission_required = 'uniworlderp.delete_payment'

    def handle_no_permission(self):
        messages.error(self.request, "You do not have permission to delete this payment.")
        return redirect('customer_vendor:payment_list')

    def get_success_url(self):
        return reverse('customer_vendor:invoice_view', kwargs={'pk': self.object.invoice_id})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['model_name'] = 'Payment'
        context['cancel_url'] = reverse('customer_vendor:invoice_view', kwargs={'pk': self.object.invoice_id})
        return context
