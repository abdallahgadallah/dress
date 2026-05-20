from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils import timezone

from .forms import TransactionForm
from .models import Customer, Dress, DressFamily, LaundryRecord, Transaction


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


def _dashboard_context():
    today = timezone.localdate()
    month_start = today.replace(day=1)

    transactions = Transaction.objects.select_related("customer", "dress")
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

    month_amount = month_transactions.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
    month_paid = month_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]

    stats = {
        "today_income": today_transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"],
        "today_sales_count": today_transactions.filter(transaction_type=Transaction.TYPE_SALE).count(),
        "today_rents_count": today_transactions.filter(transaction_type=Transaction.TYPE_RENT).count(),
        "today_tailoring_count": today_transactions.filter(transaction_type=Transaction.TYPE_TAILORING).count(),
        "month_income": month_paid,
        "month_total": month_amount,
        "month_due": month_amount - month_paid,
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

    total_amount = transactions.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
    total_paid = transactions.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]
    total_due = total_amount - total_paid

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
            "invoice_count": transactions.count(),
        },
        "breakdown": breakdown,
        "recent_month_transactions": transactions.order_by("-created_at")[:10],
    }
    return render(request, "monthly_statistics.html", context)


@login_required
def deliveries_today(request):
    selected_date_raw = request.GET.get("date")
    today = timezone.localdate()
    tomorrow = today + timezone.timedelta(days=1)
    selected_date = parse_date(selected_date_raw) if selected_date_raw else today
    if selected_date is None:
        selected_date = today

    deliveries = (
        Transaction.objects.filter(
            transaction_type=Transaction.TYPE_RENT,
            status=Transaction.STATUS_ACTIVE,
            returned_at__isnull=True,
            rent_start_date=selected_date,
        )
        .select_related("customer", "dress")
        .order_by("dress__name", "created_at")
    )

    return render(
        request,
        "deliveries.html",
        {
            "deliveries": deliveries,
            "selected_date": selected_date,
            "deliveries_count": deliveries.count(),
            "today": today,
            "tomorrow": tomorrow,
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
            image=request.FILES.get("image"),
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
            family.image = request.FILES.get("image")
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
        form = TransactionForm(request.POST, transaction_type=transaction_type, dresses=dresses)
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
            paid_amount=paid_amount,
            bust=form.cleaned_data.get("bust"),
            shoulder=form.cleaned_data.get("shoulder"),
            waist=form.cleaned_data.get("waist"),
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
            transaction.status = Transaction.STATUS_COMPLETED if paid_amount >= amount else Transaction.STATUS_ACTIVE
            transaction.save(update_fields=["status"])

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
        Transaction.objects.select_related("customer", "dress", "created_by"),
        id=transaction_id,
    )
    return render(request, "invoice_detail.html", {"transaction": transaction})


@login_required
def settle_transaction_payment(request, transaction_id):
    transaction = get_object_or_404(Transaction, id=transaction_id)

    if transaction.paid_amount < transaction.amount:
        transaction.paid_amount = transaction.amount
        if transaction.transaction_type in (Transaction.TYPE_SALE, Transaction.TYPE_TAILORING):
            transaction.status = Transaction.STATUS_COMPLETED
            transaction.save(update_fields=["paid_amount", "status"])
        else:
            transaction.save(update_fields=["paid_amount"])
        messages.success(request, "تم تحصيل باقي المبلغ وتحديث الفاتورة.")
    else:
        messages.success(request, "هذه الفاتورة مدفوعة بالكامل بالفعل.")

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
        form = TransactionForm(request.POST, transaction_type=transaction.transaction_type, dresses=dresses)
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
        customer = _get_or_create_customer(customer_name, customer_phone, customer_notes)
        dress = form.cleaned_data.get("dress_id")

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
        transaction.paid_amount = paid_amount
        transaction.bust = form.cleaned_data.get("bust")
        transaction.shoulder = form.cleaned_data.get("shoulder")
        transaction.waist = form.cleaned_data.get("waist")
        transaction.rent_start_date = rent_start_date
        transaction.expected_return_date = expected_return_date
        transaction.delivery_date = delivery_date

        if transaction.transaction_type == Transaction.TYPE_SALE:
            transaction.status = Transaction.STATUS_COMPLETED
        elif transaction.transaction_type == Transaction.TYPE_TAILORING:
            transaction.status = (
                Transaction.STATUS_COMPLETED if paid_amount >= amount else Transaction.STATUS_ACTIVE
            )

        transaction.save()

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
