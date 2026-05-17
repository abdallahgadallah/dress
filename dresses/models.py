from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Dress(models.Model):
    STATUS_AVAILABLE = "available"
    STATUS_RENTED = "rented"
    STATUS_LAUNDRY = "laundry"
    STATUS_SOLD = "sold"

    STATUS_CHOICES = [
        (STATUS_AVAILABLE, "متاح"),
        (STATUS_RENTED, "مؤجر"),
        (STATUS_LAUNDRY, "في الغسيل"),
        (STATUS_SOLD, "تم بيعه"),
    ]

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True)
    size = models.CharField(max_length=20)
    color = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    rent_price = models.DecimalField(max_digits=10, decimal_places=2)
    sell_price = models.DecimalField(max_digits=10, decimal_places=2)
    tailoring_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_AVAILABLE,
    )
    image = models.ImageField(upload_to="dresses/", null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.name} - {self.code}"


class Customer(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


class Transaction(models.Model):
    TYPE_RENT = "rent"
    TYPE_SALE = "sale"
    TYPE_TAILORING = "tailoring"

    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    TYPE_CHOICES = [
        (TYPE_RENT, "إيجار"),
        (TYPE_SALE, "بيع"),
        (TYPE_TAILORING, "تفصيل"),
    ]
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "نشطة"),
        (STATUS_COMPLETED, "مكتملة"),
        (STATUS_CANCELLED, "ملغية"),
    ]

    invoice_number = models.CharField(max_length=30, unique=True, editable=False)
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    dress = models.ForeignKey(
        Dress,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_transactions",
    )
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bust = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    shoulder = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    waist = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    rent_start_date = models.DateField(null=True, blank=True)
    expected_return_date = models.DateField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    delivery_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.invoice_number

    @property
    def balance(self):
        return self.amount - self.paid_amount

    @property
    def is_fully_paid(self):
        return self.balance <= 0

    @property
    def is_open_rent(self):
        return (
            self.transaction_type == self.TYPE_RENT
            and self.status == self.STATUS_ACTIVE
            and self.returned_at is None
        )

    @property
    def is_current_rent(self):
        today = timezone.localdate()
        return (
            self.is_open_rent
            and self.rent_start_date is not None
            and self.expected_return_date is not None
            and self.rent_start_date <= today <= self.expected_return_date
        )

    @property
    def is_upcoming_rent(self):
        today = timezone.localdate()
        return self.is_open_rent and self.rent_start_date is not None and self.rent_start_date > today

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            stamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
            self.invoice_number = f"INV-{stamp}"
        super().save(*args, **kwargs)


class LaundryRecord(models.Model):
    STATUS_PENDING = "pending"
    STATUS_DONE = "done"

    STATUS_CHOICES = [
        (STATUS_PENDING, "قيد الغسيل"),
        (STATUS_DONE, "تم الغسيل"),
    ]

    dress = models.ForeignKey(Dress, on_delete=models.CASCADE, related_name="laundry_records")
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="laundry_records",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.dress.code} - {self.get_status_display()}"
