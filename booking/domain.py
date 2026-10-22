"""All calendar mutations take the same PostgreSQL row lock before validating.

SQLite exists only for a single-process local demonstration, not shared hosting.
"""
from datetime import datetime, timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import (Studio, WeeklyDay, DayOverride, Booking, PersonalBlock,
                     Client, ChangeRequest, Event)

ACTIVE = ['pending', 'confirmed']


def locked_studio():
    return Studio.objects.select_for_update().get(pk=1)


def consent_text():
    return (f'Я даю {settings.OPERATOR_NAME or "[оператор не настроен]"}, '
            f'адрес: {settings.OPERATOR_ADDRESS or "[адрес не настроен]"}, '
            'согласие на обработку имени, телефона, выбранных услуг, времени визита '
            'и добровольно указанного комментария для записи, связи по записи и ведения '
            'истории посещений. Действия: сбор, запись, систематизация, накопление, '
            'хранение, уточнение, извлечение, использование, блокирование, удаление '
            'и уничтожение, с использованием средств автоматизации. '
            'Срок: до отзыва согласия, но не более 1 года после последнего визита. '
            f'Отзыв и обращения: {settings.PRIVACY_CONTACT or "[контакт не настроен]"}. '
            'Согласие не включает рекламу и распространение данных.')


def day_window(day):
    rule = DayOverride.objects.filter(date=day).first()
    if rule is None:
        rule = WeeklyDay.objects.filter(weekday=day.weekday()).first()
    if rule is None or rule.closed:
        return None
    return tuple(timezone.make_aware(datetime.combine(day, t)) for t in (rule.start, rule.end))


def occupied(start, end, exclude=None):
    visits = Booking.objects.filter(status__in=ACTIVE, start__lt=end, end__gt=start)
    if exclude:
        visits = visits.exclude(pk=exclude)
    return visits.exists() or PersonalBlock.objects.filter(start__lt=end, end__gt=start).exists()


def validate_slot(start, minutes, studio, exclude=None):
    start = timezone.localtime(start)
    day = start.date()
    now = timezone.now()
    if start < now + timedelta(minutes=studio.lead_minutes):
        raise ValidationError('Это время уже слишком близко. Выбери более позднее.')
    if day > timezone.localdate() + timedelta(days=studio.horizon):
        raise ValidationError('Эта дата ещё не открыта для записи.')
    window = day_window(day)
    end = start + timedelta(minutes=minutes)
    if not window or start < window[0] or end > window[1]:
        raise ValidationError('Визит не помещается в рабочее время.')
    if start.second or start.microsecond or start.minute % 15:
        raise ValidationError('Выбери время из доступных интервалов.')
    if occupied(start, end, exclude):
        raise ValidationError('Это время уже занято. Выбери другой интервал.')
    return end


def available_slots(day, minutes, exclude=None):
    studio = Studio.objects.get(pk=1)
    window = day_window(day)
    if not window or day > timezone.localdate() + timedelta(days=studio.horizon):
        return []
    start, close = window
    # Old schedules may start between quarter hours; keep offered times aligned.
    if start.minute % 15 or start.second or start.microsecond:
        start = start.replace(second=0, microsecond=0) + timedelta(minutes=15 - start.minute % 15)
    earliest = timezone.now() + timedelta(minutes=studio.lead_minutes)
    busy = list(Booking.objects.filter(status__in=ACTIVE, start__lt=close, end__gt=start)
                .exclude(pk=exclude).values_list('start', 'end'))
    busy += list(PersonalBlock.objects.filter(start__lt=close, end__gt=start).values_list('start', 'end'))
    slots = []
    while start + timedelta(minutes=minutes) <= close:
        end = start + timedelta(minutes=minutes)
        if start >= earliest and not any(start < b and end > a for a, b in busy):
            slots.append(start)
        start += timedelta(minutes=15)
    return slots


def free_windows(day):
    """Merge bookable quarter hours, excluding bookings and personal blocks."""
    result = []
    for start in available_slots(day, 15):
        end = start + timedelta(minutes=15)
        if result and result[-1][1] == start:
            result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return result


def record(booking, text, notify=False):
    Event.objects.create(booking=booking, text=text)
    from .notifications import booking_event
    booking_event(booking, text, to_master=notify)


@transaction.atomic
def create_booking(data, staff_entry=False):
    studio = locked_studio()
    # Re-read services under the calendar lock; stale forms cannot bypass edits.
    from .models import Service
    requested = list(data['services'])
    services = list(Service.objects.filter(pk__in=[s.pk for s in requested], active=True))
    if not services or len(services) != len(requested):
        raise ValidationError('Список услуг изменился. Обнови страницу.')
    minutes = sum(s.minutes for s in services)
    end = validate_slot(data['start'], minutes, studio)
    booking = Booking.objects.create(
        name=data['name'], phone=data['phone'], comment=data.get('comment', ''),
        start=data['start'], end=end, service_summary=' + '.join(s.name for s in services),
        consent_at=timezone.now(), consent_version=settings.CONSENT_VERSION,
        consent_text=('Внесено мастером: мастер подтвердил получение отдельного согласия клиентки.\n' if staff_entry else '') + consent_text())
    booking.services.set(services)
    record(booking, 'Заявка получена. Ожидает подтверждения Татьяной.', notify=not staff_entry)
    return booking


@transaction.atomic
def request_change(booking_id, kind, start=None):
    studio = locked_studio()
    booking = Booking.objects.get(pk=booking_id)
    if booking.status not in ACTIVE or booking.start <= timezone.now():
        raise ValidationError('Изменить эту запись через ссылку уже нельзя. Свяжись с мастером.')
    if booking.changes.filter(status='pending').exists():
        raise ValidationError('Предыдущий запрос ещё ожидает решения.')
    if kind == 'move':
        if start is None or start == booking.start:
            raise ValidationError('Выбери новое время.')
        validate_slot(start, int((booking.end - booking.start).total_seconds() / 60), studio, booking.pk)
    elif kind != 'cancel':
        raise ValidationError('Неизвестный тип запроса.')
    change = ChangeRequest.objects.create(booking=booking, kind=kind, proposed_start=start if kind == 'move' else None)
    record(booking, 'Запрошен перенос.' if kind == 'move' else 'Запрошена отмена.', notify=True)
    return change


@transaction.atomic
def decide_booking(pk, action):
    locked_studio()
    booking = Booking.objects.get(pk=pk)
    if action in ('confirm', 'reject') and booking.status == 'pending':
        if action == 'confirm':
            if booking.start <= timezone.now():
                raise ValidationError('Время заявки уже прошло. Отклони её и создай новую запись.')
            if occupied(booking.start, booking.end, booking.pk):
                raise ValidationError('Время занято: подтверждение невозможно.')
            client, _ = Client.objects.get_or_create(phone=booking.phone, defaults={'name': booking.name})
            booking.client = client
            booking.status = 'confirmed'
        else:
            booking.status = 'rejected'
    elif action == 'done' and booking.status == 'confirmed' and booking.end <= timezone.now():
        booking.status = 'done'
    else:
        raise ValidationError('Статус уже изменился или действие пока недоступно.')
    booking.save()
    if booking.status not in ACTIVE:
        booking.changes.filter(status='pending').update(status='rejected')
    record(booking, booking.get_status_display())


@transaction.atomic
def decide_change(pk, approve):
    studio = locked_studio()
    change = ChangeRequest.objects.select_related('booking').get(pk=pk)
    booking = change.booking
    if change.status != 'pending' or booking.status not in ACTIVE:
        raise ValidationError('Запрос уже обработан.')
    if approve:
        if change.kind == 'cancel':
            booking.status = 'cancelled'
        else:
            minutes = int((booking.end - booking.start).total_seconds() / 60)
            end = validate_slot(change.proposed_start, minutes, studio, booking.pk)
            booking.start, booking.end = change.proposed_start, end
        booking.save()
    change.status = 'approved' if approve else 'rejected'
    change.save()
    record(booking, f'{change.get_kind_display()}: {change.get_status_display().lower()}.')


@transaction.atomic
def create_block(data):
    locked_studio()
    if data['end'] <= data['start']:
        raise ValidationError('Окончание должно быть позже начала.')
    if occupied(data['start'], data['end']):
        raise ValidationError('Здесь уже есть запись или личное дело. Сначала освободи время.')
    return PersonalBlock.objects.create(**data)
