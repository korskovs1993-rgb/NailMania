"""Private inbox and durable, generic Web Push delivery. No third-party broker."""
import base64
import hashlib
import json
from datetime import timedelta
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import (AccountPreferences, Booking, Notification, PersonalBlock,
                     PrivateEvent, PushDelivery, PushSubscription)


def configured():
    return bool(settings.WEBPUSH_PUBLIC_KEY and settings.WEBPUSH_PRIVATE_KEY and settings.WEBPUSH_SUBJECT)


def validate_subscription(data):
    try:
        endpoint = data['endpoint']
        parsed = urlsplit(endpoint)
        # Exact provider allowlist prevents arbitrary URLs/SSRF. Redirects disabled at transport.
        if (len(endpoint) > 2048 or parsed.scheme != 'https' or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment
                or parsed.hostname not in settings.WEBPUSH_ALLOWED_HOSTS):
            raise ValueError()
        keys = data['keys']
        key = base64.urlsafe_b64decode(keys['p256dh'] + '=' * (-len(keys['p256dh']) % 4))
        auth = base64.urlsafe_b64decode(keys['auth'] + '=' * (-len(keys['auth']) % 4))
        if len(key) != 65 or len(auth) != 16 or len(keys['p256dh']) > 128 or len(keys['auth']) > 64:
            raise ValueError()
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), key)
        return {'endpoint': endpoint, 'endpoint_hash': hashlib.sha256(endpoint.encode()).hexdigest(),
                'p256dh': keys['p256dh'], 'auth': keys['auth']}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValidationError('Браузер прислал неподдерживаемую подписку.') from None


@transaction.atomic
def notify(owner_id, text, url, **kwargs):
    if not owner_id:
        return None
    if kwargs.get('dedupe_key'):
        notice, created = Notification.objects.get_or_create(
            dedupe_key=kwargs.pop('dedupe_key'), defaults=dict(owner_id=owner_id, text=text, url=url, **kwargs))
        if not created:
            return notice
    else:
        notice = Notification.objects.create(owner_id=owner_id, text=text, url=url, **kwargs)
    PushDelivery.objects.bulk_create([
        PushDelivery(notification=notice, subscription=sub)
        for sub in PushSubscription.objects.filter(owner_id=owner_id)
    ])
    return notice


def booking_event(booking, text, to_master):
    if to_master:
        for owner_id in get_user_model().objects.filter(is_staff=True, is_active=True).values_list('pk', flat=True):
            notify(owner_id, text, reverse('visit', args=[booking.pk]), booking=booking)
    elif booking.account_id:
        notify(booking.account_id, text, reverse('my_calendar'), booking=booking)


def wants_reminders(owner_id):
    return not AccountPreferences.objects.filter(owner_id=owner_id, reminders=False).exists()


def reminder_valid(notice, now):
    if not notice.reminder_start:
        return True
    if not wants_reminders(notice.owner_id) or notice.reminder_start <= now:
        return False
    if notice.booking_id:
        return Booking.objects.filter(pk=notice.booking_id, status='confirmed', start=notice.reminder_start).exists()
    if notice.private_event_id:
        return PrivateEvent.objects.filter(pk=notice.private_event_id, done=False, start=notice.reminder_start).exists()
    return PersonalBlock.objects.filter(pk=notice.personal_block_id, start=notice.reminder_start).exists()


def create_reminders():
    now = timezone.now()
    window = dict(start__gt=now, start__lte=now + timedelta(hours=1))
    staff_ids = list(get_user_model().objects.filter(is_staff=True, is_active=True).values_list('pk', flat=True))
    sources = [
        ('booking', Booking.objects.filter(status='confirmed', **window)),
        ('private_event', PrivateEvent.objects.filter(done=False, **window)),
        ('personal_block', PersonalBlock.objects.filter(**window)),
    ]
    for kind, records in sources:
        for item in records.iterator():
            owners = ([item.account_id] + staff_ids if kind == 'booking' else
                      [item.owner_id] if kind == 'private_event' else staff_ids)
            for owner_id in set(owners) - {None}:
                if not wants_reminders(owner_id):
                    continue
                label = 'Визит' if kind == 'booking' else 'Личное дело'
                at = timezone.localtime(item.start).strftime('%d.%m в %H:%M')
                url = reverse('dashboard' if owner_id in staff_ids else 'my_calendar')
                notify(owner_id, f'{label} {at}. До начала меньше часа.', url,
                       **{kind: item}, reminder_start=item.start,
                       dedupe_key=f'{kind}:{item.pk}:{item.start.isoformat()}:{owner_id}')


def send_push(subscription, tag):
    import requests
    from pywebpush import webpush

    class NoRedirectSession(requests.Session):
        def request(self, method, url, **kwargs):
            kwargs['allow_redirects'] = False
            return super().request(method, url, **kwargs)

    info = {'endpoint': subscription.endpoint, 'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth}}
    validate_subscription(info)  # Also validate restored/old records before network access.
    with NoRedirectSession() as session:
        response = webpush(info, data=json.dumps({'tag': tag}),
                          vapid_private_key=settings.WEBPUSH_PRIVATE_KEY,
                          vapid_claims={'sub': settings.WEBPUSH_SUBJECT},
                          timeout=10, ttl=300, requests_session=session)
        if not 200 <= response.status_code < 300:
            raise ValueError('Push provider rejected delivery')


def deliver_pending(limit=50):
    if not configured():
        return 0
    sent = 0
    for _ in range(limit):
        with transaction.atomic():
            delivery = (PushDelivery.objects.select_for_update(skip_locked=True)
                        .filter(sent_at__isnull=True, abandoned=False)
                        .filter(Q(next_attempt__isnull=True) | Q(next_attempt__lte=timezone.now()))
                        .order_by('pk').first())
            if delivery is None:
                break
            delivery.attempts += 1
            delivery.next_attempt = timezone.now() + timedelta(minutes=2)
            delivery.save(update_fields=['attempts', 'next_attempt'])
        notice = delivery.notification
        if (notice.read_at or not notice.owner or not notice.owner.is_active
                or notice.owner_id != delivery.subscription.owner_id
                or notice.created_at < timezone.now() - timedelta(days=1)
                or not reminder_valid(notice, timezone.now())):
            PushDelivery.objects.filter(pk=delivery.pk).update(abandoned=True)
            continue
        try:
            send_push(delivery.subscription, f'neilmania-{notice.pk}')
        except Exception as error:
            response = getattr(error, 'response', None)
            status = getattr(response, 'status_code', None)
            if status in (404, 410):
                delivery.subscription.delete()
                continue
            # Never store exception text: provider URLs contain capability tokens.
            PushDelivery.objects.filter(pk=delivery.pk).update(
                abandoned=delivery.attempts >= 8, last_error='Не удалось доставить push.',
                next_attempt=timezone.now() + timedelta(minutes=min(60, 2 ** delivery.attempts)))
        else:
            PushDelivery.objects.filter(pk=delivery.pk).update(sent_at=timezone.now(), last_error='')
            sent += 1
    return sent
