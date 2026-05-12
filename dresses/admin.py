from django.contrib import admin

from .models import Customer, Dress, LaundryRecord, Transaction


@admin.register(Dress)
class DressAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "size", "status", "rent_price", "sell_price")
    list_filter = ("status", "size")
    search_fields = ("name", "code", "color")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
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
        "status",
        "created_at",
    )
    list_filter = ("transaction_type", "status", "created_at")
    search_fields = ("invoice_number", "customer__name", "dress__code")


@admin.register(LaundryRecord)
class LaundryRecordAdmin(admin.ModelAdmin):
    list_display = ("dress", "status", "cost", "created_at", "finished_at")
    list_filter = ("status",)
