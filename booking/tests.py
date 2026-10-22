import io
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings, Client as HttpClient
from django.urls import reverse
from django.utils import timezone
from . import domain
from .models import (Studio, WeeklyDay, DayOverride, Service, Booking, Client,
                     PersonalBlock, ChangeRequest, Notification, Event)


def setup_calendar():
    Studio.objects.create(pk=1)
    for day in range(7):
        WeeklyDay.objects.create(weekday=day, start=time(9), end=time(19))
    return Service.objects.create(name='Маникюр', minutes=60)


def future(hour=10, minute=0, days=2):
    return timezone.make_aware(datetime.combine(timezone.localdate() + timedelta(days=days), time(hour, minute)))


def data(service, start=None, phone='+79990000001'):
    return {'services': [service], 'start': start or future(), 'name': 'Тестовая клиентка', 'phone': phone, 'comment': ''}


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, PUBLIC_BOOKING_ENABLED=True)
class CalendarTests(TestCase):
    def setUp(self):
        self.service = setup_calendar()

    def test_multiple_services_sum_and_pending_holds_time(self):
        extra = Service.objects.create(name='Дизайн', minutes=15)
        payload = data(self.service)
        payload['services'].append(extra)
        visit = domain.create_booking(payload)
        self.assertEqual(visit.end - visit.start, timedelta(minutes=75))
        self.assertEqual(visit.status, 'pending')
        self.assertEqual(visit.services.count(), 2)
        self.assertNotIn(future(10, 15), domain.available_slots(future().date(), 60))

    def test_overlap_rejected_no_extra_rows(self):
        domain.create_booking(data(self.service))
        with self.assertRaises(ValidationError):
            domain.create_booking(data(self.service, future(10, 15)))
        self.assertEqual(Booking.objects.count(), 1)

    def test_adjacent_visits_allowed(self):
        domain.create_booking(data(self.service))
        domain.create_booking(data(self.service, future(11)))
        self.assertEqual(Booking.objects.count(), 2)

    def test_off_grid_rejected(self):
        with self.assertRaises(ValidationError):
            domain.create_booking(data(self.service, future(10, 7)))

    def test_last_slot_must_fit(self):
        with self.assertRaises(ValidationError):
            domain.create_booking(data(self.service, future(18, 15)))

    def test_closed_override_and_reopening(self):
        DayOverride.objects.create(date=future().date(), closed=True)
        self.assertEqual(domain.available_slots(future().date(), 60), [])
        DayOverride.objects.update(closed=False, start=time(12), end=time(14))
        self.assertEqual(domain.available_slots(future().date(), 60)[0], future(12))

    def test_personal_block_hidden_and_busy(self):
        domain.create_block({'title': 'Секретная встреча', 'start': future(10), 'end': future(11, 30)})
        response = self.client.get('/slots/', {'date': future().date(), 'services': self.service.pk})
        self.assertNotContains(response, 'Секретная')
        self.assertNotIn('10:15', response.json()['slots'])
        self.assertIn('11:30', response.json()['slots'])

    def test_block_cannot_overwrite_booking(self):
        domain.create_booking(data(self.service))
        with self.assertRaises(ValidationError):
            domain.create_block({'title': 'Дело', 'start': future(10), 'end': future(12)})
        self.assertFalse(PersonalBlock.objects.exists())

    def test_move_waits_for_master(self):
        visit = domain.create_booking(data(self.service))
        change = domain.request_change(visit.pk, 'move', future(12))
        visit.refresh_from_db()
        self.assertEqual(visit.start, future())
        domain.decide_change(change.pk, True)
        visit.refresh_from_db()
        self.assertEqual(visit.start, future(12))
        self.assertEqual(visit.end, future(13))
        self.assertIn(future(), domain.available_slots(future().date(), 60))

    def test_conflicting_move_preserves_old_visit_and_request(self):
        visit = domain.create_booking(data(self.service))
        change = domain.request_change(visit.pk, 'move', future(12))
        domain.create_booking(data(self.service, future(12), '+79990000002'))
        with self.assertRaises(ValidationError):
            domain.decide_change(change.pk, True)
        visit.refresh_from_db(); change.refresh_from_db()
        self.assertEqual(visit.start, future())
        self.assertEqual(change.status, 'pending')

    def test_cancel_requires_approval(self):
        visit = domain.create_booking(data(self.service))
        change = domain.request_change(visit.pk, 'cancel')
        visit.refresh_from_db()
        self.assertEqual(visit.status, 'pending')
        domain.decide_change(change.pk, True)
        visit.refresh_from_db()
        self.assertEqual(visit.status, 'cancelled')
        self.assertIn(future(), domain.available_slots(future().date(), 60))

    def test_rejected_change_preserves_booking(self):
        visit = domain.create_booking(data(self.service))
        change = domain.request_change(visit.pk, 'cancel')
        domain.decide_change(change.pk, False)
        visit.refresh_from_db()
        self.assertEqual(visit.status, 'pending')

    def test_only_one_pending_change(self):
        visit = domain.create_booking(data(self.service))
        domain.request_change(visit.pk, 'cancel')
        with self.assertRaises(ValidationError):
            domain.request_change(visit.pk, 'move', future(12))

    def test_confirm_reuses_client_without_overwriting_notes_or_name(self):
        client = Client.objects.create(name='Проверенное имя', phone='+79990000001', notes='Личные заметки')
        visit = domain.create_booking(data(self.service))
        domain.decide_booking(visit.pk, 'confirm')
        visit.refresh_from_db(); client.refresh_from_db()
        self.assertEqual(visit.client, client)
        self.assertEqual(client.name, 'Проверенное имя')
        self.assertEqual(client.notes, 'Личные заметки')

    def test_repeated_confirmation_rejected(self):
        visit = domain.create_booking(data(self.service))
        domain.decide_booking(visit.pk, 'confirm')
        with self.assertRaises(ValidationError):
            domain.decide_booking(visit.pk, 'confirm')
        self.assertEqual(Client.objects.count(), 1)

    def test_rejection_releases_slot_and_change(self):
        visit = domain.create_booking(data(self.service))
        domain.request_change(visit.pk, 'cancel')
        domain.decide_booking(visit.pk, 'reject')
        self.assertFalse(ChangeRequest.objects.filter(status='pending').exists())
        self.assertIn(future(), domain.available_slots(future().date(), 60))

    def test_service_edit_does_not_change_history(self):
        visit = domain.create_booking(data(self.service))
        self.service.name = 'Другое'; self.service.minutes = 120; self.service.save()
        visit.refresh_from_db()
        self.assertEqual(visit.service_summary, 'Маникюр')
        self.assertEqual(visit.end - visit.start, timedelta(minutes=60))

    def test_horizon_and_past_rejected(self):
        for start in [future(days=-1), future(days=181)]:
            with self.assertRaises(ValidationError):
                domain.create_booking(data(self.service, start))

    def test_inactive_service_rejected(self):
        self.service.active = False; self.service.save()
        with self.assertRaises(ValidationError):
            domain.create_booking(data(self.service))

    def test_public_booking_post_and_consent(self):
        from .models import AccountPreferences
        user = get_user_model().objects.create_user('booking-client')
        AccountPreferences.objects.create(owner=user, name='Тест', phone='+79990000001')
        self.client.force_login(user)
        payload = {'services': [self.service.pk], 'date': future().date().isoformat(), 'time': '10:00', 'name': 'Тест', 'phone': '8 (999) 000-00-01'}
        response = self.client.post('/', payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Booking.objects.exists())
        payload['consent'] = 'on'
        response = self.client.post('/', payload)
        self.assertEqual(response.status_code, 302)
        visit = Booking.objects.get()
        self.assertEqual(visit.phone, '+79990000001')
        self.assertTrue(visit.consent_text)
        self.assertIsNotNone(visit.consent_at)

    def test_public_status_does_not_expose_phone_notes_or_name(self):
        visit = domain.create_booking(data(self.service))
        domain.decide_booking(visit.pk, 'confirm')
        Client.objects.update(notes='VERY PRIVATE')
        response = self.client.get(reverse('status', args=[visit.token]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, visit.phone)
        self.assertNotContains(response, visit.name)
        self.assertNotContains(response, 'VERY PRIVATE')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'strict-origin')

    def test_bad_token_is_404(self):
        self.assertEqual(self.client.get('/s/not-a-token/').status_code, 404)

    def test_private_pages_require_staff(self):
        urls = ['/studio/', '/studio/inbox/', '/studio/clients/', '/studio/schedule/', '/studio/services/', '/studio/block/']
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user('ordinary')
        self.client.force_login(user)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_all_staff_pages_render(self):
        user = get_user_model().objects.create_user('master', is_staff=True)
        self.client.force_login(user)
        visit = domain.create_booking(data(self.service))
        for url in ['/studio/', '/studio/inbox/', '/studio/clients/', '/studio/clients/new/', '/studio/schedule/', '/studio/services/', '/studio/block/', '/studio/new/', reverse('visit', args=[visit.pk])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_month_calendar_marks_visits_and_personal_days(self):
        self.client.force_login(get_user_model().objects.create_user('master', is_staff=True))
        visit = domain.create_booking(data(self.service))
        block_start = future(days=3)
        domain.create_block({'title': 'Дело', 'start': block_start, 'end': block_start + timedelta(days=1)})
        response = self.client.get('/studio/', {'view': 'month', 'date': visit.start.date()})
        days = {d['date']: d for d in response.context['week']}
        self.assertTrue(days[visit.start.date()]['busy'])
        self.assertTrue(days[block_start.date()]['personal'])
        self.assertTrue(days[(block_start + timedelta(days=1)).date()]['personal'])
        self.assertEqual(len(days) % 7, 0)
        self.assertEqual(self.client.get('/studio/').context['calendar_view'], 'month')

    def test_month_navigation_crosses_year(self):
        self.client.force_login(get_user_model().objects.create_user('master', is_staff=True))
        response = self.client.get('/studio/', {'view': 'month', 'date': '2026-12-15'})
        self.assertEqual(response.context['next_week'].isoformat(), '2027-01-01')
        response = self.client.get('/studio/', {'view': 'month', 'date': '2024-02-15'})
        self.assertIn('2024-02-29', [d['date'].isoformat() for d in response.context['week']])

    def test_csrf_required(self):
        browser = HttpClient(enforce_csrf_checks=True)
        self.assertEqual(browser.post('/', {}).status_code, 403)

    def test_valid_csrf_and_origin_allow_login(self):
        get_user_model().objects.create_user('master', password='test-login-password', is_staff=True)
        browser = HttpClient(enforce_csrf_checks=True)
        browser.get('/login/')
        token = browser.cookies['csrftoken'].value
        response = browser.post('/login/', {'username': 'master', 'password': 'test-login-password',
            'csrfmiddlewaretoken': token}, HTTP_ORIGIN='http://testserver')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(browser.get('/studio/').status_code, 200)

    def test_booking_date_uses_html_iso_format(self):
        from .models import AccountPreferences
        user = get_user_model().objects.create_user('date-client')
        AccountPreferences.objects.create(owner=user, name='Тест', phone='+79990000001')
        self.client.force_login(user)
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertContains(self.client.get('/'), f'value="{tomorrow}"')

    def test_anonymous_cannot_confirm(self):
        visit = domain.create_booking(data(self.service))
        response = self.client.post(reverse('decision', args=[visit.pk]), {'action': 'confirm'})
        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        self.assertEqual(visit.status, 'pending')

    def test_daily_override_upsert(self):
        self.client.force_login(get_user_model().objects.create_user('master', is_staff=True))
        payload = {'section': 'day', 'date': future().date(), 'closed': 'on', 'start': '09:00', 'end': '19:00'}
        self.client.post('/studio/schedule/', payload)
        payload['start'] = '10:00'
        self.client.post('/studio/schedule/', payload)
        self.assertEqual(DayOverride.objects.count(), 1)
        self.assertEqual(DayOverride.objects.get().start, time(10))

    def test_staff_adds_confirmed_visit(self):
        self.client.force_login(get_user_model().objects.create_user('master', is_staff=True))
        response = self.client.post('/studio/new/', {'services': [self.service.pk], 'date': future().date(), 'time': '10:00', 'name': 'Тест', 'phone': '+79990000001', 'consent': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Booking.objects.get().status, 'confirmed')

    @override_settings(PUBLIC_BOOKING_ENABLED=False)
    def test_public_booking_closed_by_default(self):
        from .models import AccountPreferences
        user = get_user_model().objects.create_user('closed-client')
        AccountPreferences.objects.create(owner=user, name='Тест', phone='+79990000001')
        self.client.force_login(user)
        self.client.post('/', {'services': [self.service.pk], 'date': future().date(), 'time': '10:00', 'name': 'Тест', 'phone': '+79990000001', 'consent': 'on'})
        self.assertFalse(Booking.objects.exists())

    def test_notification_inbox_persists_without_push_configuration(self):
        master = get_user_model().objects.create_user('notify-master', is_staff=True)
        domain.create_booking(data(self.service))
        self.assertEqual(Notification.objects.filter(owner=master, read_at__isnull=True).count(), 1)

    @override_settings(WEBPUSH_PUBLIC_KEY='')
    def test_retired_telegram_queue_is_never_delivered(self):
        Notification.objects.create()
        with patch('booking.notifications.send_push') as send:
            call_command('send_notifications', stdout=io.StringIO())
        send.assert_not_called()

    def test_login_rate_limit(self):
        for _ in range(10):
            self.client.post('/login/', {'username': 'none', 'password': 'wrong'})
        self.assertEqual(self.client.post('/login/', {}).status_code, 429)


class BackupTests(TestCase):
    def test_encrypted_round_trip_and_overwrite_protection(self):
        service = setup_calendar()
        user = get_user_model().objects.create_user('master', password='test-backup-password', is_staff=True)
        visit = domain.create_booking(data(service))
        domain.decide_booking(visit.pk, 'confirm')
        with tempfile.TemporaryDirectory() as folder, override_settings(BACKUP_KEY=Fernet.generate_key().decode()):
            path = Path(folder) / 'copy.nbak'
            call_command('backup', output=str(path), stdout=io.StringIO())
            self.assertNotIn(b'+79990000001', path.read_bytes())
            with self.assertRaises(CommandError):
                call_command('restore_backup', str(path), stdout=io.StringIO())
            Booking.objects.all().delete(); Client.objects.all().delete(); Service.objects.all().delete()
            Studio.objects.all().delete(); WeeklyDay.objects.all().delete(); Notification.objects.all().delete()
            user.delete()
            call_command('restore_backup', str(path), stdout=io.StringIO())
            restored = Booking.objects.get()
            self.assertEqual(restored.token, visit.token)
            self.assertEqual(restored.status, 'confirmed')
            self.assertEqual(restored.services.count(), 1)
            self.assertEqual(restored.events.count(), 2)
            self.assertTrue(get_user_model().objects.get().check_password('test-backup-password'))


class PostgresConcurrencyTests(TransactionTestCase):
    def test_two_simultaneous_bookings_only_one_succeeds(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row locks; SQLite is demo only.')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        service = setup_calendar()
        barrier = Barrier(2)
        def attempt():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                domain.create_booking(data(Service.objects.get(pk=service.pk)))
                return 'created'
            except ValidationError:
                return 'occupied'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, ['created', 'occupied'])
        self.assertEqual(Booking.objects.count(), 1)
