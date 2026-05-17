from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Dress, LaundryRecord, Transaction


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

    def test_create_invoice_saves_optional_measurements(self):
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
                "description": "تفصيل جديد",
            },
        )

        self.assertEqual(response.status_code, 302)
        transaction = Transaction.objects.latest("id")
        self.assertEqual(str(transaction.bust), "92.00")
        self.assertEqual(str(transaction.shoulder), "39.00")
        self.assertEqual(str(transaction.waist), "74.00")

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
        self.assertContains(response, "2500")
