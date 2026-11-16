from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.core.paginator import Paginator
from django.db.models import Count, F, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils import timezone

from .forms import ExpenseForm, PaymentForm, PurchaseForm, SalesCustomerForm, SalesOrderForm, SecurityDepositForm, SupplierForm, TransactionForm
from .image_utils import convert_heif_to_jpeg
from .models import Customer, Dress, DressFamily, Expense, LaundryRecord, Payment, Purchase, PurchaseItem, SalesCustomer, SalesOrder, SalesOrderItem, Supplier, Transaction


def _to_decimal(value, default="0"):
    try:
        return Decimal(str(value or default))
    except (InvalidOperation, TypeError):
        return Decimal(default)


def _refresh_dress_status(dress):
    today = timezone.localdate()
    has_completed_sale = dress.transactions.filter(
        transaction_type=Transaction.TYPE_SALE,
        status=Transaction.STATUS_COMPLETED,
    ).exists()
    has_pending_laundry = dress.laundry_records.filter(status=LaundryRecord.STATUS_PENDING).exists()
    has_current_rent = dress.transactions.filter(
        transaction_type=Transaction.TYPE_RENT,
        status=Transaction.STATUS_ACTIVE,
        returned_at__isnull=True,
        rent_start_date__lte=today,
        expected_return_date__gte=today,
    ).exists()

    if has_completed_sale:
        new_status = Dress.STATUS_SOLD
    elif has_pending_laundry:
        new_status = Dress.STATUS_LAUNDRY
    elif has_current_rent:
        new_status = Dress.STATUS_RENTED
    else:
        new_status = Dress.STATUS_AVAILABLE

    if dress.status != new_status:
        dress.status = new_status
        dress.save(update_fields=["status"])


def _has_rent_overlap(dress, rent_start_date, expected_return_date):
    return dress.transactions.filter(
        transaction_type=Transaction.TYPE_RENT,
        status=Transaction.STATUS_ACTIVE,
        returned_at__isnull=True,
        rent_start_date__lte=expected_return_date,
        expected_return_date__gte=rent_start_date,
    ).exists()


def _has_rent_overlap_excluding(transaction, dress, rent_start_date, expected_return_date):
    return dress.transactions.exclude(id=transaction.id).filter(
        transaction_type=Transaction.TYPE_RENT,
        status=Transaction.STATUS_ACTIVE,
        returned_at__isnull=True,
        rent_start_date__lte=expected_return_date,
        expected_return_date__gte=rent_start_date,
    ).exists()


def _get_or_create_customer(name, phone, notes):
    if not name:
        return None

    customer, _ = Customer.objects.get_or_create(
        name=name,
        phone=phone,
        defaults={"notes": notes},
    )
    fields_to_update = []
    if customer.phone != phone:
        customer.phone = phone
        fields_to_update.append("phone")
    if notes and customer.notes != notes:
        customer.notes = notes
        fields_to_update.append("notes")
    if fields_to_update:
        customer.save(update_fields=fields_to_update)
    return customer


def _get_or_create_sales_customer(name, phone, notes):
    customer, _ = SalesCustomer.objects.get_or_create(
        name=name,
        phone=phone,
        defaults={"notes": notes},
    )
    fields_to_update = []
    if notes and customer.notes != notes:
        customer.notes = notes
        fields_to_update.append("notes")
    if fields_to_update:
        customer.save(update_fields=fields_to_update)
    return customer


def _parse_dress_variants(code, size, color, variant_codes, variant_sizes, variant_colors):
    variants = []
    used_codes = set()

    def add_variant(code_value, size_value, color_value):
        normalized_code = (code_value or "").strip()
        normalized_size = (size_value or "").strip()
        normalized_color = (color_value or "").strip()

        if not normalized_code or not normalized_size:
            return "كل قطعة لازم يكون لها كود ومقاس."
        if normalized_code in used_codes:
            return f"الكود {normalized_code} مكرر داخل نفس الفستان."

        used_codes.add(normalized_code)
        variants.append(
            {
                "code": normalized_code,
                "size": normalized_size,
                "color": normalized_color,
            }
        )
        return None

    error = add_variant(code, size, color)
    if error:
        return None, error

    total_variants = max(len(variant_codes), len(variant_sizes), len(variant_colors))
    for index in range(total_variants):
        code_value = variant_codes[index] if index < len(variant_codes) else ""
        size_value = variant_sizes[index] if index < len(variant_sizes) else ""
        color_value = variant_colors[index] if index < len(variant_colors) else ""

        if not any([(code_value or "").strip(), (size_value or "").strip(), (color_value or "").strip()]):
            continue

        error = add_variant(code_value, size_value, color_value)
        if error:
            return None, error

    return variants, None


def _sync_family_items_from_family(family):
    family.items.update(
        name=family.name,
        description=family.description,
        rent_price=family.rent_price,
        sell_price=family.sell_price,
        tailoring_price=family.tailoring_price,
        image=family.image.name if family.image else None,
    )


def _transaction_form_context(transaction_type, request, transaction=None):
    dresses = Dress.objects.exclude(status=Dress.STATUS_SOLD)
    if transaction and transaction.dress_id:
        dresses = dresses | Dress.objects.filter(id=transaction.dress_id)
    dresses = dresses.distinct().order_by("name")
    return {
        "transaction_type": transaction_type,
        "page_title": {
            Transaction.TYPE_RENT: "فاتورة إيجار",
            Transaction.TYPE_SALE: "فاتورة بيع",
            Transaction.TYPE_TAILORING: "فاتورة تفصيل",
        }[transaction_type],
        "dresses": dresses,
        "transaction": transaction,
        "selected_dress_id": str(
            request.POST.get("dress_id")
            or request.GET.get("dress")
            or (transaction.dress_id if transaction and transaction.dress_id else "")
        ),
    }


def _transaction_form_initial(transaction_type, request, transaction=None):
    initial = {
        "dress_id": request.GET.get("dress", ""),
    }
    if transaction:
        initial.update(
            {
                "customer_name": transaction.customer.name if transaction.customer else "",
                "phone": transaction.customer.phone if transaction.customer else "",
                "customer_notes": transaction.customer.notes if transaction.customer else "",
                "dress_id": transaction.dress_id or "",
                "amount": transaction.amount,
                "paid_amount": transaction.paid_amount,
                "bust": transaction.bust,
                "shoulder": transaction.shoulder,
                "waist": transaction.waist,
                "side": transaction.side,
                "sleeve": transaction.sleeve,
                "waist_length": transaction.waist_length,
                "skirt_length": transaction.skirt_length,
                "full_length": transaction.full_length,
                "tailoring_model_image": transaction.tailoring_model_image,
                "rent_start_date": transaction.rent_start_date,
                "expected_return_date": transaction.expected_return_date,
                "delivery_date": transaction.delivery_date,
                "description": transaction.description,
            }
        )
    return initial


def _user_can_view_financials(user):
    return user.is_superuser or user.is_staff or user.groups.filter(name="manager").exists()


def _user_can_manage_users(user):
    return user.is_superuser or user.is_staff or user.groups.filter(name="manager").exists()


def _normalize_transaction_status(transaction):
    if transaction.transaction_type == Transaction.TYPE_SALE:
        return Transaction.STATUS_COMPLETED
    if transaction.transaction_type == Transaction.TYPE_TAILORING and transaction.paid_amount >= transaction.amount:
        return Transaction.STATUS_COMPLETED
    if transaction.status == Transaction.STATUS_CANCELLED:
        return Transaction.STATUS_CANCELLED
    return Transaction.STATUS_ACTIVE


def _create_transaction_payment(transaction, amount, user, notes="دفعة على الفاتورة"):
    amount = _to_decimal(amount)
    if amount <= 0:
        return None

    payment = Payment.objects.create(
        transaction=transaction,
        direction=Payment.DIRECTION_IN,
        amount=amount,
        notes=notes,
        created_by=user,
    )
    transaction.paid_amount = (transaction.paid_amount or Decimal("0.00")) + amount
    transaction.status = _normalize_transaction_status(transaction)
    transaction.save(update_fields=["paid_amount", "status"])
    return payment


def _sync_purchase_totals(purchase):
    items_total = purchase.items.aggregate(total=Coalesce(Sum("total_cost"), Decimal("0.00")))["total"]
    total_amount = max(purchase.total_amount or Decimal("0.00"), items_total)
    payments_total = purchase.payments.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
    legacy_paid = max((purchase.paid_amount or Decimal("0.00")) - payments_total, Decimal("0.00"))
    paid_amount = legacy_paid + payments_total
    purchase.total_amount = total_amount
    purchase.paid_amount = paid_amount
    purchase.status = Purchase.STATUS_PAID if paid_amount >= total_amount and total_amount > 0 else Purchase.STATUS_OPEN
    purchase.save(update_fields=["total_amount", "paid_amount", "status", "updated_at"])


def _supplier_totals(supplier):
    purchases = supplier.purchases.all()
    total_amount = purchases.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"]
    paid_amount = purchases.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]
    return {
        "total_amount": total_amount,
        "paid_amount": paid_amount,
        "due_amount": total_amount - paid_amount,
    }


def _record_supplier_payment(supplier, amount, user, notes):
    amount = _to_decimal(amount)
    if amount <= 0:
        return Decimal("0.00")

    remaining = amount
    open_purchases = supplier.purchases.filter(
        total_amount__gt=0,
        paid_amount__lt=F("total_amount"),
    ).order_by("purchased_at", "created_at")

    for purchase in open_purchases:
        purchase_due = purchase.balance
        if purchase_due <= 0:
            continue
        payment_amount = min(remaining, purchase_due)
        Payment.objects.create(
            purchase=purchase,
            direction=Payment.DIRECTION_OUT,
            amount=payment_amount,
            notes=notes or "دفعة من حساب المورد",
            created_by=user,
        )
        purchase.paid_amount = (purchase.paid_amount or Decimal("0.00")) + payment_amount
        _sync_purchase_totals(purchase)
        remaining -= payment_amount
        if remaining <= 0:
            break

    return amount - remaining


def _payment_redirect(payment):
    if payment.purchase and payment.purchase.supplier_id:
        return redirect("supplier_payments", supplier_id=payment.purchase.supplier_id)
    return redirect("purchase_list")


def _parse_purchase_items(post_data):
    names = post_data.getlist("item_name[]")
    quantities = post_data.getlist("item_quantity[]")
    costs = post_data.getlist("item_unit_cost[]")
    notes_list = post_data.getlist("item_notes[]")

    items = []
    row_count = max(len(names), len(quantities), len(costs), len(notes_list))
    for index in range(row_count):
        name = (names[index] if index < len(names) else "").strip()
        quantity_raw = (quantities[index] if index < len(quantities) else "").strip()
        cost_raw = (costs[index] if index < len(costs) else "").strip()
        notes = (notes_list[index] if index < len(notes_list) else "").strip()

        if not any([name, quantity_raw, cost_raw, notes]):
            continue
        if not name:
            return None, "كل سطر شراء لازم يكون له اسم قطعة."
        try:
            quantity = int(quantity_raw)
        except (TypeError, ValueError):
            return None, "الكمية يجب أن تكون رقمًا صحيحًا."
        if quantity <= 0:
            return None, "الكمية يجب أن تكون أكبر من صفر."

        unit_cost = _to_decimal(cost_raw)
        if unit_cost <= 0:
            return None, "سعر القطعة يجب أن يكون أكبر من صفر."

        items.append(
            {
                "item_name": name,
                "quantity": quantity,
                "unit_cost": unit_cost,
                "notes": notes,
            }
        )

    if not items:
        return None, "أضف قطعة واحدة على الأقل في فاتورة الشراء."

    return items, None


def _purchase_form_rows(post_data=None):
    if post_data is None:
        return [{"item_name": "", "item_quantity": 1, "item_unit_cost": "", "item_notes": ""}]

    names = post_data.getlist("item_name[]")
    quantities = post_data.getlist("item_quantity[]")
    costs = post_data.getlist("item_unit_cost[]")
    notes_list = post_data.getlist("item_notes[]")
    row_count = max(1, len(names), len(quantities), len(costs), len(notes_list))

    rows = []
    for index in range(row_count):
        rows.append(
            {
                "item_name": names[index] if index < len(names) else "",
                "item_quantity": quantities[index] if index < len(quantities) else "1",
                "item_unit_cost": costs[index] if index < len(costs) else "",
                "item_notes": notes_list[index] if index < len(notes_list) else "",
            }
        )
    return rows


def _sync_sales_order_totals(sales_order):
    items_total = sales_order.items.aggregate(total=Coalesce(Sum("total_price"), Decimal("0.00")))["total"]
    sales_order.total_amount = max(sales_order.total_amount or Decimal("0.00"), items_total)
    sales_order.paid_amount = sales_order.payments.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
    sales_order.status = SalesOrder.STATUS_PAID if sales_order.is_fully_paid and sales_order.total_amount > 0 else SalesOrder.STATUS_OPEN
    sales_order.save(update_fields=["total_amount", "paid_amount", "status", "updated_at"])


def _create_sales_order_payment(sales_order, amount, user, notes="دفعة على فاتورة المبيعات"):
    amount = _to_decimal(amount)
    if amount <= 0 or amount > sales_order.balance:
        return None
    Payment.objects.create(
        sales_order=sales_order,
        direction=Payment.DIRECTION_IN,
        amount=amount,
        notes=notes,
        created_by=user,
    )
    _sync_sales_order_totals(sales_order)


def _customer_sales_totals(customer):
    orders = customer.sales_orders.all()
    total_amount = orders.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"]
    paid_amount = orders.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]
    return {"total_amount": total_amount, "paid_amount": paid_amount, "due_amount": total_amount - paid_amount}


def _record_customer_sales_payment(customer, amount, user, notes):
    remaining = _to_decimal(amount)
    if remaining <= 0:
        return Decimal("0.00")
    original_amount = remaining
    for sales_order in customer.sales_orders.filter(total_amount__gt=0, paid_amount__lt=F("total_amount")).order_by("ordered_at", "created_at"):
        payment_amount = min(remaining, sales_order.balance)
        _create_sales_order_payment(sales_order, payment_amount, user, notes or "دفعة من حساب العميل")
        remaining -= payment_amount
        if remaining <= 0:
            break
    return original_amount - remaining


def _parse_sales_order_items(post_data):
    names = post_data.getlist("dress_name[]")
    quantities = post_data.getlist("dress_quantity[]")
    prices = post_data.getlist("dress_unit_price[]")
    notes_list = post_data.getlist("dress_notes[]")
    items = []
    for index in range(max(len(names), len(quantities), len(prices), len(notes_list))):
        name = (names[index] if index < len(names) else "").strip()
        quantity_raw = (quantities[index] if index < len(quantities) else "").strip()
        price_raw = (prices[index] if index < len(prices) else "").strip()
        notes = (notes_list[index] if index < len(notes_list) else "").strip()
        if not any([name, quantity_raw, price_raw, notes]):
            continue
        if not name:
            return None, "اكتب اسم الموديل في كل سطر مبيعات."
        try:
            quantity = int(quantity_raw)
        except (TypeError, ValueError):
            return None, "كمية كل موديل يجب أن تكون رقمًا صحيحًا."
        if quantity <= 0:
            return None, "كمية كل موديل يجب أن تكون أكبر من صفر."
        unit_price = _to_decimal(price_raw)
        if unit_price <= 0:
            return None, "سعر الموديل يجب أن يكون أكبر من صفر."
        items.append({"dress_name": name, "quantity": quantity, "unit_price": unit_price, "notes": notes})
    if not items:
        return None, "أضف موديل فستان واحد على الأقل في طلب المبيعات."
    return items, None


def _sales_order_form_rows(post_data=None):
    if post_data is None:
        return [{"dress_name": "", "dress_quantity": 1, "dress_unit_price": "", "dress_notes": ""}]
    names = post_data.getlist("dress_name[]")
    quantities = post_data.getlist("dress_quantity[]")
    prices = post_data.getlist("dress_unit_price[]")
    notes_list = post_data.getlist("dress_notes[]")
    return [
        {
            "dress_name": names[index] if index < len(names) else "",
            "dress_quantity": quantities[index] if index < len(quantities) else "1",
            "dress_unit_price": prices[index] if index < len(prices) else "",
            "dress_notes": notes_list[index] if index < len(notes_list) else "",
        }
        for index in range(max(1, len(names), len(quantities), len(prices), len(notes_list)))
    ]


def _dashboard_context():
    today = timezone.localdate()
    month_start = today.replace(day=1)

    transactions = Transaction.objects.select_related("customer", "dress")
    sales_orders = SalesOrder.objects.all()
    purchases = Purchase.objects.select_related("supplier", "created_by")
    dresses = Dress.objects.all()
    current_rentals = transactions.filter(
        transaction_type=Transaction.TYPE_RENT,
        status=Transaction.STATUS_ACTIVE,
        returned_at__isnull=True,
        rent_start_date__lte=today,
        expected_return_date__gte=today,
    )
    upcoming_rentals = transactions.filter(
        transaction_type=Transaction.TYPE_RENT,
        status=Transaction.STATUS_ACTIVE,
        returned_at__isnull=True,
        rent_start_date__gt=today,
    )
    pending_laundry = LaundryRecord.objects.filter(status=LaundryRecord.STATUS_PENDING).select_related(
        "dress", "transaction"
    )

    today_transactions = transactions.filter(created_at__date=today)
    month_transactions = transactions.filter(created_at__date__gte=month_start)
    today_sales_orders = sales_orders.filter(created_at__date=today)
    month_sales_orders = sales_orders.filter(created_at__date__gte=month_start)
    today_rent_transactions = today_transactions.filter(transaction_type=Transaction.TYPE_RENT)
    today_sale_transactions = today_transactions.filter(transaction_type=Transaction.TYPE_SALE)
    today_tailoring_transactions = today_transactions.filter(transaction_type=Transaction.TYPE_TAILORING)
    month_rent_transactions = month_transactions.filter(transaction_type=Transaction.TYPE_RENT)
    month_sale_transactions = month_transactions.filter(transaction_type=Transaction.TYPE_SALE)
    month_tailoring_transactions = month_transactions.filter(transaction_type=Transaction.TYPE_TAILORING)

    month_amount = month_transactions.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
    month_paid = month_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]
    month_sales_order_amount = month_sales_orders.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"]
    month_sales_order_paid = month_sales_orders.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]
    month_purchases = purchases.filter(purchased_at__gte=month_start)
    month_purchase_total = month_purchases.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"]
    month_purchase_paid = month_purchases.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]

    stats = {
        "today_income": today_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"] + today_sales_orders.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
        "today_rent_income": today_rent_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
        "today_sale_invoice_income": today_sale_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
        "today_sales_orders_income": today_sales_orders.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
        "today_tailoring_income": today_tailoring_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
        "today_sale_invoices_count": today_transactions.filter(transaction_type=Transaction.TYPE_SALE).count(),
        "today_sales_orders_count": today_sales_orders.count(),
        "today_rents_count": today_transactions.filter(transaction_type=Transaction.TYPE_RENT).count(),
        "today_tailoring_count": today_transactions.filter(transaction_type=Transaction.TYPE_TAILORING).count(),
        "month_income": month_paid + month_sales_order_paid,
        "month_rent_income": month_rent_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
        "month_sale_invoice_income": month_sale_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
        "month_sales_orders_income": month_sales_order_paid,
        "month_tailoring_income": month_tailoring_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
        "month_total": month_amount + month_sales_order_amount,
        "month_due": (month_amount + month_sales_order_amount) - (month_paid + month_sales_order_paid),
        "month_purchase_total": month_purchase_total,
        "month_purchase_paid": month_purchase_paid,
        "month_purchase_due": month_purchase_total - month_purchase_paid,
        "available_dresses": dresses.filter(status=Dress.STATUS_AVAILABLE).count(),
        "rented_dresses": dresses.filter(status=Dress.STATUS_RENTED).count(),
        "laundry_dresses": dresses.filter(status=Dress.STATUS_LAUNDRY).count(),
        "sold_dresses": dresses.filter(status=Dress.STATUS_SOLD).count(),
    }

    monthly_breakdown = list(
        month_transactions.values("transaction_type")
        .annotate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")), count=Count("id"))
        .order_by("transaction_type")
    )
    type_labels = dict(Transaction.TYPE_CHOICES)
    for row in monthly_breakdown:
        row["label"] = type_labels.get(row["transaction_type"], row["transaction_type"])

    return {
        "dresses": dresses.order_by("-created_at"),
        "recent_transactions": transactions[:5],
        "active_rentals": current_rentals[:8],
        "upcoming_rentals": upcoming_rentals[:8],
        "pending_laundry": pending_laundry[:8],
        "recent_purchases": month_purchases.order_by("-created_at")[:5],
        "stats": stats,
        "monthly_breakdown": monthly_breakdown,
        "today": today,
    }


@login_required
def dashboard(request):
    context = _dashboard_context()
    context["can_view_financials"] = _user_can_view_financials(request.user)
    return render(request, "dashboard.html", context)


@login_required
def monthly_statistics(request):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    today = timezone.localdate()
    try:
        selected_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        selected_year = today.year
    try:
        selected_month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        selected_month = today.month

    if selected_month < 1 or selected_month > 12:
        selected_month = today.month

    month_start = today.replace(year=selected_year, month=selected_month, day=1)
    if selected_month == 12:
        next_month = month_start.replace(year=selected_year + 1, month=1, day=1)
    else:
        next_month = month_start.replace(month=selected_month + 1, day=1)

    transactions = Transaction.objects.filter(
        created_at__date__gte=month_start,
        created_at__date__lt=next_month,
    ).select_related("customer", "dress", "created_by")
    sales_orders = SalesOrder.objects.filter(
        created_at__date__gte=month_start,
        created_at__date__lt=next_month,
    )
    purchases = Purchase.objects.filter(
        purchased_at__gte=month_start,
        purchased_at__lt=next_month,
    ).select_related("supplier", "created_by")

    total_amount = transactions.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"] + sales_orders.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"]
    total_paid = transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"] + sales_orders.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]
    total_due = total_amount - total_paid
    tailoring_transactions = transactions.filter(transaction_type=Transaction.TYPE_TAILORING)
    rent_transactions = transactions.filter(transaction_type=Transaction.TYPE_RENT)
    sale_transactions = transactions.filter(transaction_type=Transaction.TYPE_SALE)
    purchase_total = purchases.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"]
    purchase_paid = purchases.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]

    breakdown = list(
        transactions.values("transaction_type")
        .annotate(
            total=Coalesce(Sum("amount"), Decimal("0.00")),
            paid=Coalesce(Sum("paid_amount"), Decimal("0.00")),
            count=Count("id"),
        )
        .order_by("transaction_type")
    )
    type_labels = dict(Transaction.TYPE_CHOICES)
    for row in breakdown:
        row["label"] = type_labels.get(row["transaction_type"], row["transaction_type"])

    context = {
        "selected_year": selected_year,
        "selected_month": selected_month,
        "month_choices": range(1, 13),
        "year_choices": range(today.year - 2, today.year + 3),
        "stats": {
            "total_amount": total_amount,
            "total_paid": total_paid,
            "total_due": total_due,
            "invoice_count": transactions.count() + sales_orders.count(),
            "tailoring_income": tailoring_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
            "rent_income": rent_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
            "sale_invoice_income": sale_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
            "sales_orders_income": sales_orders.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
            "purchase_total": purchase_total,
            "purchase_paid": purchase_paid,
            "purchase_due": purchase_total - purchase_paid,
            "purchase_count": purchases.count(),
        },
        "breakdown": breakdown,
        "recent_month_transactions": transactions.order_by("-created_at")[:10],
        "recent_month_purchases": purchases.order_by("-created_at")[:10],
    }
    return render(request, "monthly_statistics.html", context)


@login_required
def deliveries_today(request):
    selected_date_raw = request.GET.get("date")
    delivery_type = request.GET.get("type", "all").strip()
    today = timezone.localdate()
    tomorrow = today + timezone.timedelta(days=1)
    selected_date = parse_date(selected_date_raw) if selected_date_raw else today
    if selected_date is None:
        selected_date = today

    rent_deliveries = Transaction.objects.filter(
        transaction_type=Transaction.TYPE_RENT,
        status=Transaction.STATUS_ACTIVE,
        returned_at__isnull=True,
        rent_start_date=selected_date,
    )
    tailoring_deliveries = Transaction.objects.filter(
        transaction_type=Transaction.TYPE_TAILORING,
        delivery_date=selected_date,
    )

    if delivery_type == Transaction.TYPE_RENT:
        deliveries = rent_deliveries
    elif delivery_type == Transaction.TYPE_TAILORING:
        deliveries = tailoring_deliveries
    else:
        deliveries = rent_deliveries | tailoring_deliveries

    deliveries = deliveries.select_related("customer", "dress").order_by("transaction_type", "created_at")

    return render(
        request,
        "deliveries.html",
        {
            "deliveries": deliveries,
            "selected_date": selected_date,
            "deliveries_count": deliveries.count(),
            "today": today,
            "tomorrow": tomorrow,
            "selected_type": delivery_type,
            "delivery_type_choices": [
                ("all", "الكل"),
                (Transaction.TYPE_RENT, "تسليمات الإيجار"),
                (Transaction.TYPE_TAILORING, "تسليمات التفصيل"),
            ],
        },
    )


@login_required
def invoice_list(request):
    query = request.GET.get("q", "").strip()
    invoices = Transaction.objects.select_related("customer", "dress", "created_by").order_by("-created_at")

    if query:
        invoices = invoices.filter(
            Q(invoice_number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__phone__icontains=query)
            | Q(dress__name__icontains=query)
            | Q(dress__code__icontains=query)
            | Q(created_by__username__icontains=query)
            | Q(description__icontains=query)
        )

    paginator = Paginator(invoices, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "invoices.html",
        {
            "invoices": page_obj,
            "page_obj": page_obj,
            "search_query": query,
            "results_count": paginator.count,
        },
    )


@login_required
def sales_order_list(request):
    query = request.GET.get("q", "").strip()
    orders = SalesOrder.objects.select_related("customer", "created_by").order_by("-ordered_at", "-created_at")
    if query:
        orders = orders.filter(
            Q(invoice_number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__phone__icontains=query)
            | Q(notes__icontains=query)
        )
    paginator = Paginator(orders, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "sales_orders.html", {"orders": page_obj, "page_obj": page_obj, "search_query": query, "results_count": paginator.count})


@login_required
def create_sales_order(request):
    if request.method == "POST":
        form = SalesOrderForm(request.POST)
        items, items_error = _parse_sales_order_items(request.POST)
        if form.is_valid() and not items_error:
            sales_order = SalesOrder.objects.create(
                customer=form.cleaned_data["customer"],
                created_by=request.user,
                notes=form.cleaned_data["notes"],
                ordered_at=form.cleaned_data["ordered_at"],
            )
            for item in items:
                SalesOrderItem.objects.create(sales_order=sales_order, **item)
            _sync_sales_order_totals(sales_order)
            initial_paid = form.cleaned_data["initial_paid_amount"]
            if initial_paid > sales_order.total_amount:
                sales_order.delete()
                form.add_error("initial_paid_amount", "لا يمكن أن يكون المدفوع أكبر من إجمالي فاتورة المبيعات.")
            else:
                if initial_paid > 0:
                    _create_sales_order_payment(sales_order, initial_paid, request.user, "دفعة أولى من العميل")
                messages.success(request, "تم إنشاء فاتورة المبيعات بنجاح.")
                return redirect("sales_order_detail", sales_order_id=sales_order.id)
        if items_error:
            messages.error(request, items_error)
    else:
        form = SalesOrderForm(initial={"ordered_at": timezone.localdate(), "initial_paid_amount": 0})
    return render(
        request,
        "sales_order_form.html",
        {
            "form": form,
            "item_rows": _sales_order_form_rows(request.POST if request.method == "POST" else None),
        },
    )


@login_required
def sales_order_detail(request, sales_order_id):
    sales_order = get_object_or_404(
        SalesOrder.objects.select_related("customer", "created_by").prefetch_related("items", "payments__created_by"),
        id=sales_order_id,
    )
    return render(request, "sales_order_detail.html", {"sales_order": sales_order, "payment_form": PaymentForm()})


@login_required
def add_sales_order_payment(request, sales_order_id):
    sales_order = get_object_or_404(SalesOrder, id=sales_order_id)
    if request.method != "POST":
        return redirect("sales_order_detail", sales_order_id=sales_order.id)
    form = PaymentForm(request.POST)
    if form.is_valid():
        if form.cleaned_data["amount"] > sales_order.balance:
            messages.error(request, "لا يمكن أن تتجاوز الدفعة إجمالي المتبقي على العميل.")
        else:
            _create_sales_order_payment(sales_order, form.cleaned_data["amount"], request.user, form.cleaned_data["notes"] or "دفعة على فاتورة المبيعات")
            messages.success(request, "تم تسجيل الدفعة.")
    else:
        messages.error(request, "راجع مبلغ الدفعة ثم حاول مرة أخرى.")
    return redirect("sales_order_detail", sales_order_id=sales_order.id)


@login_required
def sales_customer_list(request):
    query = request.GET.get("q", "").strip()
    today = timezone.localdate()
    try:
        selected_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        selected_year = today.year
    try:
        selected_month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        selected_month = today.month
    if selected_month < 0 or selected_month > 12:
        selected_month = today.month

    customer_form = SalesCustomerForm(request.POST or None)
    if request.method == "POST" and customer_form.is_valid():
        SalesCustomer.objects.create(
            name=customer_form.cleaned_data["name"],
            phone=customer_form.cleaned_data["phone"],
            notes=customer_form.cleaned_data["notes"],
        )
        messages.success(request, "تمت إضافة العميل بنجاح.")
        return redirect("sales_customer_list")

    customers = SalesCustomer.objects.all().order_by("name")
    if query:
        customers = customers.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(notes__icontains=query))
    for customer in customers:
        period_orders = customer.sales_orders.filter(ordered_at__year=selected_year)
        period_payments = Payment.objects.filter(sales_order__customer=customer, created_at__year=selected_year)
        if selected_month:
            period_orders = period_orders.filter(ordered_at__month=selected_month)
            period_payments = period_payments.filter(created_at__month=selected_month)
        customer.orders_count = period_orders.count()
        customer.total_amount = period_orders.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"]
        customer.paid_amount = period_payments.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
        customer.due_amount = customer.total_amount - customer.paid_amount
    return render(
        request,
        "sales_customers.html",
        {
            "customers": customers,
            "search_query": query,
            "customer_form": customer_form,
            "selected_year": selected_year,
            "selected_month": selected_month,
            "month_choices": range(1, 13),
            "year_choices": range(today.year - 2, today.year + 3),
        },
    )


@login_required
def sales_customer_detail(request, customer_id):
    customer = get_object_or_404(SalesCustomer, id=customer_id)
    today = timezone.localdate()
    try:
        selected_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        selected_year = today.year
    try:
        selected_month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        selected_month = today.month
    if selected_month < 0 or selected_month > 12:
        selected_month = today.month
    orders = customer.sales_orders.prefetch_related("items").filter(ordered_at__year=selected_year)
    payments_query = Payment.objects.filter(sales_order__customer=customer, created_at__year=selected_year)
    if selected_month:
        orders = orders.filter(ordered_at__month=selected_month)
        payments_query = payments_query.filter(created_at__month=selected_month)
    orders = orders.order_by("-ordered_at", "-created_at")
    totals = _customer_sales_totals(customer)
    if request.method == "POST":
        payment_form = PaymentForm(request.POST)
        if payment_form.is_valid():
            amount = payment_form.cleaned_data["amount"]
            if amount > totals["due_amount"]:
                payment_form.add_error("amount", f"لا يمكن أن تتجاوز الدفعة المتبقي على العميل وهو {totals['due_amount']}.")
            else:
                recorded_amount = _record_customer_sales_payment(customer, amount, request.user, payment_form.cleaned_data["notes"])
                if recorded_amount <= 0:
                    payment_form.add_error("amount", "لا يوجد مبلغ مستحق لتسجيل هذه الدفعة.")
                else:
                    messages.success(request, "تم توزيع الدفعة على أقدم فواتير العميل.")
                    return redirect("sales_customer_detail", customer_id=customer.id)
    else:
        payment_form = PaymentForm()
    payments = payments_query.select_related("sales_order", "created_by").order_by("-created_at")
    period_totals = {
        "total_amount": orders.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))["total"],
        "paid_amount": payments.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"],
    }
    period_totals["due_amount"] = period_totals["total_amount"] - period_totals["paid_amount"]
    return render(
        request,
        "sales_customer_detail.html",
        {
            "customer": customer,
            "orders": orders,
            "totals": totals,
            "period_totals": period_totals,
            "payment_form": payment_form,
            "payments": payments[:5],
            "payments_count": payments.count(),
            "selected_year": selected_year,
            "selected_month": selected_month,
            "month_choices": range(1, 13),
            "year_choices": range(today.year - 2, today.year + 3),
        },
    )


@login_required
def supplier_list(request):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    form = SupplierForm(request.POST or None)
    query = request.GET.get("q", "").strip()
    suppliers = Supplier.objects.annotate(
        purchases_count=Count("purchases", distinct=True),
        total_amount=Coalesce(Sum("purchases__total_amount"), Decimal("0.00")),
        paid_amount=Coalesce(Sum("purchases__paid_amount"), Decimal("0.00")),
    ).order_by("name")
    if query:
        suppliers = suppliers.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(notes__icontains=query))

    if request.method == "POST" and form.is_valid():
        Supplier.objects.create(
            name=form.cleaned_data["name"],
            phone=form.cleaned_data["phone"],
            notes=form.cleaned_data["notes"],
        )
        messages.success(request, "تمت إضافة المورد بنجاح.")
        return redirect("supplier_list")

    for supplier in suppliers:
        supplier.due_amount = supplier.total_amount - supplier.paid_amount

    return render(
        request,
        "suppliers.html",
        {
            "form": form,
            "suppliers": suppliers,
            "search_query": query,
        },
    )


@login_required
def supplier_detail(request, supplier_id):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    supplier = get_object_or_404(Supplier, id=supplier_id)
    today = timezone.localdate()
    try:
        selected_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        selected_year = today.year
    try:
        selected_month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        selected_month = today.month

    if selected_month < 0 or selected_month > 12:
        selected_month = today.month

    supplier_purchases = supplier.purchases.select_related("created_by").prefetch_related("items")
    supplier_payments = Payment.objects.filter(purchase__supplier=supplier).select_related("purchase", "created_by")

    if selected_month:
        supplier_purchases = supplier_purchases.filter(
            purchased_at__year=selected_year,
            purchased_at__month=selected_month,
        )
        supplier_payments = supplier_payments.filter(
            created_at__year=selected_year,
            created_at__month=selected_month,
        )
    else:
        supplier_purchases = supplier_purchases.filter(purchased_at__year=selected_year)
        supplier_payments = supplier_payments.filter(created_at__year=selected_year)

    supplier_purchases = supplier_purchases.order_by("-purchased_at", "-created_at")
    supplier_payments = supplier_payments.order_by("-created_at")
    totals = _supplier_totals(supplier)

    if request.method == "POST":
        payment_form = PaymentForm(request.POST)
        if payment_form.is_valid():
            amount = payment_form.cleaned_data["amount"]
            if amount > totals["due_amount"]:
                payment_form.add_error("amount", "لا يمكن أن تتجاوز الدفعة إجمالي المتبقي على المورد.")
            else:
                distributed_amount = _record_supplier_payment(
                    supplier,
                    amount,
                    request.user,
                    payment_form.cleaned_data["notes"] or "دفعة من حساب المورد",
                )
                if distributed_amount <= 0:
                    payment_form.add_error("amount", "لا توجد فواتير مفتوحة لهذا المورد.")
                else:
                    messages.success(request, "تم تسجيل الدفعة وتوزيعها على فواتير المورد.")
                    return redirect("supplier_detail", supplier_id=supplier.id)
    else:
        payment_form = PaymentForm()

    return render(
        request,
        "supplier_detail.html",
        {
            "supplier": supplier,
            "purchases": supplier_purchases,
            "payments": supplier_payments[:5],
            "payments_count": supplier_payments.count(),
            "payment_form": payment_form,
            "totals": totals,
            "selected_month": selected_month,
            "selected_year": selected_year,
            "month_choices": range(0, 13),
            "year_choices": range(today.year - 2, today.year + 3),
        },
    )


@login_required
def supplier_payments(request, supplier_id):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    supplier = get_object_or_404(Supplier, id=supplier_id)
    today = timezone.localdate()
    try:
        selected_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        selected_year = today.year
    try:
        selected_month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        selected_month = today.month

    if selected_month < 0 or selected_month > 12:
        selected_month = today.month

    payments = Payment.objects.filter(purchase__supplier=supplier).select_related("purchase", "created_by")
    if selected_month:
        payments = payments.filter(created_at__year=selected_year, created_at__month=selected_month)
    else:
        payments = payments.filter(created_at__year=selected_year)

    payments = payments.order_by("-created_at")

    return render(
        request,
        "supplier_payments.html",
        {
            "supplier": supplier,
            "payments": payments,
            "payments_count": payments.count(),
            "selected_month": selected_month,
            "selected_year": selected_year,
            "month_choices": range(0, 13),
            "year_choices": range(today.year - 2, today.year + 3),
        },
    )


@login_required
def expense_list(request):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    today = timezone.localdate()
    try:
        selected_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        selected_year = today.year
    try:
        selected_month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        selected_month = today.month

    if selected_month < 0 or selected_month > 12:
        selected_month = today.month

    selected_type = request.GET.get("type", "all").strip() or "all"

    expenses = Expense.objects.select_related("created_by")
    today_expenses = Expense.objects.select_related("created_by").filter(spent_at=today).order_by("-created_at")
    if selected_month:
        expenses = expenses.filter(spent_at__year=selected_year, spent_at__month=selected_month)
    else:
        expenses = expenses.filter(spent_at__year=selected_year)

    if selected_type in {Expense.TYPE_RENT, Expense.TYPE_TAILORING}:
        expenses = expenses.filter(expense_type=selected_type)
    else:
        selected_type = "all"

    expenses = expenses.order_by("-spent_at", "-created_at")

    if request.method == "POST":
        form = ExpenseForm(request.POST)
        if form.is_valid():
            Expense.objects.create(
                expense_type=form.cleaned_data["expense_type"],
                title=form.cleaned_data["title"],
                amount=form.cleaned_data["amount"],
                spent_at=form.cleaned_data["spent_at"],
                notes=form.cleaned_data["notes"],
                created_by=request.user,
            )
            messages.success(request, "تم تسجيل المصروف بنجاح.")
            return redirect("expense_list")
    else:
        form = ExpenseForm(initial={"spent_at": today})

    rent_total = expenses.filter(expense_type=Expense.TYPE_RENT).aggregate(
        total=Coalesce(Sum("amount"), Decimal("0.00"))
    )["total"]
    tailoring_total = expenses.filter(expense_type=Expense.TYPE_TAILORING).aggregate(
        total=Coalesce(Sum("amount"), Decimal("0.00"))
    )["total"]
    today_rent_total = today_expenses.filter(expense_type=Expense.TYPE_RENT).aggregate(
        total=Coalesce(Sum("amount"), Decimal("0.00"))
    )["total"]
    today_tailoring_total = today_expenses.filter(expense_type=Expense.TYPE_TAILORING).aggregate(
        total=Coalesce(Sum("amount"), Decimal("0.00"))
    )["total"]

    return render(
        request,
        "expenses.html",
        {
            "form": form,
            "expenses": expenses[:5],
            "expenses_count": expenses.count(),
            "today_expenses": today_expenses[:5],
            "today_expenses_count": today_expenses.count(),
            "today_date": today,
            "selected_month": selected_month,
            "selected_year": selected_year,
            "selected_type": selected_type,
            "month_choices": range(0, 13),
            "year_choices": range(today.year - 2, today.year + 3),
            "type_choices": [
                ("all", "كل المصروفات"),
                (Expense.TYPE_RENT, "مصروفات الأتيليه"),
                (Expense.TYPE_TAILORING, "مصروفات المصنع"),
            ],
            "stats": {
                "rent_total": rent_total,
                "tailoring_total": tailoring_total,
                "all_total": rent_total + tailoring_total,
                "count": expenses.count(),
                "today_rent_total": today_rent_total,
                "today_tailoring_total": today_tailoring_total,
                "today_all_total": today_rent_total + today_tailoring_total,
                "today_count": today_expenses.count(),
            },
        },
    )


@login_required
def today_expense_list(request):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    today = timezone.localdate()
    expenses = Expense.objects.select_related("created_by").filter(spent_at=today).order_by("-created_at")
    return render(
        request,
        "today_expenses.html",
        {
            "expenses": expenses,
            "expenses_count": expenses.count(),
            "today_date": today,
        },
    )


@login_required
def expense_archive_list(request):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    today = timezone.localdate()
    try:
        selected_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        selected_year = today.year
    try:
        selected_month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        selected_month = today.month

    if selected_month < 0 or selected_month > 12:
        selected_month = today.month

    selected_type = request.GET.get("type", "all").strip() or "all"
    expenses = Expense.objects.select_related("created_by")
    if selected_month:
        expenses = expenses.filter(spent_at__year=selected_year, spent_at__month=selected_month)
    else:
        expenses = expenses.filter(spent_at__year=selected_year)

    if selected_type in {Expense.TYPE_RENT, Expense.TYPE_TAILORING}:
        expenses = expenses.filter(expense_type=selected_type)
    else:
        selected_type = "all"

    expenses = expenses.order_by("-spent_at", "-created_at")
    return render(
        request,
        "expense_archive.html",
        {
            "expenses": expenses,
            "expenses_count": expenses.count(),
            "selected_month": selected_month,
            "selected_year": selected_year,
            "selected_type": selected_type,
            "month_choices": range(0, 13),
            "year_choices": range(today.year - 2, today.year + 3),
            "type_choices": [
                ("all", "كل المصروفات"),
                (Expense.TYPE_RENT, "مصروفات الأتيليه"),
                (Expense.TYPE_TAILORING, "مصروفات المصنع"),
            ],
        },
    )


@login_required
def delete_supplier(request, supplier_id):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")
    if request.method != "POST":
        return redirect("supplier_list")

    supplier = get_object_or_404(Supplier, id=supplier_id)
    if supplier.purchases.exists():
        messages.error(request, "لا يمكن حذف المورد لأنه مرتبط بفواتير شراء. احذف الفواتير أولًا.")
        return redirect("supplier_detail", supplier_id=supplier.id)

    supplier_name = supplier.name
    supplier.delete()
    messages.success(request, f"تم حذف المورد {supplier_name} بنجاح.")
    return redirect("supplier_list")


@login_required
def purchase_list(request):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    query = request.GET.get("q", "").strip()
    purchases = Purchase.objects.select_related("supplier", "created_by").order_by("-purchased_at", "-created_at")
    if query:
        purchases = purchases.filter(
            Q(invoice_number__icontains=query)
            | Q(supplier__name__icontains=query)
            | Q(notes__icontains=query)
            | Q(created_by__username__icontains=query)
        )

    paginator = Paginator(purchases, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "purchases.html",
        {
            "purchases": page_obj,
            "page_obj": page_obj,
            "search_query": query,
            "results_count": paginator.count,
        },
    )


@login_required
def create_purchase(request):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    if request.method == "POST":
        form = PurchaseForm(request.POST)
        items, items_error = _parse_purchase_items(request.POST)
        if form.is_valid() and not items_error:
            purchase = Purchase.objects.create(
                supplier=form.cleaned_data["supplier"],
                created_by=request.user,
                notes=form.cleaned_data["notes"],
                purchased_at=form.cleaned_data["purchased_at"],
            )
            for item in items:
                PurchaseItem.objects.create(purchase=purchase, **item)
            _sync_purchase_totals(purchase)
            initial_paid = form.cleaned_data["initial_paid_amount"]
            if initial_paid > purchase.total_amount:
                purchase.delete()
                form.add_error("initial_paid_amount", "لا يمكن أن يكون المدفوع أكبر من إجمالي فاتورة الشراء.")
            else:
                if initial_paid > 0:
                    Payment.objects.create(
                        purchase=purchase,
                        direction=Payment.DIRECTION_OUT,
                        amount=initial_paid,
                        notes="دفعة أولى للمورد",
                        created_by=request.user,
                    )
                    purchase.paid_amount = (purchase.paid_amount or Decimal("0.00")) + initial_paid
                    _sync_purchase_totals(purchase)
                messages.success(request, "تم إنشاء فاتورة شراء جديدة.")
                return redirect("purchase_detail", purchase_id=purchase.id)

        if items_error:
            messages.error(request, items_error)
    else:
        form = PurchaseForm(initial={"purchased_at": timezone.localdate(), "initial_paid_amount": 0})

    return render(
        request,
        "purchase_form.html",
        {
            "form": form,
            "item_rows": _purchase_form_rows(request.POST if request.method == "POST" else None),
            "page_title": "فاتورة شراء جديدة",
            "submit_label": "حفظ فاتورة الشراء",
            "purchase": None,
        },
    )


@login_required
def edit_purchase(request, purchase_id):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    purchase = get_object_or_404(Purchase.objects.select_related("supplier").prefetch_related("items", "payments"), id=purchase_id)
    existing_payments_total = purchase.payments.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]

    if request.method == "POST":
        form = PurchaseForm(request.POST)
        items, items_error = _parse_purchase_items(request.POST)
        if form.is_valid() and not items_error:
            purchase.supplier = form.cleaned_data["supplier"]
            purchase.notes = form.cleaned_data["notes"]
            purchase.purchased_at = form.cleaned_data["purchased_at"]
            purchase.save(update_fields=["supplier", "notes", "purchased_at", "updated_at"])

            purchase.items.all().delete()
            for item in items:
                PurchaseItem.objects.create(purchase=purchase, **item)

            _sync_purchase_totals(purchase)
            desired_paid = form.cleaned_data["initial_paid_amount"]
            if desired_paid > purchase.total_amount:
                form.add_error("initial_paid_amount", "لا يمكن أن يكون المدفوع أكبر من إجمالي فاتورة الشراء.")
            elif desired_paid < existing_payments_total:
                form.add_error("initial_paid_amount", "لا يمكن أن يكون المدفوع أقل من إجمالي الدفعات المسجلة.")
            else:
                purchase.paid_amount = desired_paid
                purchase.status = (
                    Purchase.STATUS_PAID
                    if purchase.paid_amount >= purchase.total_amount and purchase.total_amount > 0
                    else Purchase.STATUS_OPEN
                )
                purchase.save(update_fields=["paid_amount", "status", "updated_at"])
                messages.success(request, "تم تعديل فاتورة الشراء بنجاح.")
                return redirect("purchase_detail", purchase_id=purchase.id)

        if items_error:
            messages.error(request, items_error)
    else:
        form = PurchaseForm(
            initial={
                "supplier": purchase.supplier_id,
                "purchased_at": purchase.purchased_at,
                "initial_paid_amount": purchase.paid_amount,
                "notes": purchase.notes,
            }
        )

    item_rows = (
        _purchase_form_rows(request.POST)
        if request.method == "POST"
        else [
            {
                "item_name": item.item_name,
                "item_quantity": item.quantity,
                "item_unit_cost": item.unit_cost,
                "item_notes": item.notes,
            }
            for item in purchase.items.all()
        ]
    )

    return render(
        request,
        "purchase_form.html",
        {
            "form": form,
            "item_rows": item_rows or _purchase_form_rows(None),
            "page_title": f"تعديل فاتورة الشراء {purchase.invoice_number}",
            "submit_label": "حفظ التعديلات",
            "purchase": purchase,
        },
    )


@login_required
def delete_purchase(request, purchase_id):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")
    if request.method != "POST":
        return redirect("purchase_detail", purchase_id=purchase_id)

    purchase = get_object_or_404(Purchase.objects.select_related("supplier"), id=purchase_id)
    supplier_id = purchase.supplier_id
    purchase.delete()
    messages.success(request, "تم حذف فاتورة الشراء.")
    if supplier_id:
        return redirect("supplier_detail", supplier_id=supplier_id)
    return redirect("purchase_list")


@login_required
def purchase_detail(request, purchase_id):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    purchase = get_object_or_404(
        Purchase.objects.select_related("supplier", "created_by").prefetch_related("items", "payments__created_by"),
        id=purchase_id,
    )

    return render(
        request,
        "purchase_detail.html",
        {
            "purchase": purchase,
        },
    )


@login_required
def edit_supplier_payment(request, payment_id):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")

    payment = get_object_or_404(
        Payment.objects.select_related("purchase__supplier", "created_by"),
        id=payment_id,
        purchase__isnull=False,
    )
    purchase = payment.purchase
    payments_total = purchase.payments.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
    legacy_paid = max((purchase.paid_amount or Decimal("0.00")) - payments_total, Decimal("0.00"))
    other_payments_total = purchase.payments.exclude(id=payment.id).aggregate(
        total=Coalesce(Sum("amount"), Decimal("0.00"))
    )["total"]

    if request.method == "POST":
        form = PaymentForm(request.POST)
        if form.is_valid():
            new_amount = form.cleaned_data["amount"]
            if legacy_paid + other_payments_total + new_amount > purchase.total_amount:
                form.add_error("amount", "لا يمكن أن تتجاوز الدفعات إجمالي فاتورة الشراء.")
            else:
                purchase.paid_amount = (purchase.paid_amount or Decimal("0.00")) - payment.amount + new_amount
                payment.amount = new_amount
                payment.notes = form.cleaned_data["notes"]
                payment.save(update_fields=["amount", "notes"])
                _sync_purchase_totals(purchase)
                messages.success(request, "تم تعديل الدفعة بنجاح.")
                return redirect("supplier_payments", supplier_id=purchase.supplier_id)
    else:
        form = PaymentForm(initial={"amount": payment.amount, "notes": payment.notes})

    return render(
        request,
        "payment_form.html",
        {
            "form": form,
            "page_title": "تعديل دفعة المورد",
            "payment": payment,
            "cancel_url": "supplier_payments",
            "cancel_id": purchase.supplier_id,
        },
    )


@login_required
def delete_supplier_payment(request, payment_id):
    if not _user_can_view_financials(request.user):
        return HttpResponseForbidden("غير مسموح")
    if request.method != "POST":
        return redirect("purchase_list")

    payment = get_object_or_404(
        Payment.objects.select_related("purchase__supplier"),
        id=payment_id,
        purchase__isnull=False,
    )
    purchase = payment.purchase
    supplier_id = purchase.supplier_id
    purchase.paid_amount = max((purchase.paid_amount or Decimal("0.00")) - payment.amount, Decimal("0.00"))
    payment.delete()
    _sync_purchase_totals(purchase)
    messages.success(request, "تم حذف الدفعة بنجاح.")
    if supplier_id:
        return redirect("supplier_payments", supplier_id=supplier_id)
    return redirect("purchase_detail", purchase_id=purchase.id)


@login_required
def user_list(request):
    if not _user_can_manage_users(request.user):
        return HttpResponseForbidden("غير مسموح")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        role = request.POST.get("role", "employee").strip()

        if not username or not password:
            messages.error(request, "اكتب اسم المستخدم وكلمة المرور.")
            return redirect("user_list")

        if User.objects.filter(username=username).exists():
            messages.error(request, "اسم المستخدم موجود بالفعل.")
            return redirect("user_list")

        user = User.objects.create_user(username=username, password=password)
        manager_group, _ = Group.objects.get_or_create(name="manager")
        employee_group, _ = Group.objects.get_or_create(name="employee")

        if role == "manager":
            user.is_staff = True
            user.save(update_fields=["is_staff"])
            user.groups.add(manager_group)
        else:
            user.is_staff = False
            user.save(update_fields=["is_staff"])
            user.groups.add(employee_group)

        messages.success(request, "تم إنشاء المستخدم بنجاح.")
        return redirect("user_list")

    users = User.objects.all().order_by("username")
    return render(request, "users.html", {"users": users})


@login_required
def delete_user(request, user_id):
    if not _user_can_manage_users(request.user):
        return HttpResponseForbidden("غير مسموح")

    if request.method != "POST":
        return redirect("user_list")

    user_to_delete = get_object_or_404(User, id=user_id)

    if user_to_delete == request.user:
        messages.error(request, "لا يمكن حذف المستخدم الحالي أثناء تسجيل الدخول.")
        return redirect("user_list")

    if user_to_delete.is_superuser:
        messages.error(request, "لا يمكن حذف حساب الأدمن الرئيسي من هنا.")
        return redirect("user_list")

    username = user_to_delete.username
    user_to_delete.delete()
    messages.success(request, f"تم حذف المستخدم {username} بنجاح.")
    return redirect("user_list")


@login_required
def change_password(request):
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "تم تغيير كلمة المرور بنجاح.")
            return redirect("dashboard")
    else:
        form = PasswordChangeForm(request.user)

    return render(request, "change_password.html", {"form": form})


@login_required
def laundry_list(request):
    records = (
        LaundryRecord.objects.filter(status=LaundryRecord.STATUS_PENDING)
        .select_related("dress", "transaction")
        .order_by("-created_at")
    )
    paginator = Paginator(records, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "laundry.html",
        {
            "laundry_records": page_obj,
            "page_obj": page_obj,
            "results_count": paginator.count,
        },
    )


@login_required
def current_rentals_list(request):
    today = timezone.localdate()
    rentals = (
        Transaction.objects.filter(
            transaction_type=Transaction.TYPE_RENT,
            status=Transaction.STATUS_ACTIVE,
            returned_at__isnull=True,
            rent_start_date__lte=today,
            expected_return_date__gte=today,
        )
        .select_related("customer", "dress")
        .order_by("expected_return_date", "dress__name")
    )
    paginator = Paginator(rentals, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "current_rentals.html",
        {
            "rentals": page_obj,
            "page_obj": page_obj,
            "results_count": paginator.count,
            "today": today,
        },
    )


@login_required
def dress_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    rent_bookings = Transaction.objects.filter(
        transaction_type=Transaction.TYPE_RENT,
        rent_start_date__isnull=False,
        expected_return_date__isnull=False,
    ).select_related("customer").order_by("rent_start_date", "expected_return_date")
    items_queryset = Dress.objects.prefetch_related(
        Prefetch("transactions", queryset=rent_bookings, to_attr="rent_bookings")
    ).order_by("name", "size", "color", "code")
    families = DressFamily.objects.prefetch_related(
        Prefetch("items", queryset=items_queryset, to_attr="prefetched_items")
    ).order_by("-created_at")
    status_choices = dict(Dress.STATUS_CHOICES)

    if query:
        families = families.filter(
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(items__code__icontains=query)
            | Q(items__size__icontains=query)
            | Q(items__color__icontains=query)
        )

    if status and status in status_choices:
        families = families.filter(items__status=status)

    families = families.distinct()
    paginator = Paginator(families, 6)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "dresses.html",
        {
            "dresses": page_obj,
            "page_obj": page_obj,
            "search_query": query,
            "selected_status": status,
            "status_choices": Dress.STATUS_CHOICES,
            "results_count": paginator.count,
        },
    )


@login_required
def add_dress(request):
    if request.method == "POST":
        variant_codes = request.POST.getlist("variant_code[]")
        variant_sizes = request.POST.getlist("variant_size[]")
        variant_colors = request.POST.getlist("variant_color[]")
        form_data = {
            "name": request.POST.get("name", ""),
            "rent_price": request.POST.get("rent_price", ""),
            "sell_price": request.POST.get("sell_price", ""),
            "tailoring_price": request.POST.get("tailoring_price", ""),
            "description": request.POST.get("description", ""),
            "code": request.POST.get("code", ""),
            "size": request.POST.get("size", ""),
            "color": request.POST.get("color", ""),
            "variant_rows": [
                {"code": code_value, "size": size_value, "color": color_value}
                for code_value, size_value, color_value in zip(
                    variant_codes,
                    variant_sizes + [""] * max(0, len(variant_codes) - len(variant_sizes)),
                    variant_colors + [""] * max(0, len(variant_codes) - len(variant_colors)),
                )
            ],
        }
        variants, error = _parse_dress_variants(
            request.POST.get("code"),
            request.POST.get("size"),
            request.POST.get("color"),
            variant_codes,
            variant_sizes,
            variant_colors,
        )
        if error:
            messages.error(request, error)
            return render(
                request,
                "dress_form.html",
                {
                    "page_title": "إضافة فستان",
                    "dress": None,
                    "form_data": form_data,
                },
            )

        family = DressFamily.objects.create(
            name=request.POST["name"],
            description=request.POST.get("description", ""),
            rent_price=_to_decimal(request.POST.get("rent_price")),
            sell_price=_to_decimal(request.POST.get("sell_price")),
            tailoring_price=_to_decimal(request.POST.get("tailoring_price")),
            image=convert_heif_to_jpeg(request.FILES.get("image")),
        )
        for variant in variants:
            Dress.objects.create(
                family=family,
                name=family.name,
                code=variant["code"],
                size=variant["size"],
                color=variant["color"],
                description=family.description,
                rent_price=family.rent_price,
                sell_price=family.sell_price,
                tailoring_price=family.tailoring_price,
                image=family.image,
            )
        messages.success(request, "تمت إضافة الفستان بنجاح.")
        return redirect("dashboard")

    return render(
        request,
        "dress_form.html",
        {"page_title": "إضافة فستان", "dress": None, "form_data": None},
    )


@login_required
def edit_dress(request, id):
    dress = get_object_or_404(Dress, id=id)
    family = dress.family

    if request.method == "POST":
        family.name = request.POST["name"]
        family.description = request.POST.get("description", "")
        family.rent_price = _to_decimal(request.POST.get("rent_price"))
        family.sell_price = _to_decimal(request.POST.get("sell_price"))
        family.tailoring_price = _to_decimal(request.POST.get("tailoring_price"))
        if request.FILES.get("image"):
            family.image = convert_heif_to_jpeg(request.FILES.get("image"))
        family.save()

        dress.code = request.POST["code"]
        dress.size = request.POST["size"]
        dress.color = request.POST.get("color", "")
        dress.save()
        _sync_family_items_from_family(family)
        messages.success(request, "تم تحديث بيانات الفستان.")
        return redirect("dashboard")

    return render(
        request,
        "dress_form.html",
        {
            "page_title": "تعديل فستان",
            "dress": dress,
            "family": family,
            "form_data": None,
        },
    )


@login_required
def delete_dress(request, id):
    dress = get_object_or_404(Dress, id=id)
    family = dress.family
    dress.delete()
    if family and not family.items.exists():
        family.delete()
    messages.success(request, "تم حذف الفستان.")
    return redirect("dashboard")


@login_required
def create_transaction(request, transaction_type):
    allowed_types = {
        Transaction.TYPE_RENT: "فاتورة إيجار",
        Transaction.TYPE_SALE: "فاتورة بيع",
        Transaction.TYPE_TAILORING: "فاتورة تفصيل",
    }
    if transaction_type not in allowed_types:
        return redirect("dashboard")

    dresses = Dress.objects.exclude(status=Dress.STATUS_SOLD).distinct().order_by("name")

    if request.method == "POST":
        form = TransactionForm(request.POST, request.FILES, transaction_type=transaction_type, dresses=dresses)
        if not form.is_valid():
            return render(
                request,
                "transaction_form.html",
                {
                    **_transaction_form_context(transaction_type, request),
                    "form": form,
                    "dresses": dresses,
                },
            )

        customer_name = form.cleaned_data["customer_name"].strip()
        customer_phone = form.cleaned_data["phone"]
        customer_notes = form.cleaned_data["customer_notes"].strip()
        amount = form.cleaned_data["amount"]
        paid_amount = form.cleaned_data["paid_amount"]
        description = form.cleaned_data["description"]
        rent_start_date = form.cleaned_data["rent_start_date"]
        expected_return_date = form.cleaned_data["expected_return_date"]
        delivery_date = form.cleaned_data["delivery_date"]
        customer = _get_or_create_customer(customer_name, customer_phone, customer_notes)
        dress = form.cleaned_data.get("dress_id")

        if dress:
            if transaction_type == Transaction.TYPE_RENT:
                if dress.status == Dress.STATUS_SOLD:
                    form.add_error("dress_id", "هذا الفستان تم بيعه ولا يمكن حجزه.")
                    return render(
                        request,
                        "transaction_form.html",
                        {
                            **_transaction_form_context(transaction_type, request),
                            "form": form,
                            "dresses": dresses,
                        },
                    )
                if _has_rent_overlap(dress, rent_start_date, expected_return_date):
                    form.add_error("rent_start_date", "هذا الفستان محجوز بالفعل في هذه المدة. اختر مواعيد مختلفة.")
                    form.add_error("expected_return_date", "هذا الفستان محجوز بالفعل في هذه المدة. اختر مواعيد مختلفة.")
                    return render(
                        request,
                        "transaction_form.html",
                        {
                            **_transaction_form_context(transaction_type, request),
                            "form": form,
                            "dresses": dresses,
                        },
                    )
            elif transaction_type == Transaction.TYPE_SALE:
                has_future_or_current_booking = dress.transactions.filter(
                    transaction_type=Transaction.TYPE_RENT,
                    status=Transaction.STATUS_ACTIVE,
                    returned_at__isnull=True,
                ).exists()
                if dress.status != Dress.STATUS_AVAILABLE or has_future_or_current_booking:
                    form.add_error("dress_id", "هذا الفستان غير متاح للبيع الآن.")
                    return render(
                        request,
                        "transaction_form.html",
                        {
                            **_transaction_form_context(transaction_type, request),
                            "form": form,
                            "dresses": dresses,
                        },
                    )
        transaction = Transaction.objects.create(
            transaction_type=transaction_type,
            customer=customer,
            dress=dress,
            created_by=request.user,
            description=description,
            amount=amount,
            paid_amount=0,
            security_deposit=Decimal("0.00"),
            bust=form.cleaned_data.get("bust"),
            shoulder=form.cleaned_data.get("shoulder"),
            waist=form.cleaned_data.get("waist"),
            side=form.cleaned_data.get("side"),
            sleeve=form.cleaned_data.get("sleeve"),
            waist_length=form.cleaned_data.get("waist_length"),
            skirt_length=form.cleaned_data.get("skirt_length"),
            full_length=form.cleaned_data.get("full_length"),
            tailoring_model_image=form.cleaned_data.get("tailoring_model_image"),
            rent_start_date=rent_start_date,
            expected_return_date=expected_return_date,
            delivery_date=delivery_date,
            status=Transaction.STATUS_ACTIVE,
        )

        if dress:
            if transaction_type == Transaction.TYPE_RENT:
                _refresh_dress_status(dress)
            elif transaction_type == Transaction.TYPE_SALE:
                dress.status = Dress.STATUS_SOLD
                transaction.status = Transaction.STATUS_COMPLETED
                transaction.save(update_fields=["status"])
            dress.save(update_fields=["status"])

        if transaction_type == Transaction.TYPE_TAILORING:
            transaction.status = Transaction.STATUS_ACTIVE
            transaction.save(update_fields=["status"])

        if paid_amount > 0:
            _create_transaction_payment(transaction, paid_amount, request.user, "المدفوع عند إنشاء الفاتورة")

        messages.success(request, f"تم إنشاء {allowed_types[transaction_type]} بنجاح.")
        return redirect("invoice_detail", transaction_id=transaction.id)

    form = TransactionForm(
        transaction_type=transaction_type,
        dresses=dresses,
        initial=_transaction_form_initial(transaction_type, request),
    )
    return render(
        request,
        "transaction_form.html",
        {
            **_transaction_form_context(transaction_type, request),
            "form": form,
            "dresses": dresses,
        },
    )


@login_required
def invoice_detail(request, transaction_id):
    transaction = get_object_or_404(
        Transaction.objects.select_related("customer", "dress", "created_by").prefetch_related("payments__created_by"),
        id=transaction_id,
    )
    return render(
        request,
        "invoice_detail.html",
        {
            "transaction": transaction,
            "payment_form": PaymentForm(),
            "security_deposit_form": SecurityDepositForm(initial={"security_deposit": transaction.security_deposit})
            if transaction.transaction_type == Transaction.TYPE_RENT
            and transaction.is_fully_paid
            and not transaction.is_security_deposit_returned
            else None,
        },
    )


@login_required
def add_transaction_payment(request, transaction_id):
    transaction = get_object_or_404(Transaction, id=transaction_id)
    if request.method != "POST":
        return redirect("invoice_detail", transaction_id=transaction.id)

    form = PaymentForm(request.POST)
    if form.is_valid():
        amount = form.cleaned_data["amount"]
        if transaction.paid_amount + amount > transaction.amount:
            messages.error(request, "لا يمكن أن تتجاوز المدفوعات إجمالي الفاتورة.")
        else:
            _create_transaction_payment(
                transaction,
                amount,
                request.user,
                form.cleaned_data["notes"] or "دفعة إضافية على الفاتورة",
            )
            messages.success(request, "تم تسجيل الدفعة على الفاتورة.")
    else:
        messages.error(request, "راجع مبلغ الدفعة ثم حاول مرة أخرى.")
    return redirect("invoice_detail", transaction_id=transaction.id)


@login_required
def settle_transaction_payment(request, transaction_id):
    transaction = get_object_or_404(Transaction, id=transaction_id)

    if transaction.paid_amount < transaction.amount:
        _create_transaction_payment(
            transaction,
            transaction.amount - transaction.paid_amount,
            request.user,
            "تحصيل باقي الفاتورة",
        )
        messages.success(request, "تم تحصيل باقي المبلغ وتحديث الفاتورة.")
    else:
        messages.success(request, "هذه الفاتورة مدفوعة بالكامل بالفعل.")

    return redirect("invoice_detail", transaction_id=transaction.id)


@login_required
def save_security_deposit(request, transaction_id):
    transaction = get_object_or_404(Transaction, id=transaction_id, transaction_type=Transaction.TYPE_RENT)
    if request.method != "POST":
        return redirect("invoice_detail", transaction_id=transaction.id)
    if transaction.is_security_deposit_returned:
        messages.error(request, "تم رد التأمين بالفعل، ولا يمكن تعديله.")
        return redirect("invoice_detail", transaction_id=transaction.id)
    if not transaction.is_fully_paid:
        messages.error(request, "يتم تسجيل التأمين بعد سداد فاتورة الإيجار بالكامل.")
        return redirect("invoice_detail", transaction_id=transaction.id)

    form = SecurityDepositForm(request.POST)
    if form.is_valid():
        transaction.security_deposit = form.cleaned_data["security_deposit"]
        if transaction.security_deposit_received_at is None:
            transaction.security_deposit_received_at = timezone.now()
        transaction.save(update_fields=["security_deposit", "security_deposit_received_at", "updated_at"])
        messages.success(request, "تم تسجيل التأمين. لا يدخل هذا المبلغ في حسابات الفاتورة أو الإيرادات.")
    else:
        messages.error(request, "أدخل قيمة صحيحة للتأمين.")
    return redirect("invoice_detail", transaction_id=transaction.id)


@login_required
def return_security_deposit(request, transaction_id):
    transaction = get_object_or_404(Transaction, id=transaction_id, transaction_type=Transaction.TYPE_RENT)
    if request.method != "POST":
        return redirect("invoice_detail", transaction_id=transaction.id)
    if not transaction.has_security_deposit:
        messages.error(request, "لا يوجد تأمين مسجل على هذه الفاتورة.")
    elif transaction.is_security_deposit_returned:
        messages.success(request, "تم رد التأمين بالفعل.")
    elif transaction.returned_at is None:
        messages.error(request, "سجل رجوع الفستان أولًا ثم قم برد التأمين.")
    else:
        transaction.security_deposit_returned_at = timezone.now()
        transaction.save(update_fields=["security_deposit_returned_at", "updated_at"])
        messages.success(request, "تم تسجيل رد التأمين للعميل.")
    return redirect("invoice_detail", transaction_id=transaction.id)


@login_required
def edit_transaction(request, transaction_id):
    transaction = get_object_or_404(
        Transaction.objects.select_related("customer", "dress"),
        id=transaction_id,
    )
    old_dress = transaction.dress
    dresses = Dress.objects.exclude(status=Dress.STATUS_SOLD)
    if transaction.dress_id:
        dresses = (dresses | Dress.objects.filter(id=transaction.dress_id)).distinct().order_by("name")
    else:
        dresses = dresses.distinct().order_by("name")

    if request.method == "POST":
        form = TransactionForm(request.POST, request.FILES, transaction_type=transaction.transaction_type, dresses=dresses)
        if not form.is_valid():
            return render(
                request,
                "transaction_form.html",
                {
                    **_transaction_form_context(transaction.transaction_type, request, transaction),
                    "form": form,
                    "dresses": dresses,
                },
            )

        customer_name = form.cleaned_data["customer_name"].strip()
        customer_phone = form.cleaned_data["phone"]
        customer_notes = form.cleaned_data["customer_notes"].strip()
        amount = form.cleaned_data["amount"]
        paid_amount = form.cleaned_data["paid_amount"]
        description = form.cleaned_data["description"]
        rent_start_date = form.cleaned_data["rent_start_date"]
        expected_return_date = form.cleaned_data["expected_return_date"]
        delivery_date = form.cleaned_data["delivery_date"]
        existing_paid_total = transaction.payments.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
        customer = _get_or_create_customer(customer_name, customer_phone, customer_notes)
        dress = form.cleaned_data.get("dress_id")

        if paid_amount < existing_paid_total:
            form.add_error("paid_amount", "لا يمكن تقليل المدفوع لأقل من المدفوعات المسجلة بالفعل.")
            return render(
                request,
                "transaction_form.html",
                {
                    **_transaction_form_context(transaction.transaction_type, request, transaction),
                    "form": form,
                    "dresses": dresses,
                },
            )

        if dress:
            if transaction.transaction_type == Transaction.TYPE_RENT:
                if dress.status == Dress.STATUS_SOLD and dress != old_dress:
                    form.add_error("dress_id", "هذا الفستان تم بيعه ولا يمكن حجزه.")
                    return render(
                        request,
                        "transaction_form.html",
                        {
                            **_transaction_form_context(transaction.transaction_type, request, transaction),
                            "form": form,
                            "dresses": dresses,
                        },
                    )
                if _has_rent_overlap_excluding(transaction, dress, rent_start_date, expected_return_date):
                    form.add_error("rent_start_date", "هذا الفستان محجوز بالفعل في هذه المدة. اختر مواعيد مختلفة.")
                    form.add_error("expected_return_date", "هذا الفستان محجوز بالفعل في هذه المدة. اختر مواعيد مختلفة.")
                    return render(
                        request,
                        "transaction_form.html",
                        {
                            **_transaction_form_context(transaction.transaction_type, request, transaction),
                            "form": form,
                            "dresses": dresses,
                        },
                    )
            elif transaction.transaction_type == Transaction.TYPE_SALE:
                has_other_booking = dress.transactions.exclude(id=transaction.id).filter(
                    transaction_type=Transaction.TYPE_RENT,
                    status=Transaction.STATUS_ACTIVE,
                    returned_at__isnull=True,
                ).exists()
                has_other_sale = dress.transactions.exclude(id=transaction.id).filter(
                    transaction_type=Transaction.TYPE_SALE,
                    status=Transaction.STATUS_COMPLETED,
                ).exists()
                if dress.status == Dress.STATUS_SOLD and dress != old_dress:
                    form.add_error("dress_id", "هذا الفستان مباع بالفعل.")
                    return render(
                        request,
                        "transaction_form.html",
                        {
                            **_transaction_form_context(transaction.transaction_type, request, transaction),
                            "form": form,
                            "dresses": dresses,
                        },
                    )
                if has_other_booking or has_other_sale:
                    form.add_error("dress_id", "هذا الفستان غير متاح للبيع الآن.")
                    return render(
                        request,
                        "transaction_form.html",
                        {
                            **_transaction_form_context(transaction.transaction_type, request, transaction),
                            "form": form,
                            "dresses": dresses,
                        },
                    )

        transaction.customer = customer
        transaction.dress = dress
        transaction.description = description
        transaction.amount = amount
        transaction.paid_amount = existing_paid_total
        transaction.bust = form.cleaned_data.get("bust")
        transaction.shoulder = form.cleaned_data.get("shoulder")
        transaction.waist = form.cleaned_data.get("waist")
        transaction.side = form.cleaned_data.get("side")
        transaction.sleeve = form.cleaned_data.get("sleeve")
        transaction.waist_length = form.cleaned_data.get("waist_length")
        transaction.skirt_length = form.cleaned_data.get("skirt_length")
        transaction.full_length = form.cleaned_data.get("full_length")
        if form.cleaned_data.get("tailoring_model_image"):
            transaction.tailoring_model_image = form.cleaned_data.get("tailoring_model_image")
        transaction.rent_start_date = rent_start_date
        transaction.expected_return_date = expected_return_date
        transaction.delivery_date = delivery_date

        if transaction.transaction_type == Transaction.TYPE_SALE:
            transaction.status = Transaction.STATUS_COMPLETED
        elif transaction.transaction_type == Transaction.TYPE_TAILORING:
            transaction.status = Transaction.STATUS_COMPLETED if existing_paid_total >= amount else Transaction.STATUS_ACTIVE

        transaction.save()

        if paid_amount > existing_paid_total:
            _create_transaction_payment(
                transaction,
                paid_amount - existing_paid_total,
                request.user,
                "تعديل رفع المدفوع في الفاتورة",
            )

        if old_dress and old_dress != dress:
            _refresh_dress_status(old_dress)
        if dress:
            _refresh_dress_status(dress)

        messages.success(request, "تم تعديل الفاتورة بنجاح.")
        return redirect("invoice_detail", transaction_id=transaction.id)

    return render(
        request,
        "transaction_form.html",
        {
            **_transaction_form_context(transaction.transaction_type, request, transaction),
            "form": TransactionForm(
                transaction_type=transaction.transaction_type,
                dresses=dresses,
                initial=_transaction_form_initial(transaction.transaction_type, request, transaction),
            ),
            "dresses": dresses,
        },
    )


@login_required
def delete_transaction(request, transaction_id):
    transaction = get_object_or_404(Transaction.objects.select_related("dress"), id=transaction_id)
    dress = transaction.dress

    if transaction.transaction_type == Transaction.TYPE_RENT:
        transaction.laundry_records.all().delete()

    transaction.delete()

    if dress:
        _refresh_dress_status(dress)

    messages.success(request, "تم حذف الفاتورة.")
    return redirect("dashboard")


@login_required
def return_rental(request, transaction_id):
    transaction = get_object_or_404(Transaction, id=transaction_id, transaction_type=Transaction.TYPE_RENT)
    if transaction.dress:
        send_to_laundry = request.POST.get("send_to_laundry") == "yes"
        transaction.returned_at = timezone.now()
        transaction.status = Transaction.STATUS_COMPLETED
        transaction.save(update_fields=["returned_at", "status"])

        if send_to_laundry:
            transaction.dress.status = Dress.STATUS_LAUNDRY
            transaction.dress.save(update_fields=["status"])
            LaundryRecord.objects.create(
                dress=transaction.dress,
                transaction=transaction,
                cost=_to_decimal(request.POST.get("laundry_cost")),
                notes=request.POST.get("laundry_notes", ""),
            )
        else:
            _refresh_dress_status(transaction.dress)

        messages.success(request, "تم تسجيل رجوع الفستان.")
    return redirect("dashboard")


@login_required
def complete_laundry(request, laundry_id):
    record = get_object_or_404(LaundryRecord.objects.select_related("dress"), id=laundry_id)
    record.status = LaundryRecord.STATUS_DONE
    record.finished_at = timezone.now()
    record.save(update_fields=["status", "finished_at"])
    _refresh_dress_status(record.dress)
    messages.success(request, "تم إنهاء الغسيل والفستان أصبح متاحًا.")
    return redirect("dashboard")
