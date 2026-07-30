from django.contrib import admin

from .models import Charge, Payment, Receipt


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    autocomplete_fields = ("received_by",)


@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ("patient", "visit", "total", "amount_paid", "balance", "created_at")
    search_fields = ("patient__patient_id", "patient__first_name")
    autocomplete_fields = ("visit", "patient", "set_by")
    readonly_fields = ("created_at", "updated_at")
    inlines = [PaymentInline]

    @admin.display(description="Total")
    def total(self, obj):
        return obj.total

    @admin.display(description="Paid")
    def amount_paid(self, obj):
        return obj.amount_paid

    @admin.display(description="Balance")
    def balance(self, obj):
        return obj.balance


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("charge", "amount", "method", "received_by", "received_at")
    list_filter = ("method", "received_at")
    search_fields = ("charge__patient__patient_id", "reference")
    autocomplete_fields = ("charge", "received_by")


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("receipt_number", "payment", "issued_at", "printed_at")
    search_fields = ("receipt_number",)
    readonly_fields = ("receipt_number", "issued_at")
