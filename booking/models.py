import secrets
from django.conf import settings
from django.db import models
from django.db.models import Q, F
from django.core.validators import MinValueValidator, MaxValueValidator


def public_token():
    return secrets.token_urlsafe(32)


class Studio(models.Model):
    name = models.CharField(max_length=100, default='Татьяна')
    city = models.CharField(max_length=100, default='Комсомольск-на-Амуре')
    step = models.PositiveSmallIntegerField(default=15, validators=[MinValueValidator(5), MaxValueValidator(60)])
    horizon = models.PositiveSmallIntegerField(default=60, validators=[MinValueValidator(1), MaxValueValidator(180)])
    lead_minutes = models.PositiveSmallIntegerField(default=60, validators=[MaxValueValidator(1440)])


class WeeklyDay(models.Model):
    weekday = models.PositiveSmallIntegerField(unique=True)
    closed = models.BooleanField(default=False)
    start = models.TimeField(default='09:00')
    end = models.TimeField(default='19:00')


class DayOverride(models.Model):
    date = models.DateField(unique=True)
    closed = models.BooleanField(default=False)
    start = models.TimeField(default='09:00')
    end = models.TimeField(default='19:00')


class Service(models.Model):
    name = models.CharField('Название', max_length=100)
    minutes = models.PositiveSmallIntegerField('Длительность, мин', default=60, validators=[MinValueValidator(15), MaxValueValidator(480)])
    active = models.BooleanField('Доступна для записи', default=True)
    class Meta:
        ordering = ['id']
    def __str__(self):
        return f'{self.name} · {self.minutes} мин'


class Client(models.Model):
    name = models.CharField('Имя', max_length=100)
    phone = models.CharField('Телефон', max_length=16, unique=True)
    notes = models.TextField('Заметки мастера', blank=True, max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['name']


class Booking(models.Model):
    account = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='appointments')
    STATES = [('pending', 'Ожидает подтверждения'), ('confirmed', 'Подтверждена'),
              ('rejected', 'Отклонена'), ('cancelled', 'Отменена'), ('done', 'Визит завершён')]
    token = models.CharField(max_length=64, default=public_token, unique=True, editable=False)
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.SET_NULL, related_name='bookings')
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=16)
    comment = models.TextField(blank=True, max_length=1000)
    services = models.ManyToManyField(Service)
    service_summary = models.CharField(max_length=1200)
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField()
    status = models.CharField(max_length=12, choices=STATES, default='pending')
    consent_at = models.DateTimeField(null=True)
    consent_version = models.CharField(max_length=40, blank=True)
    consent_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['start']
        constraints = [models.CheckConstraint(condition=Q(end__gt=F('start')), name='booking_positive_duration')]


class PersonalTemplate(models.Model):
    icon = models.CharField('Иконка', max_length=8, blank=True, default='', choices=[('', '✦ Без иконки'), ('🛍', '🛍 Магазин'), ('🏋️', '🏋️ Спорт'), ('🏥', '🏥 Поликлиника'), ('☕', '☕ Отдых'), ('📚', '📚 Учёба'), ('♡', '♡ Для себя')])
    title = models.CharField('Название шаблона', max_length=150, unique=True)
    minutes = models.PositiveSmallIntegerField('Длительность, мин', default=60, validators=[MinValueValidator(15), MaxValueValidator(1440)])

    class Meta:
        ordering = ['title']

    def __str__(self):
        return f'{self.title} · {self.minutes} мин'


class PersonalBlock(models.Model):
    title = models.CharField('Личное дело', max_length=150)
    start = models.DateTimeField('Начало')
    end = models.DateTimeField('Окончание')
    class Meta:
        ordering = ['start']
        constraints = [models.CheckConstraint(condition=Q(end__gt=F('start')), name='block_positive_duration')]


class ChangeRequest(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='changes')
    kind = models.CharField(max_length=8, choices=[('cancel', 'Отмена'), ('move', 'Перенос')])
    proposed_start = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, default='pending', choices=[('pending', 'Ожидает решения'), ('approved', 'Одобрено'), ('rejected', 'Отклонено')])
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['booking'], condition=Q(status='pending'), name='one_pending_change')]


class Event(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='events')
    text = models.CharField(max_length=250)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['created_at']


class Notification(models.Model):
    # Rows without an owner are the retired Telegram queue and are never delivered.
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE, related_name='notifications')
    text = models.CharField(max_length=250, blank=True)
    url = models.CharField(max_length=250, default='/notifications/')
    read_at = models.DateTimeField(null=True)
    dedupe_key = models.CharField(max_length=180, unique=True, null=True)
    booking = models.ForeignKey(Booking, null=True, on_delete=models.CASCADE)
    private_event = models.ForeignKey('PrivateEvent', null=True, on_delete=models.CASCADE)
    personal_block = models.ForeignKey(PersonalBlock, null=True, on_delete=models.CASCADE)
    reminder_start = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=100, blank=True)


class RateBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    hits = models.PositiveIntegerField(default=0)
    expires = models.DateTimeField()


class PrivateEvent(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='private_events')
    title = models.CharField('Название', max_length=150)
    start = models.DateTimeField('Начало')
    end = models.DateTimeField('Окончание')
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ['start']
        constraints = [models.CheckConstraint(condition=Q(end__gt=F('start')), name='private_event_positive_duration')]


class AccountPreferences(models.Model):
    reminders = models.BooleanField('Напоминать за час до визитов и личных дел', default=True)
    glass_mode = models.CharField('Прозрачность', max_length=12, default='glass', choices=[('glass', 'Прозрачный'), ('calm', 'Спокойный')])
    color_mode = models.CharField('Режим экрана', max_length=12, default='palette', choices=[('palette', 'Как в палитре'), ('light', 'Светлый'), ('dark', 'Тёмный'), ('system', 'Как на устройстве')])
    large_text = models.BooleanField('Увеличенный текст', default=False)
    name = models.CharField('Имя', max_length=100, blank=True)
    phone = models.CharField('Телефон', max_length=32, blank=True)
    consent_at = models.DateTimeField(null=True, blank=True)
    consent_text = models.TextField(blank=True)
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='preferences')
    theme = models.CharField(max_length=12, default='lavender', choices=[(x, label) for x, label in [('rose', 'Пудра'), ('sage', 'Шалфей'), ('lavender', 'Лаванда'), ('sand', 'Песок'), ('ocean', 'Океан'), ('night', 'Графит')]])
    design = models.CharField(max_length=12, default='glass', choices=[('glass', 'Glass'), ('classic', 'Классический')])


class PushSubscription(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    endpoint = models.TextField()
    endpoint_hash = models.CharField(max_length=64, unique=True)
    p256dh = models.CharField(max_length=128)
    auth = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class PushDelivery(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE)
    subscription = models.ForeignKey(PushSubscription, on_delete=models.CASCADE)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt = models.DateTimeField(null=True)
    sent_at = models.DateTimeField(null=True)
    abandoned = models.BooleanField(default=False)
    last_error = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['notification', 'subscription'], name='one_push_per_device')]
