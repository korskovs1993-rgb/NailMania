from datetime import datetime, timedelta, time
import calendar
from functools import wraps
from pathlib import Path
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse, FileResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST, require_GET
from . import domain
from .forms import (BookingForm, StaffBookingForm, ClientForm, BlockForm, StudioForm,
                    DayForm, ServiceForm, QuarterTimeField, PersonalTemplateForm, TIME_CHOICES)
from .models import (Studio, WeeklyDay, DayOverride, Service, Client, Booking,
                     PersonalBlock, PersonalTemplate, ChangeRequest, AccountPreferences)
from .security import limited
from . import drafts


def staff(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f'/login/?next={request.path}')
        if not request.user.is_staff:
            return HttpResponse('Доступ только для мастера.', status=403)
        return view(request, *args, **kwargs)
    return wrapped


class StudioLogin(LoginView):
    template_name = 'login.html'
    def post(self, request, *args, **kwargs):
        if limited(request, 'login', 10):
            return HttpResponse('Слишком много попыток. Попробуй через 10 минут.', status=429)
        return super().post(request, *args, **kwargs)


def booking_open():
    return settings.PUBLIC_BOOKING_ENABLED and (settings.DEBUG or all([
        settings.OPERATOR_NAME, settings.OPERATOR_ADDRESS, settings.PRIVACY_CONTACT]))


def book(request):
    if request.method == 'POST' and request.POST.get('intent') in ('login', 'signup', 'profile'):
        drafts.save_choices(request, request.POST)
        if request.POST['intent'] == 'profile' and request.user.is_authenticated:
            return redirect('preferences')
        return redirect('/account/signup/' if request.POST['intent'] == 'signup' else '/account/login/?next=/')
    profile = AccountPreferences.objects.filter(owner=request.user).first() if request.user.is_authenticated else None
    profile_ready = bool(profile and profile.name and profile.phone)
    if request.method == 'POST' and not request.user.is_authenticated:
        drafts.save_choices(request, request.POST)
        return redirect('/account/login/?next=/')
    if request.method == 'POST' and not profile_ready:
        drafts.save_choices(request, request.POST)
        messages.info(request, 'Добавь имя и телефон в настройках аккаунта — они будут подставляться во все заявки.')
        return redirect('preferences')
    try:
        selected = datetime.strptime(request.GET.get('date', ''), '%Y-%m-%d').date()
        if not 1900 <= selected.year <= 2100:
            raise ValueError()
    except ValueError:
        selected = timezone.localdate() + timedelta(days=1)
    payload = request.POST.copy() if request.method == 'POST' else None
    if payload is not None:
        payload['name'] = profile.name
        payload['phone'] = profile.phone
    initial = {'date': selected, **drafts.choices(request)}
    if request.GET.get('date'):
        initial['date'] = selected
    elif isinstance(initial.get('date'), str):
        selected = datetime.strptime(initial['date'], '%Y-%m-%d').date()
    form = BookingForm(payload, initial=initial)
    if request.method == 'POST':
        if not booking_open():
            form.add_error(None, 'Онлайн-запись пока не открыта.')
        elif limited(request, 'booking', 6):
            return HttpResponse('Слишком много заявок. Попробуй через 10 минут.', status=429)
        elif form.is_valid():
            try:
                with transaction.atomic():
                    booking = domain.create_booking(form.cleaned_data)
                    booking.account = request.user
                    booking.save(update_fields=['account'])
                request.session.pop(drafts.KEY, None)
                return redirect('status', token=booking.token)
            except ValidationError as error:
                form.add_error(None, error)
    if form.is_bound:
        try:
            selected = datetime.strptime(request.POST.get('date', ''), '%Y-%m-%d').date()
            if not 1900 <= selected.year <= 2100:
                selected = timezone.localdate()
        except ValueError:
            pass
    dates = calendar.Calendar(firstweekday=0).itermonthdates(selected.year, selected.month)
    days = [{'date': day, 'available': bool(domain.available_slots(day, 15)) if booking_open() else False,
             'outside': day.month != selected.month} for day in dates]
    first = selected.replace(day=1)
    return render(request, 'book.html', {'form': form, 'booking_open': booking_open(), 'booking_profile': profile, 'profile_ready': profile_ready,
        'public_days': days, 'selected': selected,
        'previous': (first - timedelta(days=1)).replace(day=1),
        'next_month': (first + timedelta(days=32)).replace(day=1)})


@staff
@transaction.atomic
def staff_book(request):
    form = StaffBookingForm(request.POST or None, initial={'date': timezone.localdate() + timedelta(days=1)})
    if request.method == 'POST' and form.is_valid():
        try:
            booking = domain.create_booking(form.cleaned_data, staff_entry=True)
            domain.decide_booking(booking.pk, 'confirm')
            messages.success(request, 'Запись добавлена и подтверждена.')
            return redirect('visit', pk=booking.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(request, 'book.html', {'form': form, 'booking_open': True, 'staff_entry': True})


@require_GET
def slots(request):
    try:
        day = datetime.strptime(request.GET.get('date', ''), '%Y-%m-%d').date()
        token = request.GET.get('token')
        exclude = None
        if token:
            booking = get_object_or_404(Booking, token=token)
            if booking.status not in domain.ACTIVE:
                return JsonResponse({'slots': []})
            minutes = int((booking.end - booking.start).total_seconds() / 60)
            exclude = booking.pk
        else:
            ids = request.GET.getlist('services')
            services = list(Service.objects.filter(pk__in=ids, active=True))
            if not services or len(services) != len(set(ids)):
                return JsonResponse({'slots': [], 'minutes': 0})
            minutes = sum(s.minutes for s in services)
        return JsonResponse({'slots': [x.strftime('%H:%M') for x in domain.available_slots(day, minutes, exclude)], 'minutes': minutes})
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Неверная дата или услуги.'}, status=400)


def status(request, token):
    booking = get_object_or_404(Booking, token=token)
    if request.method == 'POST':
        if limited(request, 'change', 10):
            return HttpResponse('Попробуй отправить запрос позже.', status=429)
        try:
            start = None
            if request.POST.get('kind') == 'move':
                value = f'{request.POST.get("date", "")}T{request.POST.get("time", "")}'
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is not None:
                    raise ValueError()
                start = timezone.make_aware(parsed)
            domain.request_change(booking.pk, request.POST.get('kind'), start)
            messages.success(request, 'Запрос отправлен Татьяне. Пока действует прежняя запись.')
        except (ValidationError, ValueError) as error:
            messages.error(request, '; '.join(error.messages) if isinstance(error, ValidationError) else 'Выбери дату и время.')
        return redirect('status', token=token)
    return render(request, 'status.html', {'booking': booking,
        'pending_change': booking.changes.filter(status='pending').first(),
        'can_change': booking.status in domain.ACTIVE and booking.start > timezone.now(),
        'today': timezone.localdate().isoformat(), 'time_choices': TIME_CHOICES})


@staff
def dashboard(request):
    try:
        selected = datetime.strptime(request.GET.get('date', ''), '%Y-%m-%d').date()
    except ValueError:
        selected = timezone.localdate()
    if not 1900 <= selected.year <= 2100:
        selected = timezone.localdate()
    mode = request.GET.get('view', request.session.get('calendar_view', 'month'))
    if mode not in ('week', 'month'):
        mode = 'week'
    request.session['calendar_view'] = mode
    if mode == 'month':
        dates = list(calendar.Calendar(firstweekday=0).itermonthdates(selected.year, selected.month))
        first = selected.replace(day=1)
        previous = (first - timedelta(days=1)).replace(day=1)
        next_period = (first + timedelta(days=32)).replace(day=1)
    else:
        first = selected - timedelta(days=selected.weekday())
        dates = [first + timedelta(days=i) for i in range(7)]
        previous, next_period = selected - timedelta(days=7), selected + timedelta(days=7)
    range_start = timezone.make_aware(datetime.combine(dates[0], time.min))
    range_end = timezone.make_aware(datetime.combine(dates[-1] + timedelta(days=1), time.min))
    occupied_visits = list(Booking.objects.exclude(status__in=['rejected', 'cancelled'])
        .filter(start__lt=range_end, end__gt=range_start).values_list('start', 'end'))
    occupied_blocks = list(PersonalBlock.objects.filter(start__lt=range_end, end__gt=range_start)
        .values_list('start', 'end'))
    week = []
    for day in dates:
        start = timezone.make_aware(datetime.combine(day, time.min))
        end = start + timedelta(days=1)
        count = sum(a < end and b > start for a, b in occupied_visits)
        personal = any(a < end and b > start for a, b in occupied_blocks)
        week.append({'date': day, 'count': count, 'personal': personal,
                     'busy': bool(count or personal), 'outside': day.month != selected.month,
                     'today': day == timezone.localdate()})
    visits = Booking.objects.filter(start__date=selected).exclude(status__in=['rejected', 'cancelled'])
    selected_start = timezone.make_aware(datetime.combine(selected, time.min))
    blocks = PersonalBlock.objects.filter(start__lt=selected_start + timedelta(days=1), end__gt=selected_start)
    timeline = sorted([{'start': b.start, 'booking': b} for b in visits] +
                      [{'start': b.start, 'block': b} for b in blocks], key=lambda x: x['start'])
    return render(request, 'dashboard.html', {'selected': selected, 'week': week, 'timeline': timeline,
        'previous': previous, 'next_week': next_period, 'calendar_view': mode,
        'pending': Booking.objects.filter(status='pending').count(),
        'change_count': ChangeRequest.objects.filter(status='pending').count(),
        'confirmed': visits.filter(status='confirmed').count()})


@staff
def inbox(request):
    return render(request, 'inbox.html', {'bookings': Booking.objects.filter(status='pending'),
                                        'changes': ChangeRequest.objects.filter(status='pending').select_related('booking')})


@staff
def visit(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    return render(request, 'visit.html', {'booking': booking, 'can_complete': booking.end <= timezone.now()})


@staff
@require_POST
def decision(request, pk):
    get_object_or_404(Booking, pk=pk)
    try:
        domain.decide_booking(pk, request.POST.get('action'))
        messages.success(request, 'Решение сохранено.')
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
    return redirect('visit', pk=pk)


@staff
@require_POST
def change_decision(request, pk):
    get_object_or_404(ChangeRequest, pk=pk)
    if request.POST.get('action') not in ('approve', 'reject'):
        return HttpResponse(status=400)
    try:
        domain.decide_change(pk, request.POST['action'] == 'approve')
        messages.success(request, 'Решение сохранено.')
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
    return redirect('inbox')


@staff
def clients(request):
    query = request.GET.get('q', '').strip()[:100]
    clients = Client.objects.all()
    if query:
        clients = clients.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    return render(request, 'clients.html', {'clients': clients[:100], 'query': query})


@staff
def client_edit(request, pk=None):
    client = get_object_or_404(Client, pk=pk) if pk else None
    form = ClientForm(request.POST or None, instance=client)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            domain.locked_studio()
            form.save()
        messages.success(request, 'Карточка сохранена.')
        return redirect('clients')
    return render(request, 'client.html', {'form': form, 'client': client})


@staff
def block(request):
    if request.method == 'POST' and request.POST.get('section') == 'template':
        return redirect('services')
    form = BlockForm(request.POST if request.method == 'POST' and request.POST.get('section') != 'template' else None)
    if request.method == 'POST' and request.POST.get('section') != 'template' and form.is_valid():
        try:
            domain.create_block(form.cleaned_data)
            messages.success(request, 'Личное дело добавлено. Клиенткам это время недоступно.')
            return redirect('dashboard')
        except ValidationError as error:
            form.add_error(None, error)
    return render(request, 'block.html', {'form': form, 'templates': PersonalTemplate.objects.all()})


@staff
@require_POST
def delete_block(request, pk):
    with transaction.atomic():
        domain.locked_studio()
        get_object_or_404(PersonalBlock, pk=pk).delete()
    messages.success(request, 'Время снова свободно.')
    return redirect('dashboard')


class WeeklyForm(forms.ModelForm):
    start = QuarterTimeField(label='Начало')
    end = QuarterTimeField(label='Окончание')
    class Meta:
        model = WeeklyDay
        fields = ['closed', 'start', 'end']
        widgets = {'start': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}), 'end': forms.TimeInput(format='%H:%M', attrs={'type': 'time'})}
    def clean(self):
        data = super().clean()
        if not data.get('closed') and data.get('start') and data.get('end') and data['end'] <= data['start']:
            raise ValidationError('Конец дня должен быть позже начала.')
        return data


@staff
def schedule(request):
    try:
        first = datetime.strptime(request.GET.get('date', ''), '%Y-%m-%d').date()
        if not 1900 <= first.year <= 2100:
            raise ValueError()
    except ValueError:
        first = timezone.localdate()
    service_id = request.GET.get('service', '')
    selected_service = Service.objects.filter(pk=int(service_id), active=True).first() if service_id.isdigit() and len(service_id) < 10 else None
    free_days = []
    for offset in range(7):
        day = first + timedelta(days=offset)
        if selected_service:
            windows = []
            for slot in domain.available_slots(day, selected_service.minutes):
                if windows and windows[-1][1] + timedelta(minutes=15) == slot:
                    windows[-1] = (windows[-1][0], slot)
                else:
                    windows.append((slot, slot))
        else:
            windows = domain.free_windows(day)
        free_days.append({'date': day, 'windows': windows, 'closed': not domain.day_window(day)})
    display = {'free_days': free_days, 'first': first, 'last': first + timedelta(days=6),
               'previous': first - timedelta(days=7), 'next': first + timedelta(days=7), 'snapshot_at': timezone.localtime(),
               'selected_service': selected_service, 'available_services': Service.objects.filter(active=True)}
    studio = Studio.objects.get(pk=1)
    display['share_data'] = {'name': studio.name, 'city': studio.city,
        'service': str(selected_service) if selected_service else 'Свободное рабочее время',
        'start_times': bool(selected_service), 'created': timezone.localtime().strftime('%d.%m.%Y %H:%M'),
        'url': settings.PUBLIC_BOOKING_URL,
        'days': [{'label': date_format(x['date'], 'j E, l'), 'windows': [a.strftime('%H:%M') if a == b else a.strftime('%H:%M') + '–' + b.strftime('%H:%M') for a, b in x['windows']], 'closed': x['closed']} for x in free_days]}
    if request.GET.get('share') == '1':
        return render(request, 'schedule_share.html', display)
    FormSet = forms.modelformset_factory(WeeklyDay, form=WeeklyForm, extra=0)
    week = FormSet(request.POST if request.POST.get('section') == 'week' else None,
                   queryset=WeeklyDay.objects.order_by('weekday'), prefix='week')
    studio_form = StudioForm(request.POST if request.POST.get('section') == 'studio' else None, instance=Studio.objects.get(pk=1))
    target_day = None
    if request.POST.get('section') == 'day':
        try:
            target_day = DayOverride.objects.filter(date=datetime.strptime(request.POST.get('date', ''), '%Y-%m-%d').date()).first()
        except ValueError:
            pass
    day_form = DayForm(request.POST if request.POST.get('section') == 'day' else None, instance=target_day)
    if request.method == 'POST':
        form = {'week': week, 'studio': studio_form, 'day': day_form}.get(request.POST.get('section'))
        if form and form.is_valid():
            with transaction.atomic():
                domain.locked_studio()
                form.save()
            messages.success(request, 'Расписание сохранено. Ранее созданные записи сохранены, даже если выходят за новые часы.')
            return redirect('schedule')
    names = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
    return render(request, 'schedule.html', {**display, 'week': week, 'week_rows': zip(names, week.forms),
        'studio_form': studio_form, 'day_form': day_form, 'overrides': DayOverride.objects.filter(date__gte=timezone.localdate())})


@staff
@require_POST
def delete_override(request, pk):
    with transaction.atomic():
        domain.locked_studio()
        get_object_or_404(DayOverride, pk=pk).delete()
    return redirect('schedule')


@staff
def services(request, pk=None, template_pk=None):
    service = get_object_or_404(Service, pk=pk) if pk else None
    personal = get_object_or_404(PersonalTemplate, pk=template_pk) if template_pk else None
    template_post = request.method == 'POST' and (personal is not None or request.POST.get('section') == 'template')
    personal_tab = bool(personal or template_post or request.GET.get('tab') == 'personal')
    template_form = PersonalTemplateForm(request.POST if template_post else None, prefix='template', instance=personal)
    if template_post and template_form.is_valid():
        with transaction.atomic():
            domain.locked_studio()
            template_form.save()
        messages.success(request, 'Шаблон личного дела сохранён.')
        return redirect('/studio/services/?tab=personal')
    form = ServiceForm(request.POST if request.method == 'POST' and not template_post else None, instance=service)
    if request.method == 'POST' and not template_post and form.is_valid():
        with transaction.atomic():
            domain.locked_studio()
            form.save()
        messages.success(request, 'Услуга сохранена. Длительность существующих визитов не изменилась.')
        return redirect('services')
    items = list(PersonalTemplate.objects.all() if personal_tab else Service.objects.all())
    for item in items:
        hours, minutes = divmod(item.minutes, 60)
        item.duration_label = ' '.join(part for part in (f'{hours} ч' if hours else '', f'{minutes} мин' if minutes else '') if part)
    return render(request, 'services.html', {'editor': template_form if personal_tab else form,
        'items': items, 'personal_tab': personal_tab, 'editing': personal or service,
        'service_count': Service.objects.count(), 'template_count': PersonalTemplate.objects.count(),
        'show_editor': bool(pk or template_pk or request.GET.get('new') == '1' or request.method == 'POST')})


def legal(request, document):
    return render(request, 'legal.html', {'document': document, 'consent_text': domain.consent_text(), 'consent_version': settings.CONSENT_VERSION})


def worker(request):
    return FileResponse(open(Path(settings.BASE_DIR) / 'static' / 'sw.js', 'rb'), content_type='application/javascript')


def offline(request):
    return render(request, 'offline.html')
