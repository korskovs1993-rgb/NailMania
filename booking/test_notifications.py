import base64
import io
import json
import tempfile
from pathlib import Path
from datetime import timedelta
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pywebpush import WebPushException

from . import domain, notifications
from .models import AccountPreferences, Booking, Notification, PersonalBlock, PrivateEvent, PushDelivery, PushSubscription
from .tests import setup_calendar, data, future


def subscription_data(endpoint='https://web.push.apple.com/test-device'):
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=').decode()
    return {'endpoint': endpoint, 'keys': {'p256dh': encode(point), 'auth': encode(b'x' * 16)}}


@override_settings(SECURE_SSL_REDIRECT=False, WEBPUSH_PUBLIC_KEY='test-public',
                   WEBPUSH_PRIVATE_KEY='test-private', WEBPUSH_SUBJECT='mailto:test@example.org')
class NotificationTests(TestCase):
    def setUp(self):
        self.service = setup_calendar()
        self.master = get_user_model().objects.create_user('master', is_staff=True)
        self.user = get_user_model().objects.create_user('client')
        self.other = get_user_model().objects.create_user('other')

    def subscribe(self, user=None):
        return PushSubscription.objects.create(owner=user or self.user, **notifications.validate_subscription(subscription_data()))

    def booking(self):
        booking = domain.create_booking(data(self.service))
        booking.account = self.user
        booking.save(update_fields=['account'])
        return booking

    def test_recipients_for_booking_confirmation_and_change(self):
        visit = self.booking()
        self.assertEqual(list(Notification.objects.values_list('owner_id', flat=True)), [self.master.pk])
        domain.decide_booking(visit.pk, 'confirm')
        self.assertEqual(Notification.objects.filter(owner=self.user).get().text, 'Подтверждена')
        change = domain.request_change(visit.pk, 'cancel')
        self.assertEqual(Notification.objects.filter(owner=self.master).count(), 2)
        domain.decide_change(change.pk, True)
        self.assertIn('одобрено', Notification.objects.filter(owner=self.user).latest('pk').text)
        self.assertFalse(Notification.objects.filter(owner=self.other).exists())

    def test_staff_entry_does_not_alert_master_about_own_action(self):
        domain.create_booking(data(self.service), staff_entry=True)
        self.assertFalse(Notification.objects.exists())

    def test_rollback_discards_notifications_and_delivery(self):
        self.subscribe(self.master)
        with self.assertRaises(ValueError), transaction.atomic():
            self.booking()
            raise ValueError()
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(PushDelivery.objects.exists())

    def test_inbox_badge_and_read_are_owner_scoped(self):
        own = notifications.notify(self.user.pk, 'Моё сообщение', '/my/')
        other = notifications.notify(self.other.pk, 'Секрет другого', '/my/')
        self.client.force_login(self.user)
        response = self.client.get(reverse('notifications'))
        self.assertContains(response, own.text)
        self.assertNotContains(response, other.text)
        own.refresh_from_db()
        self.assertIsNone(own.read_at)
        self.assertEqual(self.client.get(reverse('notification_state')).json()['unread'], 1)
        self.assertEqual(self.client.get(reverse('notifications_read')).status_code, 405)
        self.assertEqual(self.client.post(reverse('notifications_read'), {'id': other.pk}).status_code, 404)
        self.client.post(reverse('notifications_read'))
        own.refresh_from_db(); other.refresh_from_db()
        self.assertIsNotNone(own.read_at)
        self.assertIsNone(other.read_at)

    def test_anonymous_and_csrf_protection(self):
        self.assertEqual(self.client.get(reverse('notifications')).status_code, 302)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        self.assertEqual(secure.post(reverse('push_subscription'), '{}', content_type='application/json').status_code, 403)

    def test_subscription_ownership_revoke_and_no_reassignment(self):
        self.client.force_login(self.user)
        payload = subscription_data()
        url = reverse('push_subscription')
        self.assertEqual(self.client.post(url, payload, content_type='application/json').status_code, 200)
        self.assertEqual(self.client.post(url, payload, content_type='application/json').status_code, 200)
        self.assertEqual(PushSubscription.objects.count(), 1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, payload, content_type='application/json').status_code, 409)
        self.client.post(url, dict(payload, action='remove'), content_type='application/json')
        self.assertEqual(PushSubscription.objects.count(), 1)
        self.client.force_login(self.user)
        self.client.post(url, dict(payload, action='remove'), content_type='application/json')
        self.assertFalse(PushSubscription.objects.exists())

    def test_subscription_validation_blocks_ssrf_and_bad_keys(self):
        for endpoint in ['http://web.push.apple.com/a', 'https://127.0.0.1/a', 'https://web.push.apple.com.evil.test/a', 'https://user@web.push.apple.com/a', 'https://web.push.apple.com:444/a']:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValidationError):
                notifications.validate_subscription(subscription_data(endpoint))
        payload = subscription_data()
        payload['keys']['p256dh'] = 'garbage'
        with self.assertRaises(ValidationError):
            notifications.validate_subscription(payload)

    def test_delivery_retries_and_does_not_duplicate_success(self):
        self.subscribe()
        notifications.notify(self.user.pk, 'Личное сообщение', '/my/')
        with patch('booking.notifications.send_push', side_effect=OSError('secret-url')):
            self.assertEqual(notifications.deliver_pending(), 0)
        delivery = PushDelivery.objects.get()
        self.assertEqual(delivery.attempts, 1)
        self.assertGreater(delivery.next_attempt, timezone.now())
        self.assertNotIn('secret-url', delivery.last_error)
        PushDelivery.objects.update(next_attempt=timezone.now())
        with patch('booking.notifications.send_push') as send:
            self.assertEqual(notifications.deliver_pending(), 1)
            self.assertEqual(notifications.deliver_pending(), 0)
        send.assert_called_once()

    def test_expired_subscription_removed(self):
        self.subscribe()
        notifications.notify(self.user.pk, 'Сообщение', '/my/')
        with patch('booking.notifications.send_push', side_effect=WebPushException('Gone', response=Mock(status_code=410))):
            notifications.deliver_pending()
        self.assertFalse(PushSubscription.objects.exists())
        self.assertEqual(Notification.objects.count(), 1)

    def test_transport_payload_contains_only_tag(self):
        sub = self.subscribe()
        with patch('pywebpush.webpush', return_value=Mock(status_code=201)) as send:
            notifications.send_push(sub, 'neilmania-123')
        self.assertEqual(json.loads(send.call_args.kwargs['data']), {'tag': 'neilmania-123'})
        session = send.call_args.kwargs['requests_session']
        with patch('requests.Session.request', return_value=Mock(status_code=302)) as transport:
            session.request('POST', sub.endpoint)
        self.assertFalse(transport.call_args.kwargs['allow_redirects'])

    def test_generated_keys_encrypt_and_sign_without_real_network(self):
        sub = self.subscribe()
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            output = io.StringIO()
            call_command('init_push', stdout=output)
            self.assertNotIn('BEGIN PRIVATE KEY', output.getvalue())
            with self.assertRaises(CommandError):
                call_command('init_push', stdout=io.StringIO())
            with override_settings(WEBPUSH_PRIVATE_KEY=str(Path(folder) / '.keys' / 'webpush.pem')):
                with patch('requests.Session.request', return_value=Mock(status_code=201)) as send:
                    notifications.send_push(sub, 'neilmania-123')
            payload = send.call_args.kwargs
            self.assertNotIn(b'neilmania-123', payload['data'])
            self.assertEqual(payload['headers']['content-encoding'], 'aes128gcm')
            self.assertFalse(payload['allow_redirects'])

    def test_reminders_once_and_skip_changed_done_or_opted_out(self):
        self.subscribe()
        now = timezone.now()
        event = PrivateEvent.objects.create(owner=self.user, title='Личное', start=now+timedelta(minutes=30), end=now+timedelta(hours=1))
        PrivateEvent.objects.create(owner=self.other, title='Готово', done=True, start=event.start, end=event.end)
        notifications.create_reminders(); notifications.create_reminders()
        self.assertEqual(Notification.objects.filter(owner=self.user).count(), 1)
        self.assertFalse(Notification.objects.filter(owner=self.other).exists())
        event.done = True; event.save()
        with patch('booking.notifications.send_push') as send:
            notifications.deliver_pending()
        send.assert_not_called()
        self.assertTrue(PushDelivery.objects.get().abandoned)
        AccountPreferences.objects.create(owner=self.user, reminders=False)
        event.done = False; event.start += timedelta(minutes=5); event.save()
        notifications.create_reminders()
        self.assertEqual(Notification.objects.filter(owner=self.user).count(), 1)

    def test_confirmed_visits_and_master_blocks_have_reminders(self):
        now = timezone.now()
        visit = Booking.objects.create(account=self.user, name='Test', phone='+79990000001', start=now+timedelta(minutes=40), end=now+timedelta(hours=2), status='confirmed')
        PersonalBlock.objects.create(title='Личное', start=now+timedelta(minutes=50), end=now+timedelta(hours=3))
        notifications.create_reminders()
        self.assertEqual(Notification.objects.filter(owner=self.user).count(), 1)
        self.assertEqual(Notification.objects.filter(owner=self.master).count(), 2)
        notice = Notification.objects.get(owner=self.user)
        visit.status = 'cancelled'; visit.save()
        self.assertFalse(notifications.reminder_valid(notice, now))

    def test_read_notice_is_not_pushed(self):
        self.subscribe()
        notice = notifications.notify(self.user.pk, 'Уже прочитано', '/my/')
        notice.read_at = timezone.now(); notice.save()
        with patch('booking.notifications.send_push') as send:
            notifications.deliver_pending()
        send.assert_not_called()

    def test_disconnect_all_does_not_touch_other_users(self):
        self.subscribe()
        other = PushSubscription.objects.create(owner=self.other, **notifications.validate_subscription(subscription_data('https://web.push.apple.com/other')))
        self.client.force_login(self.user)
        self.client.post(reverse('push_disconnect_all'))
        self.assertEqual(list(PushSubscription.objects.values_list('pk', flat=True)), [other.pk])

    def test_test_push_rate_limited_and_preferences_independent(self):
        self.subscribe()
        self.client.force_login(self.user)
        self.client.post(reverse('push_test'))
        self.client.post(reverse('push_test'))
        self.assertEqual(Notification.objects.count(), 1)
        self.client.post(reverse('notification_options'))
        prefs = AccountPreferences.objects.get(owner=self.user)
        self.assertFalse(prefs.reminders)
        self.assertEqual(prefs.design, 'glass')
