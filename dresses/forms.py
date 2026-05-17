from django import forms

from .models import Dress, Transaction


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
    rent_start_date = forms.DateField(required=False, label="من يوم")
    expected_return_date = forms.DateField(required=False, label="إلى يوم")
    delivery_date = forms.DateField(required=False, label="موعد التسليم")
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
