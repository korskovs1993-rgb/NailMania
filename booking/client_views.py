from datetime import datetime, timedelta, time
import calendar
from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import QuarterDateTimeField, normalize_phone
from .models import PrivateEvent, Booking, AccountPreferences
from .security import limited
from . import drafts


class SignupForm(UserCreationForm):
    name = forms.CharField(label='Имя', max_length=100)
    phone = forms.CharField(label='Телефон', max_length=32, widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel'}))
    consent = forms.BooleanField(label='Согласен на обработку имени, телефона, логина, личных дел и настроек для работы моего кабинета и записи к мастеру')

    def clean_phone(self):
        return normalize_phone(self.cleaned_data['phone'])


class ProfileForm(forms.ModelForm):
    name = forms.CharField(label='Имя', max_length=100)
    phone = forms.CharField(label='Телефон', max_length=32, widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel'}))

    class Meta:
        model = AccountPreferences
        fields = ['name', 'phone']

    def clean_phone(self):
        return normalize_phone(self.cleaned_data['phone'])


class PersonalEventForm(forms.ModelForm):
    start = QuarterDateTimeField(label='Начало')
    end = QuarterDateTimeField(label='Окончание')

    class Meta:
        model = PrivateEvent
        fields = ['title', 'start', 'end']

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['end'] <= data['start']:
            raise ValidationError('Окончание должно быть позже начала.')
        return data


class PreferencesForm(forms.ModelForm):
    class Meta:
        model = AccountPreferences
        fields = ['design', 'theme', 'glass_mode', 'color_mode', 'large_text']
        labels = {'design': 'Оформление', 'theme': 'Палитра'}


class ClientLogin(LoginView):
    template_name = 'account_login.html'

    def post(self, request, *args, **kwargs):
        if limited(request, 'client-login', 10):
            return HttpResponse('Слишком много попыток. Попробуй позже.', status=429)
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return self.get_redirect_url() or ('/studio/' if self.request.user.is_staff else '/my/')


def signup(request):
    from .views import booking_open
    if request.user.is_authenticated:
        return redirect('my_calendar')
    form = SignupForm(request.POST or None)
    if request.method == 'POST':
        if not booking_open():
            form.add_error(None, 'Регистрация пока не открыта.')
        elif limited(request, 'signup', 5):
            return HttpResponse('Слишком много попыток. Попробуй позже.', status=429)
        elif form.is_valid():
            user = form.save()
            AccountPreferences.objects.create(owner=user, name=form.cleaned_data['name'], phone=form.cleaned_data['phone'], consent_at=timezone.now(), consent_text=str(form.fields['consent'].label))
            login(request, user)
            return redirect('book' if drafts.choices(request) else 'my_calendar')
    return render(request, 'account_signup.html', {'form': form})


@login_required(login_url='/account/login/')
def my_calendar(request):
    if request.user.is_staff:
        return redirect('dashboard')
    try:
        day = datetime.strptime(request.GET.get('date', ''), '%Y-%m-%d').date()
        if not 1900 <= day.year <= 2100:
            raise ValueError()
    except ValueError:
        day = timezone.localdate()
    dates = list(calendar.Calendar(firstweekday=0).itermonthdates(day.year, day.month))
    begin = timezone.make_aware(datetime.combine(dates[0], time.min))
    finish = timezone.make_aware(datetime.combine(dates[-1] + timedelta(days=1), time.min))
    private = list(PrivateEvent.objects.filter(owner=request.user, start__lt=finish, end__gt=begin))
    visits = list(Booking.objects.filter(account=request.user, start__lt=finish, end__gt=begin).exclude(status__in=['cancelled','rejected']))
    days = []
    for date in dates:
        a = timezone.make_aware(datetime.combine(date, time.min))
        b = a + timedelta(days=1)
        days.append({'date': date, 'count': sum(x.start < b and x.end > a for x in private + visits)})
    a = timezone.make_aware(datetime.combine(day, time.min))
    b = a + timedelta(days=1)
    first = day.replace(day=1)
    return render(request, 'my_calendar.html', {'days': days, 'selected': day,
        'previous': (first-timedelta(days=1)).replace(day=1), 'next_month': (first+timedelta(days=32)).replace(day=1),
        'events': [x for x in private if x.start < b and x.end > a],
        'appointments': Booking.objects.filter(account=request.user).order_by('-start')[:30]})


@login_required(login_url='/account/login/')
def private_event(request, pk=None):
    if request.user.is_staff:
        return HttpResponse(status=403)
    event = get_object_or_404(PrivateEvent, pk=pk, owner=request.user) if pk else None
    form = PersonalEventForm(request.POST or None, instance=event)
    if request.method == 'POST' and form.is_valid():
        event = form.save(commit=False)
        event.owner = request.user
        event.save()
        return redirect('/my/?date=' + timezone.localtime(event.start).date().isoformat())
    return render(request, 'form.html', {'form': form, 'title': 'Моё личное дело', 'subtitle': 'Видно только в твоём кабинете. Не занимает время мастера.'})


@login_required(login_url='/account/login/')
@require_POST
def toggle_event(request, pk):
    if request.user.is_staff:
        return HttpResponse(status=403)
    event = get_object_or_404(PrivateEvent, pk=pk, owner=request.user)
    event.done = request.POST.get('done') == '1'
    event.save(update_fields=['done'])
    return redirect('/my/?date=' + timezone.localtime(event.start).date().isoformat())


@login_required(login_url='/account/login/')
def preferences(request):
    prefs, _ = AccountPreferences.objects.get_or_create(owner=request.user)
    editing_profile = request.POST.get('section') == 'profile'
    form = PreferencesForm(request.POST if request.method == 'POST' and not editing_profile else None, instance=prefs)
    profile_form = ProfileForm(request.POST if editing_profile else None, instance=prefs)
    active_form = profile_form if editing_profile else form
    if request.method == 'POST' and active_form.is_valid():
        active_form.save()
        messages.success(request, 'Настройки сохранены для твоего аккаунта.')
        return redirect('book' if editing_profile and drafts.choices(request) else 'preferences')
    return render(request, 'preferences.html', {'form': form, 'profile_form': profile_form, 'master_area': request.user.is_staff})


@login_required(login_url='/account/login/')
@require_POST
def repeat_booking(request, pk):
    visit = get_object_or_404(Booking, pk=pk, account=request.user)
    ids = list(visit.services.filter(active=True).values_list('pk', flat=True))
    drafts.save_choices(request, {'services': ids})
    if len(ids) != visit.services.count() or not ids:
        messages.info(request, 'Часть прежних услуг больше недоступна. Проверь список и выбери услуги заново.')
    else:
        messages.info(request, 'Услуги из прошлого визита выбраны. Осталось выбрать новое время.')
    return redirect('book')
