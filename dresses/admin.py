from django.contrib import admin

from .models import Customer, Dress, DressFamily, LaundryRecord, SalesCustomer, SalesOrder, SalesOrderItem, Transaction


@admin.register(DressFamily)
class DressFamilyAdmin(admin.ModelAdmin):
    list_display = ("name", "rent_price", "sell_price", "tailoring_price", "created_at")
    search_fields = ("name", "description")


@admin.register(Dress)
class DressAdmin(admin.ModelAdmin):
    list_display = ("name", "family", "code", "size", "color", "status", "rent_price", "sell_price")
    list_filter = ("status", "size", "color")
    search_fields = ("name", "code", "color", "family__name")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "created_at")
    search_fields = ("name", "phone")


@admin.register(SalesCustomer)
class SalesCustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "created_at")
    search_fields = ("name", "phone")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "transaction_type",
        "customer",
        "dress",
        "amount",
        "paid_amount",
        "security_deposit",
        "status",
        "created_at",
    )
    list_filter = ("transaction_type", "status", "created_at")
    search_fields = ("invoice_number", "customer__name", "dress__code")


@admin.register(LaundryRecord)
class LaundryRecordAdmin(admin.ModelAdmin):
    list_display = ("dress", "status", "cost", "created_at", "finished_at")
    list_filter = ("status",)


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "customer", "total_amount", "paid_amount", "status", "ordered_at")
    list_filter = ("status", "ordered_at")
    search_fields = ("invoice_number", "customer__name", "customer__phone")


@admin.register(SalesOrderItem)
class SalesOrderItemAdmin(admin.ModelAdmin):
    list_display = ("sales_order", "dress_name", "quantity", "unit_price", "total_price")
    search_fields = ("dress_name", "sales_order__invoice_number")
