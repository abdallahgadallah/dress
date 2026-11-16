from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Customer, Dress, DressFamily, Expense, LaundryRecord, Payment, Purchase, PurchaseItem, SalesCustomer, SalesOrder, Supplier, Transaction


class DashboardFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tester", password="Test@123")
        self.client.force_login(self.user)
        self.dress = Dress.objects.create(
            name="Satin Dress",
            code="D-100",
            size="M",
            rent_price=500,
            sell_price=2500,
            tailoring_price=800,
        )

    def test_create_sale_transaction_marks_dress_as_sold(self):
        response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_SALE]),
            {
                "customer_name": "Mona",
                "phone": "01000000001",
                "dress_id": self.dress.id,
                "amount": "2500",
                "paid_amount": "2500",
                "description": "بيع فستان",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.dress.refresh_from_db()
        transaction = Transaction.objects.get()
        self.assertEqual(self.dress.status, Dress.STATUS_SOLD)
        self.assertEqual(transaction.status, Transaction.STATUS_COMPLETED)

    def test_return_rental_creates_laundry_record(self):
        transaction = Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            amount=500,
            paid_amount=300,
            rent_start_date=timezone.localdate(),
            expected_return_date=timezone.localdate(),
        )
        self.dress.status = Dress.STATUS_RENTED
        self.dress.save()

        response = self.client.post(
            reverse("return_rental", args=[transaction.id]),
            {
                "send_to_laundry": "yes",
                "laundry_cost": "50",
                "laundry_notes": "بقعة بسيطة",
            },
        )

        self.assertEqual(response.status_code, 302)
        transaction.refresh_from_db()
        self.dress.refresh_from_db()
        self.assertEqual(transaction.status, Transaction.STATUS_COMPLETED)
        self.assertEqual(self.dress.status, Dress.STATUS_LAUNDRY)
        self.assertEqual(LaundryRecord.objects.count(), 1)

    def test_security_deposit_is_kept_outside_invoice_payments(self):
        transaction = Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            amount=500,
            paid_amount=500,
            rent_start_date=timezone.localdate(),
            expected_return_date=timezone.localdate(),
        )

        response = self.client.post(
            reverse("save_security_deposit", args=[transaction.id]),
            {"security_deposit": "250"},
        )

        self.assertEqual(response.status_code, 302)
        transaction.refresh_from_db()
        self.assertEqual(str(transaction.security_deposit), "250.00")
        self.assertEqual(str(transaction.paid_amount), "500.00")
        self.assertEqual(transaction.payments.count(), 0)

    def test_security_deposit_can_only_be_returned_after_dress_return(self):
        transaction = Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            amount=500,
            security_deposit=250,
            security_deposit_received_at=timezone.now(),
            rent_start_date=timezone.localdate(),
            expected_return_date=timezone.localdate(),
        )

        self.client.post(reverse("return_security_deposit", args=[transaction.id]))
        transaction.refresh_from_db()
        self.assertIsNone(transaction.security_deposit_returned_at)

        transaction.returned_at = timezone.now()
        transaction.save(update_fields=["returned_at"])
        self.client.post(reverse("return_security_deposit", args=[transaction.id]))
        transaction.refresh_from_db()
        self.assertIsNotNone(transaction.security_deposit_returned_at)

    def test_dress_search_filters_results(self):
        Dress.objects.create(
            name="Classic White",
            code="D-200",
            size="L",
            color="White",
            rent_price=300,
            sell_price=1800,
            tailoring_price=500,
        )

        response = self.client.get(reverse("dress_list"), {"q": "D-100"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Satin Dress")
        self.assertNotContains(response, "Classic White")

    def test_dress_status_filter_shows_only_matching_status(self):
        self.dress.status = Dress.STATUS_RENTED
        self.dress.save()
        Dress.objects.create(
            name="Blue Dress",
            code="D-300",
            size="S",
            status=Dress.STATUS_AVAILABLE,
            rent_price=400,
            sell_price=2000,
            tailoring_price=600,
        )

        response = self.client.get(reverse("dress_list"), {"status": Dress.STATUS_RENTED})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Satin Dress")
        self.assertNotContains(response, "Blue Dress")

    def test_dress_list_is_paginated(self):
        for index in range(7):
            Dress.objects.create(
                name=f"Extra Dress {index}",
                code=f"D-X{index}",
                size="M",
                rent_price=200,
                sell_price=1000,
                tailoring_price=300,
            )

        response = self.client.get(reverse("dress_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["dresses"]), 6)

    def test_add_dress_can_create_multiple_variants_under_one_family(self):
        response = self.client.post(
            reverse("add_dress"),
            {
                "name": "Royal Dress",
                "code": "RD-001",
                "size": "M",
                "color": "Red",
                "variant_code[]": ["RD-002", "RD-003"],
                "variant_size[]": ["L", "S"],
                "variant_color[]": ["Blue", "Green"],
                "rent_price": "700",
                "sell_price": "3200",
                "tailoring_price": "900",
                "description": "موديل واحد بعدة قطع",
            },
        )

        self.assertEqual(response.status_code, 302)
        family = DressFamily.objects.get(name="Royal Dress")
        self.assertEqual(family.items.count(), 3)
        self.assertTrue(Dress.objects.filter(code="RD-002", family=family).exists())
        self.assertTrue(Dress.objects.filter(code="RD-003", family=family).exists())

    def test_can_create_two_rent_invoices_for_different_dates(self):
        first_response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_RENT]),
            {
                "customer_name": "Sara",
                "phone": "01000000002",
                "dress_id": self.dress.id,
                "amount": "500",
                "paid_amount": "200",
                "rent_start_date": "2026-05-02",
                "expected_return_date": "2026-05-04",
                "description": "حجز أول",
            },
        )
        second_response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_RENT]),
            {
                "customer_name": "Nour",
                "phone": "01000000003",
                "dress_id": self.dress.id,
                "amount": "500",
                "paid_amount": "200",
                "rent_start_date": "2026-05-10",
                "expected_return_date": "2026-05-12",
                "description": "حجز ثاني",
            },
        )

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(Transaction.objects.filter(transaction_type=Transaction.TYPE_RENT).count(), 2)

    def test_can_rent_two_variants_of_same_family_on_same_day(self):
        family = DressFamily.objects.create(
            name="Shared Model",
            rent_price=600,
            sell_price=3000,
            tailoring_price=850,
        )
        first_item = Dress.objects.create(
            family=family,
            name="Shared Model",
            code="SM-001",
            size="M",
            color="Red",
            rent_price=600,
            sell_price=3000,
            tailoring_price=850,
        )
        second_item = Dress.objects.create(
            family=family,
            name="Shared Model",
            code="SM-002",
            size="L",
            color="Blue",
            rent_price=600,
            sell_price=3000,
            tailoring_price=850,
        )

        first_response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_RENT]),
            {
                "customer_name": "Aya",
                "phone": "01000000009",
                "dress_id": first_item.id,
                "amount": "600",
                "paid_amount": "200",
                "rent_start_date": "2026-05-20",
                "expected_return_date": "2026-05-21",
            },
        )
        second_response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_RENT]),
            {
                "customer_name": "Nesma",
                "phone": "01000000010",
                "dress_id": second_item.id,
                "amount": "600",
                "paid_amount": "200",
                "rent_start_date": "2026-05-20",
                "expected_return_date": "2026-05-21",
            },
        )

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(Transaction.objects.filter(transaction_type=Transaction.TYPE_RENT).count(), 2)

    def test_prevent_overlapping_rent_booking_for_same_dress(self):
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            amount=500,
            paid_amount=200,
            rent_start_date="2026-05-02",
            expected_return_date="2026-05-05",
        )

        response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_RENT]),
            {
                "customer_name": "Mariam",
                "phone": "01000000004",
                "dress_id": self.dress.id,
                "amount": "500",
                "paid_amount": "200",
                "rent_start_date": "2026-05-04",
                "expected_return_date": "2026-05-06",
                "description": "حجز متداخل",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "هذا الفستان محجوز بالفعل في هذه المدة")
        self.assertEqual(Transaction.objects.filter(transaction_type=Transaction.TYPE_RENT).count(), 1)

    def test_edit_sale_invoice_updates_dress_statuses(self):
        second_dress = Dress.objects.create(
            name="Rose Dress",
            code="D-400",
            size="L",
            rent_price=600,
            sell_price=2600,
            tailoring_price=900,
        )
        sale = Transaction.objects.create(
            transaction_type=Transaction.TYPE_SALE,
            dress=self.dress,
            amount=2500,
            paid_amount=2500,
            status=Transaction.STATUS_COMPLETED,
        )
        self.dress.status = Dress.STATUS_SOLD
        self.dress.save()

        response = self.client.post(
            reverse("edit_transaction", args=[sale.id]),
            {
                "customer_name": "Mona",
                "phone": "01000000005",
                "dress_id": second_dress.id,
                "amount": "2600",
                "paid_amount": "2600",
                "description": "تم التعديل",
            },
        )

        self.assertEqual(response.status_code, 302)
        sale.refresh_from_db()
        self.dress.refresh_from_db()
        second_dress.refresh_from_db()
        self.assertEqual(sale.dress, second_dress)
        self.assertEqual(self.dress.status, Dress.STATUS_AVAILABLE)
        self.assertEqual(second_dress.status, Dress.STATUS_SOLD)

    def test_delete_invoice_restores_dress_status(self):
        sale = Transaction.objects.create(
            transaction_type=Transaction.TYPE_SALE,
            dress=self.dress,
            amount=2500,
            paid_amount=2500,
            status=Transaction.STATUS_COMPLETED,
        )
        self.dress.status = Dress.STATUS_SOLD
        self.dress.save()

        response = self.client.get(reverse("delete_transaction", args=[sale.id]))

        self.assertEqual(response.status_code, 302)
        self.dress.refresh_from_db()
        self.assertFalse(Transaction.objects.filter(id=sale.id).exists())
        self.assertEqual(self.dress.status, Dress.STATUS_AVAILABLE)

    def test_dress_list_shows_booking_dates_for_dress(self):
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            amount=500,
            paid_amount=200,
            rent_start_date="2026-05-02",
            expected_return_date="2026-05-04",
        )

        response = self.client.get(reverse("dress_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2026-05-02")
        self.assertContains(response, "2026-05-04")
        self.assertContains(response, "عرض أيام الحجز")

    def test_deliveries_page_shows_only_start_date_deliveries(self):
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            amount=500,
            paid_amount=200,
            rent_start_date="2026-04-19",
            expected_return_date="2026-04-21",
        )
        other_dress = Dress.objects.create(
            name="Blue Dress",
            code="D-500",
            size="S",
            rent_price=400,
            sell_price=2000,
            tailoring_price=600,
        )
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=other_dress,
            amount=500,
            paid_amount=200,
            rent_start_date="2026-04-20",
            expected_return_date="2026-04-22",
        )

        response = self.client.get(reverse("deliveries_today"), {"date": "2026-04-19"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Satin Dress")
        self.assertNotContains(response, "Blue Dress")
        self.assertContains(response, "عدد التسليمات")
        self.assertContains(response, "الغد")

    def test_deliveries_page_includes_tailoring_deliveries(self):
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_TAILORING,
            amount=800,
            paid_amount=300,
            delivery_date="2026-04-19",
            description="تفصيل جديد",
        )

        response = self.client.get(reverse("deliveries_today"), {"date": "2026-04-19"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تفصيل")
        self.assertContains(response, "تفصيل بدون قطعة")

    def test_deliveries_page_can_filter_tailoring_only(self):
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            amount=500,
            paid_amount=200,
            rent_start_date="2026-04-19",
            expected_return_date="2026-04-21",
        )
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_TAILORING,
            amount=800,
            paid_amount=300,
            delivery_date="2026-04-19",
            description="تفصيل جديد",
        )

        response = self.client.get(reverse("deliveries_today"), {"date": "2026-04-19", "type": Transaction.TYPE_TAILORING})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تفصيل بدون قطعة")
        self.assertNotContains(response, "Satin Dress")

    def test_invoice_list_search_filters_invoices(self):
        sale = Transaction.objects.create(
            transaction_type=Transaction.TYPE_SALE,
            dress=self.dress,
            created_by=self.user,
            amount=2500,
            paid_amount=2500,
            status=Transaction.STATUS_COMPLETED,
            description="فاتورة ساتان",
        )
        other_dress = Dress.objects.create(
            name="Blue Dress",
            code="D-700",
            size="S",
            rent_price=400,
            sell_price=2000,
            tailoring_price=600,
        )
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_SALE,
            dress=other_dress,
            amount=2000,
            paid_amount=1500,
            status=Transaction.STATUS_COMPLETED,
            description="فاتورة بلو",
        )

        response = self.client.get(reverse("invoice_list"), {"q": sale.invoice_number})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, sale.invoice_number)
        self.assertContains(response, "Satin Dress")
        self.assertNotContains(response, "Blue Dress")

    def test_create_invoice_stores_logged_in_user(self):
        response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_SALE]),
            {
                "customer_name": "Mona",
                "phone": "01000000006",
                "dress_id": self.dress.id,
                "amount": "2500",
                "paid_amount": "2500",
                "description": "بيع مسجل",
            },
        )

        self.assertEqual(response.status_code, 302)
        transaction = Transaction.objects.latest("id")
        self.assertEqual(transaction.created_by, self.user)

    def test_invoice_detail_shows_print_button(self):
        transaction = Transaction.objects.create(
            transaction_type=Transaction.TYPE_SALE,
            dress=self.dress,
            created_by=self.user,
            amount=2500,
            paid_amount=2500,
            status=Transaction.STATUS_COMPLETED,
        )

        response = self.client.get(reverse("invoice_detail", args=[transaction.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "طباعة الفاتورة")
        self.assertContains(response, "window.print()")

    def test_create_invoice_saves_optional_measurements(self):
        uploaded_image = SimpleUploadedFile(
            "model.gif",
            (
                b"GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00"
                b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00"
                b"\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_TAILORING]),
            {
                "customer_name": "Laila",
                "phone": "01000000008",
                "amount": "800",
                "paid_amount": "300",
                "bust": "92",
                "shoulder": "39",
                "waist": "74",
                "side": "44",
                "sleeve": "58",
                "waist_length": "42",
                "skirt_length": "108",
                "full_length": "150",
                "description": "تفصيل جديد",
                "delivery_date": "2026-05-22",
                "tailoring_model_image": uploaded_image,
            },
        )

        self.assertEqual(response.status_code, 302)
        transaction = Transaction.objects.latest("id")
        self.assertEqual(str(transaction.bust), "92.00")
        self.assertEqual(str(transaction.shoulder), "39.00")
        self.assertEqual(str(transaction.waist), "74.00")
        self.assertEqual(str(transaction.side), "44.00")
        self.assertEqual(str(transaction.sleeve), "58.00")
        self.assertEqual(str(transaction.waist_length), "42.00")
        self.assertEqual(str(transaction.skirt_length), "108.00")
        self.assertEqual(str(transaction.full_length), "150.00")
        self.assertTrue(bool(transaction.tailoring_model_image))

    def test_prevent_paid_amount_greater_than_invoice_total(self):
        response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_SALE]),
            {
                "customer_name": "Mona",
                "phone": "01000000007",
                "dress_id": self.dress.id,
                "amount": "2500",
                "paid_amount": "3000",
                "description": "عربون أكبر من الإجمالي",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "لا يمكن أن يكون العربون أكبر من إجمالي الفاتورة")
        self.assertFalse(Transaction.objects.exists())

    def test_prevent_invalid_phone_number_in_invoice(self):
        response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_SALE]),
            {
                "customer_name": "Mona",
                "phone": "0100abc",
                "dress_id": self.dress.id,
                "amount": "2500",
                "paid_amount": "2000",
                "description": "هاتف غير صحيح",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "رقم الهاتف يجب أن يتكون من 11 رقمًا فقط")
        self.assertFalse(Transaction.objects.exists())

    def test_invalid_invoice_form_keeps_values_and_marks_wrong_fields(self):
        response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_SALE]),
            {
                "customer_name": "Mona",
                "phone": "0100abc",
                "dress_id": self.dress.id,
                "amount": "2500",
                "paid_amount": "3000",
                "bust": "95",
                "shoulder": "38",
                "waist": "72",
                "description": "بيانات لازم تفضل موجودة",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Mona"', html=False)
        self.assertContains(response, 'value="0100abc"', html=False)
        self.assertContains(response, 'value="95"', html=False)
        self.assertContains(response, 'value="38"', html=False)
        self.assertContains(response, 'value="72"', html=False)
        self.assertContains(response, "بيانات لازم تفضل موجودة")
        self.assertContains(response, "is-invalid")
        self.assertContains(response, "رقم الهاتف يجب أن يتكون من 11 رقمًا فقط")
        self.assertContains(response, "لا يمكن أن يكون العربون أكبر من إجمالي الفاتورة")

    def test_manager_can_create_employee_user(self):
        self.user.is_staff = True
        self.user.save()

        response = self.client.post(
            reverse("user_list"),
            {
                "username": "newemployee",
                "password": "Emp@12345",
                "role": "employee",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(get_user_model().objects.filter(username="newemployee").exists())

    def test_manager_can_delete_employee_user(self):
        self.user.is_staff = True
        self.user.save()
        employee = get_user_model().objects.create_user(username="employee2", password="Emp@12345")

        response = self.client.post(reverse("delete_user", args=[employee.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(get_user_model().objects.filter(id=employee.id).exists())

    def test_manager_cannot_delete_current_user(self):
        self.user.is_staff = True
        self.user.save()

        response = self.client.post(reverse("delete_user", args=[self.user.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(get_user_model().objects.filter(id=self.user.id).exists())
        self.assertContains(response, "لا يمكن حذف المستخدم الحالي أثناء تسجيل الدخول")

    def test_settle_transaction_payment_marks_invoice_fully_paid(self):
        transaction = Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            created_by=self.user,
            amount=500,
            paid_amount=200,
            rent_start_date=timezone.localdate(),
            expected_return_date=timezone.localdate(),
        )

        response = self.client.post(reverse("settle_transaction_payment", args=[transaction.id]))

        self.assertEqual(response.status_code, 302)
        transaction.refresh_from_db()
        self.assertEqual(transaction.paid_amount, transaction.amount)
        self.assertEqual(transaction.balance, 0)

    def test_laundry_page_shows_pending_records(self):
        transaction = Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            amount=500,
            paid_amount=200,
            rent_start_date=timezone.localdate(),
            expected_return_date=timezone.localdate(),
        )
        LaundryRecord.objects.create(
            dress=self.dress,
            transaction=transaction,
            cost=50,
            notes="غسيل سريع",
        )

        response = self.client.get(reverse("laundry_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Satin Dress")
        self.assertContains(response, "غسيل سريع")
        self.assertContains(response, "إجمالي الفساتين في الغسيل")

    def test_current_rentals_page_shows_only_current_rentals(self):
        today = timezone.localdate()
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            amount=500,
            paid_amount=200,
            rent_start_date=today,
            expected_return_date=today,
        )
        other_dress = Dress.objects.create(
            name="Future Dress",
            code="D-800",
            size="L",
            rent_price=500,
            sell_price=2200,
            tailoring_price=700,
        )
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=other_dress,
            amount=500,
            paid_amount=200,
            rent_start_date=today + timezone.timedelta(days=2),
            expected_return_date=today + timezone.timedelta(days=3),
        )

        response = self.client.get(reverse("current_rentals_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Satin Dress")
        self.assertNotContains(response, "Future Dress")
        self.assertContains(response, "إجمالي الإيجارات الحالية")

    def test_monthly_statistics_forbidden_for_employee(self):
        response = self.client.get(reverse("monthly_statistics"))
        self.assertEqual(response.status_code, 403)

    def test_monthly_statistics_visible_for_manager(self):
        self.user.is_staff = True
        self.user.save()
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_SALE,
            dress=self.dress,
            created_by=self.user,
            amount=2500,
            paid_amount=2000,
            status=Transaction.STATUS_COMPLETED,
        )

        response = self.client.get(reverse("monthly_statistics"), {"month": timezone.localdate().month, "year": timezone.localdate().year})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إحصائيات شهرية")
        self.assertContains(response, "إيراد فواتير البيع")
        self.assertContains(response, "2500")
        self.assertContains(response, "2000")

    def test_dashboard_shows_tailoring_income_separately(self):
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_TAILORING,
            amount=800,
            paid_amount=500,
            delivery_date=timezone.localdate(),
            created_by=self.user,
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إيراد التفصيل اليوم")
        self.assertContains(response, "500")

    def test_dashboard_shows_sale_income_separately(self):
        Transaction.objects.create(
            transaction_type=Transaction.TYPE_SALE,
            dress=self.dress,
            amount=2500,
            paid_amount=1800,
            status=Transaction.STATUS_COMPLETED,
            created_by=self.user,
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إيراد فواتير البيع اليوم")
        self.assertContains(response, "1800")

    def test_create_transaction_records_initial_payment_entry(self):
        response = self.client.post(
            reverse("create_transaction", args=[Transaction.TYPE_SALE]),
            {
                "customer_name": "Mona",
                "phone": "01000000011",
                "dress_id": self.dress.id,
                "amount": "2500",
                "paid_amount": "700",
            },
        )

        self.assertEqual(response.status_code, 302)
        transaction = Transaction.objects.latest("id")
        payment = Payment.objects.get(transaction=transaction)
        self.assertEqual(payment.amount, transaction.paid_amount)
        self.assertEqual(payment.direction, Payment.DIRECTION_IN)

    def test_create_sales_order_with_multiple_dress_models(self):
        customer = SalesCustomer.objects.create(name="Boutique Noor", phone="01000000012")
        response = self.client.post(
            reverse("create_sales_order"),
            {
                "customer": customer.id,
                "ordered_at": "2026-08-20",
                "initial_paid_amount": "300",
                "notes": "طلب جملة",
                "dress_name[]": ["موديل ساتان", "موديل سواريه"],
                "dress_quantity[]": ["2", "3"],
                "dress_unit_price[]": ["100", "150"],
                "dress_notes[]": ["أحمر", "أزرق"],
            },
        )

        self.assertEqual(response.status_code, 302)
        sales_order = SalesOrder.objects.get(customer=customer)
        self.assertEqual(sales_order.items.count(), 2)
        self.assertEqual(str(sales_order.total_amount), "650.00")
        self.assertEqual(str(sales_order.paid_amount), "300.00")
        self.assertTrue(Payment.objects.filter(sales_order=sales_order, amount=300).exists())

    def test_customer_sales_payment_is_distributed_to_oldest_orders(self):
        customer = SalesCustomer.objects.create(name="Boutique Salma", phone="01000000013")
        older_order = SalesOrder.objects.create(customer=customer, total_amount=100, ordered_at=timezone.localdate() - timezone.timedelta(days=1))
        newer_order = SalesOrder.objects.create(customer=customer, total_amount=100, ordered_at=timezone.localdate())

        response = self.client.post(
            reverse("sales_customer_detail", args=[customer.id]),
            {"amount": "150", "notes": "تحصيل"},
        )

        self.assertEqual(response.status_code, 302)
        older_order.refresh_from_db()
        newer_order.refresh_from_db()
        self.assertEqual(str(older_order.paid_amount), "100.00")
        self.assertEqual(str(newer_order.paid_amount), "50.00")

    def test_can_add_sales_customer_manually(self):
        response = self.client.post(
            reverse("sales_customer_list"),
            {"name": "Boutique Huda", "phone": "01000000014", "notes": "عميل جملة"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(SalesCustomer.objects.filter(name="Boutique Huda", phone="01000000014").exists())

    def test_sales_customer_month_filter_shows_orders_and_payments(self):
        customer = SalesCustomer.objects.create(name="Boutique Amal")
        sales_order = SalesOrder.objects.create(customer=customer, total_amount=500, paid_amount=200, ordered_at=timezone.localdate())
        Payment.objects.create(sales_order=sales_order, direction=Payment.DIRECTION_IN, amount=200, created_by=self.user)

        response = self.client.get(
            reverse("sales_customer_list"),
            {"month": timezone.localdate().month, "year": timezone.localdate().year},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Boutique Amal")
        self.assertContains(response, "اشترى")
        self.assertContains(response, "دفع")
        self.assertContains(response, "500")
        self.assertContains(response, "200")

    def test_can_add_payment_to_invoice(self):
        transaction = Transaction.objects.create(
            transaction_type=Transaction.TYPE_RENT,
            dress=self.dress,
            created_by=self.user,
            amount=500,
            paid_amount=0,
            rent_start_date=timezone.localdate(),
            expected_return_date=timezone.localdate(),
        )

        response = self.client.post(
            reverse("add_transaction_payment", args=[transaction.id]),
            {"amount": "150", "notes": "دفعة ثانية"},
        )

        self.assertEqual(response.status_code, 302)
        transaction.refresh_from_db()
        self.assertEqual(str(transaction.paid_amount), "150.00")
        self.assertTrue(Payment.objects.filter(transaction=transaction, notes="دفعة ثانية").exists())

    def test_manager_can_create_supplier(self):
        self.user.is_staff = True
        self.user.save()

        response = self.client.post(
            reverse("supplier_list"),
            {"name": "Fashion Supplier", "phone": "01234567890", "notes": "توريد شهري"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Supplier.objects.filter(name="Fashion Supplier").exists())

    def test_manager_can_create_purchase_with_items_and_initial_payment(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Main Supplier")

        response = self.client.post(
            reverse("create_purchase"),
            {
                "supplier": supplier.id,
                "purchased_at": "2026-08-17",
                "initial_paid_amount": "300",
                "notes": "شراء جديد",
                "item_name[]": ["قماش", "سوستة"],
                "item_quantity[]": ["2", "4"],
                "item_unit_cost[]": ["100", "25"],
                "item_notes[]": ["", ""],
            },
        )

        self.assertEqual(response.status_code, 302)
        purchase = Purchase.objects.latest("id")
        self.assertEqual(purchase.items.count(), 2)
        self.assertEqual(str(purchase.total_amount), "300.00")
        self.assertEqual(str(purchase.paid_amount), "300.00")
        self.assertTrue(Payment.objects.filter(purchase=purchase, amount="300").exists())

    def test_monthly_statistics_shows_purchase_totals(self):
        self.user.is_staff = True
        self.user.save()
        purchase = Purchase.objects.create(
            supplier=Supplier.objects.create(name="Season Supplier"),
            created_by=self.user,
            purchased_at=timezone.localdate(),
        )
        Purchase.objects.filter(id=purchase.id).update(total_amount=500, paid_amount=200)

        response = self.client.get(
            reverse("monthly_statistics"),
            {"month": timezone.localdate().month, "year": timezone.localdate().year},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إجمالي المشتريات")
        self.assertContains(response, "500")

    def test_supplier_list_shows_due_amount(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Due Supplier")
        Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=900,
            paid_amount=300,
            purchased_at=timezone.localdate(),
        )

        response = self.client.get(reverse("supplier_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Due Supplier")
        self.assertContains(response, "600")
        self.assertContains(response, "حساب المورد")

    def test_supplier_account_payment_distributes_across_open_purchases(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Balance Supplier")
        older_purchase = Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=500,
            paid_amount=100,
            purchased_at=timezone.localdate() - timezone.timedelta(days=2),
        )
        newer_purchase = Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=400,
            paid_amount=0,
            purchased_at=timezone.localdate() - timezone.timedelta(days=1),
        )

        response = self.client.post(
            reverse("supplier_detail", args=[supplier.id]),
            {"amount": "600", "notes": "دفعة مجمعة"},
        )

        self.assertEqual(response.status_code, 302)
        older_purchase.refresh_from_db()
        newer_purchase.refresh_from_db()
        self.assertEqual(str(older_purchase.paid_amount), "500.00")
        self.assertEqual(str(newer_purchase.paid_amount), "200.00")
        self.assertEqual(Payment.objects.filter(purchase__supplier=supplier).count(), 2)

    def test_supplier_detail_filters_purchases_and_payments_by_month(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Filtered Supplier")
        august_purchase = Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=300,
            paid_amount=100,
            purchased_at=timezone.datetime(2026, 8, 10).date(),
        )
        june_purchase = Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=450,
            paid_amount=0,
            purchased_at=timezone.datetime(2026, 6, 8).date(),
        )
        Payment.objects.create(
            purchase=august_purchase,
            direction=Payment.DIRECTION_OUT,
            amount=100,
            notes="دفعة أغسطس",
            created_by=self.user,
        )
        june_payment = Payment.objects.create(
            purchase=june_purchase,
            direction=Payment.DIRECTION_OUT,
            amount=50,
            notes="دفعة يونيو",
            created_by=self.user,
        )
        Payment.objects.filter(id=june_payment.id).update(created_at=timezone.datetime(2026, 6, 9, 12, 0, tzinfo=timezone.get_current_timezone()))

        response = self.client.get(reverse("supplier_detail", args=[supplier.id]), {"month": 6, "year": 2026})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, june_purchase.invoice_number)
        self.assertContains(response, "دفعة يونيو")
        self.assertNotContains(response, august_purchase.invoice_number)
        self.assertNotContains(response, "دفعة أغسطس")

    def test_supplier_detail_shows_only_last_five_payments(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Paged Supplier")
        purchase = Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=1000,
            paid_amount=0,
            purchased_at=timezone.localdate(),
        )
        for index in range(6):
            Payment.objects.create(
                purchase=purchase,
                direction=Payment.DIRECTION_OUT,
                amount=50,
                notes=f"دفعة {index}",
                created_by=self.user,
            )

        response = self.client.get(reverse("supplier_detail", args=[supplier.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "كل الدفعات")
        self.assertContains(response, "دفعة 5")
        self.assertNotContains(response, "دفعة 0")

    def test_supplier_payments_page_shows_all_payments(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="All Payments Supplier")
        purchase = Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=1000,
            paid_amount=0,
            purchased_at=timezone.localdate(),
        )
        for index in range(6):
            Payment.objects.create(
                purchase=purchase,
                direction=Payment.DIRECTION_OUT,
                amount=50,
                notes=f"سجل {index}",
                created_by=self.user,
            )

        response = self.client.get(reverse("supplier_payments", args=[supplier.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "سجل 0")
        self.assertContains(response, "سجل 5")

    def test_can_delete_supplier_without_purchases(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Delete Me")

        response = self.client.post(reverse("delete_supplier", args=[supplier.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Supplier.objects.filter(id=supplier.id).exists())

    def test_cannot_delete_supplier_with_purchases(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Keep Me")
        Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=200,
            paid_amount=0,
            purchased_at=timezone.localdate(),
        )

        response = self.client.post(reverse("delete_supplier", args=[supplier.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Supplier.objects.filter(id=supplier.id).exists())
        self.assertContains(response, "لا يمكن حذف المورد")

    def test_can_edit_purchase(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Original Supplier")
        new_supplier = Supplier.objects.create(name="New Supplier")
        purchase = Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=150,
            paid_amount=50,
            purchased_at=timezone.localdate(),
            notes="قديم",
        )
        PurchaseItem.objects.create(purchase=purchase, item_name="قماش", quantity=1, unit_cost=150)

        response = self.client.post(
            reverse("edit_purchase", args=[purchase.id]),
            {
                "supplier": new_supplier.id,
                "purchased_at": "2026-08-17",
                "initial_paid_amount": "70",
                "notes": "جديد",
                "item_name[]": ["ستان", "تل"],
                "item_quantity[]": ["1", "2"],
                "item_unit_cost[]": ["120", "40"],
                "item_notes[]": ["", ""],
            },
        )

        self.assertEqual(response.status_code, 302)
        purchase.refresh_from_db()
        self.assertEqual(purchase.supplier, new_supplier)
        self.assertEqual(str(purchase.total_amount), "200.00")
        self.assertEqual(str(purchase.paid_amount), "70.00")
        self.assertEqual(purchase.items.count(), 2)

    def test_can_delete_purchase(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Purchase Supplier")
        purchase = Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=300,
            paid_amount=0,
            purchased_at=timezone.localdate(),
        )

        response = self.client.post(reverse("delete_purchase", args=[purchase.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Purchase.objects.filter(id=purchase.id).exists())

    def test_can_edit_supplier_payment(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Payment Edit Supplier")
        purchase = Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=500,
            paid_amount=100,
            purchased_at=timezone.localdate(),
        )
        payment = Payment.objects.create(
            purchase=purchase,
            direction=Payment.DIRECTION_OUT,
            amount=100,
            notes="قديمة",
            created_by=self.user,
        )
        _ = payment
        purchase.refresh_from_db()

        response = self.client.post(
            reverse("edit_supplier_payment", args=[payment.id]),
            {"amount": "150", "notes": "جديدة"},
        )

        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(str(payment.amount), "150.00")
        self.assertEqual(payment.notes, "جديدة")
        self.assertEqual(str(purchase.paid_amount), "150.00")

    def test_can_delete_supplier_payment(self):
        self.user.is_staff = True
        self.user.save()
        supplier = Supplier.objects.create(name="Payment Delete Supplier")
        purchase = Purchase.objects.create(
            supplier=supplier,
            created_by=self.user,
            total_amount=500,
            paid_amount=120,
            purchased_at=timezone.localdate(),
        )
        payment = Payment.objects.create(
            purchase=purchase,
            direction=Payment.DIRECTION_OUT,
            amount=120,
            notes="تحذف",
            created_by=self.user,
        )
        purchase.refresh_from_db()

        response = self.client.post(reverse("delete_supplier_payment", args=[payment.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Payment.objects.filter(id=payment.id).exists())
        purchase.refresh_from_db()
        self.assertEqual(str(purchase.paid_amount), "0.00")

    def test_manager_can_create_expense(self):
        self.user.is_staff = True
        self.user.save()

        response = self.client.post(
            reverse("expense_list"),
            {
                "expense_type": Expense.TYPE_RENT,
                "title": "غسيل فساتين",
                "amount": "150",
                "spent_at": "2026-08-17",
                "notes": "مصروف إيجار",
            },
        )

        self.assertEqual(response.status_code, 302)
        expense = Expense.objects.get()
        self.assertEqual(expense.expense_type, Expense.TYPE_RENT)
        self.assertEqual(str(expense.amount), "150.00")

    def test_expense_page_filters_by_month(self):
        self.user.is_staff = True
        self.user.save()
        Expense.objects.create(
            expense_type=Expense.TYPE_RENT,
            title="مصروف أغسطس",
            amount=100,
            spent_at=timezone.datetime(2026, 8, 12).date(),
            created_by=self.user,
        )
        Expense.objects.create(
            expense_type=Expense.TYPE_TAILORING,
            title="مصروف يونيو",
            amount=200,
            spent_at=timezone.datetime(2026, 6, 8).date(),
            created_by=self.user,
        )

        response = self.client.get(reverse("expense_list"), {"month": 6, "year": 2026, "type": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مصروف يونيو")
        self.assertNotContains(response, "مصروف أغسطس")

    def test_expense_page_filters_by_type(self):
        self.user.is_staff = True
        self.user.save()
        rent_expense = Expense.objects.create(
            expense_type=Expense.TYPE_RENT,
            title="تنظيف إيجار",
            amount=120,
            spent_at=timezone.localdate(),
            created_by=self.user,
        )
        tailoring_expense = Expense.objects.create(
            expense_type=Expense.TYPE_TAILORING,
            title="إكسسوار تفصيل",
            amount=220,
            spent_at=timezone.localdate(),
            created_by=self.user,
        )

        response = self.client.get(
            reverse("expense_list"),
            {"month": timezone.localdate().month, "year": timezone.localdate().year, "type": Expense.TYPE_TAILORING},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إكسسوار تفصيل")
        filtered_expenses = list(response.context["expenses"])
        self.assertIn(tailoring_expense, filtered_expenses)
        self.assertNotIn(rent_expense, filtered_expenses)

    def test_expense_page_shows_today_expense_totals_separately(self):
        self.user.is_staff = True
        self.user.save()
        today = timezone.localdate()
        Expense.objects.create(
            expense_type=Expense.TYPE_RENT,
            title="مصروف إيجار اليوم",
            amount=90,
            spent_at=today,
            created_by=self.user,
        )
        Expense.objects.create(
            expense_type=Expense.TYPE_TAILORING,
            title="مصروف تفصيل اليوم",
            amount=140,
            spent_at=today,
            created_by=self.user,
        )

        response = self.client.get(reverse("expense_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مصروفات إيجار اليوم")
        self.assertContains(response, "مصروفات تفصيل اليوم")
        self.assertContains(response, "90")
        self.assertContains(response, "140")

    def test_expense_page_shows_today_expenses_section(self):
        self.user.is_staff = True
        self.user.save()
        today = timezone.localdate()
        Expense.objects.create(
            expense_type=Expense.TYPE_RENT,
            title="مصروف اليوم فقط",
            amount=75,
            spent_at=today,
            created_by=self.user,
        )
        Expense.objects.create(
            expense_type=Expense.TYPE_RENT,
            title="مصروف قديم",
            amount=50,
            spent_at=today - timezone.timedelta(days=1),
            created_by=self.user,
        )

        response = self.client.get(reverse("expense_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مصروفات اليوم")
        self.assertContains(response, "مصروف اليوم فقط")

    def test_expense_page_shows_only_last_five_today_expenses(self):
        self.user.is_staff = True
        self.user.save()
        today = timezone.localdate()
        for index in range(6):
            Expense.objects.create(
                expense_type=Expense.TYPE_RENT,
                title=f"مصروف يوم {index}",
                amount=10,
                spent_at=today,
                created_by=self.user,
            )

        response = self.client.get(reverse("expense_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "كل مصروفات اليوم")
        self.assertContains(response, "مصروف يوم 5")
        self.assertNotContains(response, "مصروف يوم 0")

    def test_expense_page_shows_only_last_five_archive_expenses(self):
        self.user.is_staff = True
        self.user.save()
        today = timezone.localdate()
        for index in range(6):
            Expense.objects.create(
                expense_type=Expense.TYPE_TAILORING,
                title=f"سجل مصروف {index}",
                amount=15,
                spent_at=today,
                created_by=self.user,
            )

        response = self.client.get(reverse("expense_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "كل سجل المصروفات")
        self.assertContains(response, "سجل مصروف 5")
        self.assertNotContains(response, "سجل مصروف 0")

    def test_today_expenses_page_shows_all_today_expenses(self):
        self.user.is_staff = True
        self.user.save()
        today = timezone.localdate()
        for index in range(6):
            Expense.objects.create(
                expense_type=Expense.TYPE_RENT,
                title=f"اليوم كامل {index}",
                amount=12,
                spent_at=today,
                created_by=self.user,
            )

        response = self.client.get(reverse("today_expense_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "اليوم كامل 0")
        self.assertContains(response, "اليوم كامل 5")

    def test_expense_archive_page_shows_all_filtered_expenses(self):
        self.user.is_staff = True
        self.user.save()
        for index in range(6):
            Expense.objects.create(
                expense_type=Expense.TYPE_TAILORING,
                title=f"أرشيف {index}",
                amount=20,
                spent_at=timezone.localdate(),
                created_by=self.user,
            )

        response = self.client.get(
            reverse("expense_archive_list"),
            {"month": timezone.localdate().month, "year": timezone.localdate().year, "type": Expense.TYPE_TAILORING},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "أرشيف 0")
        self.assertContains(response, "أرشيف 5")
