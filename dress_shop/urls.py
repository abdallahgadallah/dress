from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from dresses.views import (
    add_dress,
    complete_laundry,
    create_transaction,
    current_rentals_list,
    change_password,
    dashboard,
    delete_transaction,
    delete_dress,
    deliveries_today,
    dress_list,
    invoice_list,
    laundry_list,
    monthly_statistics,
    user_list,
    edit_transaction,
    edit_dress,
    invoice_detail,
    settle_transaction_payment,
    return_rental,
)

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('change-password/', change_password, name='change_password'),
    path('monthly-statistics/', monthly_statistics, name='monthly_statistics'),
    path('users/', user_list, name='user_list'),
    path('', dashboard, name='dashboard'),
    path('current-rentals/', current_rentals_list, name='current_rentals_list'),
    path('deliveries/', deliveries_today, name='deliveries_today'),
    path('invoices/', invoice_list, name='invoice_list'),
    path('laundry/', laundry_list, name='laundry_list'),
    path('dresses/', dress_list, name='dress_list'),
    path('add-dress/', add_dress, name='add_dress'),
    path('edit-dress/<int:id>/', edit_dress, name='edit_dress'),
    path('delete-dress/<int:id>/', delete_dress, name='delete_dress'),
    path('transactions/new/<str:transaction_type>/', create_transaction, name='create_transaction'),
    path('invoice/<int:transaction_id>/', invoice_detail, name='invoice_detail'),
    path('invoice/<int:transaction_id>/settle-payment/', settle_transaction_payment, name='settle_transaction_payment'),
    path('invoice/<int:transaction_id>/edit/', edit_transaction, name='edit_transaction'),
    path('invoice/<int:transaction_id>/delete/', delete_transaction, name='delete_transaction'),
    path('return-rental/<int:transaction_id>/', return_rental, name='return_rental'),
    path('laundry/<int:laundry_id>/complete/', complete_laundry, name='complete_laundry'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
