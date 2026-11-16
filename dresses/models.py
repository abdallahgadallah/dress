from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class DressFamily(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    rent_price = models.DecimalField(max_digits=10, decimal_places=2)
    sell_price = models.DecimalField(max_digits=10, decimal_places=2)
    tailoring_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    image = models.ImageField(upload_to="dresses/", null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def items_count(self):
        return self.items.count()


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

    family = models.ForeignKey(
        DressFamily,
        on_delete=models.CASCADE,
        related_name="items",
        null=True,
        blank=True,
    )
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

    @property
    def variant_label(self):
        if self.color:
            return f"{self.size} - {self.color}"
        return self.size

    def save(self, *args, **kwargs):
        if self.family_id is None:
            self.family = DressFamily.objects.create(
                name=self.name,
                description=self.description,
                rent_price=self.rent_price,
                sell_price=self.sell_price,
                tailoring_price=self.tailoring_price,
                image=self.image,
            )
        super().save(*args, **kwargs)


class Customer(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


class SalesCustomer(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

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
    security_deposit = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    security_deposit_received_at = models.DateTimeField(null=True, blank=True)
    security_deposit_returned_at = models.DateTimeField(null=True, blank=True)
    bust = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    shoulder = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    waist = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    side = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    sleeve = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    waist_length = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    skirt_length = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    full_length = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    tailoring_model_image = models.ImageField(upload_to="tailoring_models/", null=True, blank=True)
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
    def has_security_deposit(self):
        return self.transaction_type == self.TYPE_RENT and self.security_deposit > 0

    @property
    def is_security_deposit_returned(self):
        return self.security_deposit_returned_at is not None

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


class Purchase(models.Model):
    STATUS_OPEN = "open"
    STATUS_PAID = "paid"

    STATUS_CHOICES = [
        (STATUS_OPEN, "مفتوحة"),
        (STATUS_PAID, "مدفوعة"),
    ]

    invoice_number = models.CharField(max_length=30, unique=True, editable=False)
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchases",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_purchases",
    )
    notes = models.TextField(blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    purchased_at = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-purchased_at", "-created_at"]

    def __str__(self):
        return self.invoice_number

    @property
    def balance(self):
        return self.total_amount - self.paid_amount

    @property
    def is_fully_paid(self):
        return self.balance <= 0

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            stamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
            self.invoice_number = f"PUR-{stamp}"
        super().save(*args, **kwargs)


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="items")
    item_name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.item_name} x {self.quantity}"

    def save(self, *args, **kwargs):
        self.total_cost = Decimal(self.quantity) * self.unit_cost
        super().save(*args, **kwargs)


class SalesOrder(models.Model):
    STATUS_OPEN = "open"
    STATUS_PAID = "paid"

    STATUS_CHOICES = [
        (STATUS_OPEN, "مفتوحة"),
        (STATUS_PAID, "مدفوعة"),
    ]

    invoice_number = models.CharField(max_length=30, unique=True, editable=False)
    customer = models.ForeignKey(SalesCustomer, on_delete=models.SET_NULL, null=True, related_name="sales_orders")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_sales_orders",
    )
    notes = models.TextField(blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    ordered_at = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ordered_at", "-created_at"]

    def __str__(self):
        return self.invoice_number

    @property
    def balance(self):
        return self.total_amount - self.paid_amount

    @property
    def is_fully_paid(self):
        return self.balance <= 0

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            stamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
            self.invoice_number = f"SAL-{stamp}"
        super().save(*args, **kwargs)


class SalesOrderItem(models.Model):
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="items")
    dress_name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.dress_name} x {self.quantity}"

    def save(self, *args, **kwargs):
        self.total_price = Decimal(self.quantity) * self.unit_price
        super().save(*args, **kwargs)


class Payment(models.Model):
    DIRECTION_IN = "in"
    DIRECTION_OUT = "out"

    DIRECTION_CHOICES = [
        (DIRECTION_IN, "وارد"),
        (DIRECTION_OUT, "صادر"),
    ]

    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="payments",
    )
    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="payments",
    )
    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="payments",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_payments",
    )
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        target = self.transaction or self.purchase or self.sales_order
        return f"{self.amount} - {target}"

    def clean(self):
        targets_count = sum(
            target_id is not None
            for target_id in [self.transaction_id, self.purchase_id, self.sales_order_id]
        )
        if targets_count != 1:
            from django.core.exceptions import ValidationError

            raise ValidationError("الدفعة يجب أن ترتبط بفاتورة واحدة فقط.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Expense(models.Model):
    TYPE_RENT = "rent"
    TYPE_TAILORING = "tailoring"

    TYPE_CHOICES = [
        (TYPE_RENT, "مصروف الأتيليه"),
        (TYPE_TAILORING, "مصروف المصنع"),
    ]

    expense_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title = models.CharField(max_length=150)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)
    spent_at = models.DateField(default=timezone.localdate)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_expenses",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-spent_at", "-created_at"]

    def __str__(self):
        return f"{self.title} - {self.amount}"


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
