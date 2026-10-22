from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import PrivateEvent, AccountPreferences, Booking
from .tests import setup_calendar, future, data
from . import domain


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, PUBLIC_BOOKING_ENABLED=True)
class ClientAccountTests(TestCase):
    def setUp(self):
        self.service = setup_calendar()
        self.owner = get_user_model().objects.create_user('first', password='test-password-456')
        self.other = get_user_model().objects.create_user('second')
        self.event = PrivateEvent.objects.create(owner=self.owner, title='Private gym', start=future(), end=future(11))

    def test_anonymous_requires_login(self):
        for route in ['my_calendar', 'private_event', 'preferences']:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route)).status_code, 302)

    def test_other_account_cannot_read_edit_or_complete_event(self):
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('my_calendar')), self.event.title)
        url = reverse('private_event_edit', args=[self.event.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'title': 'stolen'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('toggle_event', args=[self.event.pk]), {'done': '1'}).status_code, 404)
        self.event.refresh_from_db()
        self.assertFalse(self.event.done)
        self.assertEqual(self.event.title, 'Private gym')

    def test_create_owner_is_session_and_quarter_hour_validated(self):
        self.client.force_login(self.owner)
        payload = {'title': 'Shop', 'owner': self.other.pk, 'start_0': future().date(), 'end_0': future().date(), 'start_1': '13:15', 'end_1': '14:00'}
        self.assertEqual(self.client.post(reverse('private_event'), payload).status_code, 302)
        self.assertEqual(PrivateEvent.objects.get(title='Shop').owner, self.owner)
        payload.update(title='Invalid', start_1='13:17')
        self.assertEqual(self.client.post(reverse('private_event'), payload).status_code, 200)
        self.assertFalse(PrivateEvent.objects.filter(title='Invalid').exists())

    def test_personal_event_does_not_block_master_and_can_be_completed(self):
        self.assertIn(future(), domain.available_slots(future().date(), 60))
        self.client.force_login(self.owner)
        response = self.client.get(reverse('my_calendar'), {'date': future().date()})
        self.assertContains(response, self.event.title)
        self.assertIn('no-store', response['Cache-Control'])
        self.client.post(reverse('toggle_event', args=[self.event.pk]), {'done': '1'})
        self.event.refresh_from_db()
        self.assertTrue(self.event.done)

    def test_settings_persist_only_for_current_account(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse('preferences'), {'theme': 'ocean', 'design': 'classic', 'glass_mode': 'glass', 'color_mode': 'palette', 'owner': self.other.pk}).status_code, 302)
        self.assertEqual(AccountPreferences.objects.get(owner=self.owner).theme, 'ocean')
        self.assertFalse(AccountPreferences.objects.filter(owner=self.other).exists())
        self.assertContains(self.client.get(reverse('my_calendar')), 'data-theme="ocean"')
        self.assertContains(self.client.get(reverse('preferences')), 'data-design="classic"')

    def test_signup_records_consent_and_never_grants_staff(self):
        payload = {'username': 'new-client', 'name': 'Анна', 'phone': '89990000001', 'password1': 'long-random-pass-6432', 'password2': 'long-random-pass-6432', 'is_staff': '1'}
        self.client.post(reverse('signup'), payload)
        self.assertFalse(get_user_model().objects.filter(username='new-client').exists())
        payload['consent'] = 'on'
        self.assertEqual(self.client.post(reverse('signup'), payload).status_code, 302)
        user = get_user_model().objects.get(username='new-client')
        self.assertFalse(user.is_staff)
        self.assertTrue(user.check_password(payload['password1']))
        self.assertIsNotNone(user.preferences.consent_at)
        self.assertTrue(user.preferences.consent_text)
        self.assertEqual(user.preferences.name, 'Анна')
        self.assertEqual(user.preferences.phone, '+79990000001')
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_booking_linked_to_authenticated_account_only(self):
        AccountPreferences.objects.create(owner=self.owner, name='Profile name', phone='+79990000002')
        self.client.force_login(self.owner)
        payload = {'services': [self.service.pk], 'date': future().date(), 'time': '10:00', 'name': 'Test', 'phone': '+79990000001', 'consent': 'on', 'account': self.other.pk}
        self.assertEqual(self.client.post(reverse('book'), payload).status_code, 302)
        visit = Booking.objects.get()
        self.assertEqual(visit.account, self.owner)
        self.assertEqual(visit.name, 'Profile name')
        self.assertEqual(visit.phone, '+79990000002')
        self.assertContains(self.client.get(reverse('my_calendar')), visit.token)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('my_calendar')), visit.token)

    def test_staff_cannot_access_private_event(self):
        self.other.is_staff = True
        self.other.save()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('private_event_edit', args=[self.event.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), self.event.title)

    def test_client_login_goes_to_personal_calendar(self):
        response = self.client.post(reverse('client_login'), {'username': 'first', 'password': 'test-password-456'})
        self.assertRedirects(response, reverse('my_calendar'))

    def test_anonymous_booking_post_cannot_create_visit(self):
        response = self.client.post(reverse('book'), {'services': [self.service.pk], 'date': future().date(), 'time': '10:00', 'name': 'Forged', 'phone': '+79990000001', 'consent': 'on'})
        self.assertRedirects(response, '/account/login/?next=/')
        self.assertFalse(Booking.objects.exists())
        response = self.client.get(reverse('book'))
        self.assertContains(response, 'Войди, чтобы записаться')
        self.assertContains(response, 'name="services"')
        self.assertNotContains(response, 'Отправить заявку →')

    def test_missing_profile_requires_completion_then_booking_has_no_identity_fields(self):
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.post(reverse('book'), {}), reverse('preferences'))
        self.assertFalse(Booking.objects.exists())
        response = self.client.post(reverse('preferences'), {'section': 'profile', 'name': 'Анна', 'phone': '89991234567'})
        self.assertEqual(response.status_code, 302)
        prefs = AccountPreferences.objects.get(owner=self.owner)
        self.assertEqual(prefs.phone, '+79991234567')
        response = self.client.get(reverse('book'))
        self.assertContains(response, 'Анна')
        self.assertContains(response, '+79991234567')
        self.assertNotContains(response, 'name="name"')
        self.assertNotContains(response, 'name="phone"')
        self.assertNotContains(response, 'ШАГ 03')

    def test_profile_rejects_invalid_phone_and_cannot_edit_other_account(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('preferences'), {'section': 'profile', 'name': 'Anna', 'phone': '123', 'owner': self.other.pk})
        self.assertEqual(AccountPreferences.objects.get(owner=self.owner).phone, '')
        self.assertFalse(AccountPreferences.objects.filter(owner=self.other).exists())

    def test_login_preserves_safe_booking_destination(self):
        response = self.client.post(reverse('client_login'), {'username': 'first', 'password': 'test-password-456', 'next': '/'})
        self.assertRedirects(response, '/')
        response = self.client.post(reverse('client_login'), {'username': 'first', 'password': 'test-password-456', 'next': 'https://example.com/'})
        self.assertRedirects(response, reverse('my_calendar'))
