import logging
from smtplib import SMTPException
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.views import PasswordChangeView, PasswordResetView
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from .models import AccountPreferences, Booking, PrivateEvent, Notification
from .security import limited


class IdentityResetForm(PasswordResetForm):
    username = forms.CharField(label='Логин', max_length=150)

    def get_users(self, email):
        # A shared email must never reset a different account.
        return (user for user in super().get_users(email) if user.username == self.cleaned_data['username'])


class RecoveryView(PasswordResetView):
    form_class = IdentityResetForm
    template_name = 'account_recovery.html'
    email_template_name = 'account_reset_email.txt'
    subject_template_name = 'account_reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), 'recovery_enabled': settings.PASSWORD_RESET_ENABLED}

    def post(self, request, *args, **kwargs):
        if not settings.PASSWORD_RESET_ENABLED:
            return self.get(request, *args, **kwargs)
        if limited(request, 'recovery', 5):
            return HttpResponse('Слишком много попыток. Попробуй через 10 минут.', status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except (SMTPException, OSError):
            # Keep the response identical for registered and unknown accounts.
            logging.getLogger(__name__).warning('Password recovery email could not be delivered.')
            return redirect('password_reset_done')


@method_decorator(login_required(login_url='/account/login/'), name='dispatch')
class ChangePassword(PasswordChangeView):
    template_name = 'account_form.html'
    success_url = reverse_lazy('preferences')
    extra_context = {'title': 'Сменить пароль', 'button_label': 'Сохранить пароль'}

    def post(self, request, *args, **kwargs):
        if limited(request, 'password-change', 8):
            return HttpResponse('Слишком много попыток. Попробуй позже.', status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, 'Пароль изменён.')
        return super().form_valid(form)


class PasswordProof(forms.Form):
    password = forms.CharField(label='Текущий пароль', widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        value = self.cleaned_data['password']
        if not self.user.check_password(value):
            raise forms.ValidationError('Пароль не подходит.')
        return value


class RecoveryEmailForm(PasswordProof):
    email = forms.EmailField(label='Почта для восстановления', max_length=254)


class DeleteAccountForm(PasswordProof):
    confirm = forms.BooleanField(label='Понимаю: аккаунт и личные дела будут удалены без возможности отмены; записи у мастера сохранятся.')


@login_required(login_url='/account/login/')
def recovery_email(request):
    form = RecoveryEmailForm(request.POST or None, user=request.user, initial={'email': request.user.email})
    if request.method == 'POST':
        if limited(request, 'recovery-email', 8):
            return HttpResponse('Слишком много попыток. Попробуй позже.', status=429)
        if form.is_valid():
            request.user.email = form.cleaned_data['email']
            request.user.save(update_fields=['email'])
            messages.success(request, 'Почта сохранена. Проверь адрес: восстановление потребует доступа к этому ящику.')
            return redirect('preferences')
    return render(request, 'account_form.html', {'form': form, 'title': 'Почта для восстановления',
        'subtitle': 'Укажи свой адрес. При восстановлении понадобятся логин и доступ к этому ящику. Отправка писем работает только после подключения почты студии.', 'button_label': 'Сохранить почту'})


@login_required(login_url='/account/login/')
def export_account(request):
    prefs = AccountPreferences.objects.filter(owner=request.user).values('name', 'phone', 'theme', 'design', 'glass_mode', 'color_mode', 'large_text', 'reminders', 'consent_at', 'consent_text').first()
    payload = {'exported_at': timezone.now(), 'account': {'username': request.user.username, 'email': request.user.email},
        'notifications': list(Notification.objects.filter(owner=request.user).values('text', 'created_at', 'read_at')),
        'profile': prefs, 'personal_events': list(PrivateEvent.objects.filter(owner=request.user).values('title', 'start', 'end', 'done')),
        'bookings': list(Booking.objects.filter(account=request.user).values('name', 'phone', 'service_summary', 'start', 'end', 'status', 'comment', 'consent_at', 'consent_text'))}
    response = JsonResponse(payload, json_dumps_params={'ensure_ascii': False, 'indent': 2})
    response['Content-Disposition'] = 'attachment; filename="neilmania-my-data.json"'
    return response


@login_required(login_url='/account/login/')
def delete_account(request):
    if request.user.is_staff or request.user.is_superuser:
        return HttpResponse('Аккаунт мастера нельзя удалить через клиентский кабинет.', status=403)
    form = DeleteAccountForm(request.POST or None, user=request.user)
    if request.method == 'POST':
        if limited(request, 'delete-account', 5):
            return HttpResponse('Слишком много попыток. Попробуй позже.', status=429)
        if form.is_valid():
            with transaction.atomic():
                request.user.delete()
            logout(request)
            messages.success(request, 'Аккаунт и личные дела удалены. Записи у мастера не отменены.')
            return redirect('book')
    return render(request, 'account_delete.html', {'form': form,
        'personal_count': PrivateEvent.objects.filter(owner=request.user).count(),
        'booking_count': Booking.objects.filter(account=request.user).count()})
