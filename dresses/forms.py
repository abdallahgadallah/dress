from django import forms

from .image_utils import convert_heif_to_jpeg
from .models import Dress, Expense, SalesCustomer, Supplier, Transaction


class TransactionForm(forms.Form):
    customer_name = forms.CharField(max_length=100, label="اسم العميل")
    phone = forms.CharField(max_length=11, label="رقم الهاتف")
    customer_notes = forms.CharField(
        required=False,
        label="ملاحظات على العميل",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    dress_id = forms.ModelChoiceField(
        queryset=Dress.objects.none(),
        required=False,
        empty_label="اختر الفستان",
        label="الفستان",
    )
    amount = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, label="إجمالي الفاتورة")
    paid_amount = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, label="المدفوع الآن")
    bust = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=6,
        label="صدر",
    )
    shoulder = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=6,
        label="كتف",
    )
    waist = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=6,
        label="وسط",
    )
    side = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=6,
        label="جناب",
    )
    sleeve = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=6,
        label="كم",
    )
    waist_length = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=6,
        label="طول الوسط",
    )
    skirt_length = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=6,
        label="طول الجيبة",
    )
    full_length = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=6,
        label="طول كامل",
    )
    rent_start_date = forms.DateField(required=False, label="من يوم")
    expected_return_date = forms.DateField(required=False, label="إلى يوم")
    delivery_date = forms.DateField(required=False, label="موعد التسليم")
    tailoring_model_image = forms.ImageField(required=False, label="صورة موديل التفصيل")
    description = forms.CharField(
        required=False,
        label="تفاصيل الفاتورة أو الطلب",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, transaction_type, dresses=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.transaction_type = transaction_type
        self.fields["dress_id"].queryset = dresses or Dress.objects.none()

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone.isdigit() or len(phone) != 11:
            raise forms.ValidationError("رقم الهاتف يجب أن يتكون من 11 رقمًا فقط.")
        return phone

    def clean_tailoring_model_image(self):
        return convert_heif_to_jpeg(self.cleaned_data.get("tailoring_model_image"))

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get("amount")
        paid_amount = cleaned_data.get("paid_amount")
        rent_start_date = cleaned_data.get("rent_start_date")
        expected_return_date = cleaned_data.get("expected_return_date")
        dress = cleaned_data.get("dress_id")

        if amount is not None and paid_amount is not None and paid_amount > amount:
            self.add_error("paid_amount", "لا يمكن أن يكون العربون أكبر من إجمالي الفاتورة.")

        if self.transaction_type != Transaction.TYPE_TAILORING and dress is None:
            self.add_error("dress_id", "اختر الفستان.")

        if self.transaction_type == Transaction.TYPE_RENT:
            if not rent_start_date:
                self.add_error("rent_start_date", "حدد تاريخ بداية الإيجار.")
            if not expected_return_date:
                self.add_error("expected_return_date", "حدد تاريخ نهاية الإيجار.")
            if rent_start_date and expected_return_date and rent_start_date > expected_return_date:
                self.add_error("expected_return_date", "تاريخ نهاية الإيجار يجب أن يكون بعد تاريخ البداية.")

        return cleaned_data


class SecurityDepositForm(forms.Form):
    security_deposit = forms.DecimalField(
        min_value=0.01,
        decimal_places=2,
        max_digits=10,
        label="قيمة التأمين",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
    )


class SupplierForm(forms.Form):
    name = forms.CharField(
        max_length=120,
        label="اسم المورد",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    phone = forms.CharField(
        max_length=20,
        required=False,
        label="رقم الهاتف",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    notes = forms.CharField(
        required=False,
        label="ملاحظات",
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
    )


class PurchaseForm(forms.Form):
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all(),
        required=False,
        empty_label="بدون مورد",
        label="المورد",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    purchased_at = forms.DateField(
        label="تاريخ الشراء",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    initial_paid_amount = forms.DecimalField(
        min_value=0,
        decimal_places=2,
        max_digits=10,
        required=False,
        label="المدفوع الآن",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    notes = forms.CharField(
        required=False,
        label="ملاحظات الفاتورة",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )

    def clean_initial_paid_amount(self):
        return self.cleaned_data.get("initial_paid_amount") or 0


class SalesOrderForm(forms.Form):
    customer = forms.ModelChoiceField(
        queryset=SalesCustomer.objects.all(),
        label="العميل",
        empty_label="اختر العميل",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    ordered_at = forms.DateField(
        label="تاريخ الطلب",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    initial_paid_amount = forms.DecimalField(
        min_value=0,
        decimal_places=2,
        max_digits=10,
        required=False,
        label="المدفوع الآن",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    notes = forms.CharField(
        required=False,
        label="ملاحظات الطلب",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )

    def clean_initial_paid_amount(self):
        return self.cleaned_data.get("initial_paid_amount") or 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = SalesCustomer.objects.order_by("name")


class SalesCustomerForm(forms.Form):
    name = forms.CharField(
        max_length=100,
        label="اسم العميل",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    phone = forms.CharField(
        max_length=20,
        required=False,
        label="رقم الهاتف",
        widget=forms.TextInput(attrs={"class": "form-control", "inputmode": "numeric"}),
    )
    notes = forms.CharField(
        required=False,
        label="ملاحظات",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


class PaymentForm(forms.Form):
    amount = forms.DecimalField(
        min_value=0.01,
        decimal_places=2,
        max_digits=10,
        label="المبلغ",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    notes = forms.CharField(
        required=False,
        label="ملاحظات",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )


class ExpenseForm(forms.Form):
    expense_type = forms.ChoiceField(
        choices=Expense.TYPE_CHOICES,
        label="نوع المصروف",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    title = forms.CharField(
        max_length=150,
        label="اسم المصروف",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    amount = forms.DecimalField(
        min_value=0.01,
        decimal_places=2,
        max_digits=10,
        label="المبلغ",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    spent_at = forms.DateField(
        label="تاريخ المصروف",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    notes = forms.CharField(
        required=False,
        label="ملاحظات",
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
    )
