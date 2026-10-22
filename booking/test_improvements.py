from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings, Client as HttpClient
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils import timezone
from . import domain, drafts
from .models import AccountPreferences, Booking, PrivateEvent, Service, Client
from .tests import setup_calendar, future, data


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, PUBLIC_BOOKING_ENABLED=True)
class ImprovementsTests(TestCase):
    def setUp(self):
        self.service = setup_calendar()
        self.password = 'A-long-test-password-742!'
        self.user = get_user_model().objects.create_user('anna', email='anna@example.com', password=self.password)
        self.other = get_user_model().objects.create_user('other', email='other@example.com', password=self.password)
        self.profile = AccountPreferences.objects.create(owner=self.user, name='Anna', phone='+79991112233')

    def visit(self):
        visit = domain.create_booking(data(self.service))
        visit.account = self.user
        visit.save(update_fields=['account'])
        return visit

    def test_repeat_only_own_active_services_and_does_not_reserve(self):
        visit = self.visit()
        old = Service.objects.create(name='Old service', active=False, minutes=30)
        visit.services.add(old)
        self.client.force_login(self.user)
        response = self.client.post(reverse('repeat_booking', args=[visit.pk]), follow=True)
        self.assertContains(response, 'больше недоступна')
        self.assertEqual(response.context['form'].initial['services'], [self.service.pk])
        self.assertNotIn('time', response.context['form'].initial)
        self.assertEqual(Booking.objects.count(), 1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse('repeat_booking', args=[visit.pk])).status_code, 404)

    def test_guest_choices_survive_login_and_never_include_contacts(self):
        payload = {'intent': 'login', 'date': future().date(), 'time': '10:00', 'services': [self.service.pk], 'phone': 'secret', 'comment': 'secret'}
        self.assertRedirects(self.client.post(reverse('book'), payload), '/account/login/?next=/')
        self.assertNotIn('secret', str(self.client.session[drafts.KEY]))
        self.assertFalse(Booking.objects.exists())
        response = self.client.post(reverse('client_login'), {'username': self.user.username, 'password': self.password, 'next': '/'}, follow=True)
        self.assertEqual(response.context['form'].initial['services'], [self.service.pk])
        self.assertEqual(response.context['form'].initial['time'], '10:00')
        self.assertEqual(str(response.context['selected']), str(future().date()))

    def test_expired_draft_is_discarded(self):
        session = self.client.session
        session[drafts.KEY] = {'services': [self.service.pk], 'time': '10:00', 'saved_at': timezone.now().timestamp() - 1801}
        session.save()
        response = self.client.get(reverse('book'))
        self.assertNotIn('time', response.context['form'].initial)
        self.assertNotIn(drafts.KEY, self.client.session)

    def test_choices_survive_signup(self):
        self.client.post(reverse('book'), {'intent': 'signup', 'date': future().date(), 'time': '10:15', 'services': [self.service.pk]})
        response = self.client.post(reverse('signup'), {'username': 'new-person', 'name': 'Test', 'phone': '+79990000009', 'password1': self.password, 'password2': self.password, 'consent': 'on'}, follow=True)
        self.assertEqual(response.request['PATH_INFO'], reverse('book'))
        self.assertEqual(response.context['form'].initial['time'], '10:15')
        self.assertFalse(Booking.objects.exists())

    def test_choices_rechecked_if_slot_taken_during_login(self):
        self.client.post(reverse('book'), {'intent': 'login', 'date': future().date(), 'time': '10:00', 'services': [self.service.pk]})
        self.visit()
        self.client.force_login(self.user)
        response = self.client.post(reverse('book'), {'date': future().date(), 'time': '10:00', 'services': [self.service.pk], 'consent': 'on'})
        self.assertContains(response, 'Это время уже занято')
        self.assertEqual(Booking.objects.count(), 1)

    def test_export_only_owned_data_and_no_staff_notes_or_tokens(self):
        visit = self.visit()
        visit.client = Client.objects.create(name='Anna', phone=visit.phone, notes='secret master note')
        visit.save(update_fields=['client'])
        PrivateEvent.objects.create(owner=self.user, title='My gym', start=future(), end=future(11))
        PrivateEvent.objects.create(owner=self.other, title='Other private event', start=future(), end=future(11))
        self.client.force_login(self.user)
        response = self.client.get(reverse('export_account'))
        self.assertContains(response, 'My gym')
        self.assertNotContains(response, 'Other private event')
        self.assertNotContains(response, 'secret master note')
        self.assertNotContains(response, visit.token)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])

    def test_deletion_requires_password_and_confirmation_retains_master_booking(self):
        visit = self.visit()
        PrivateEvent.objects.create(owner=self.user, title='My gym', start=future(), end=future(11))
        self.client.force_login(self.user)
        url = reverse('delete_account')
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'password': 'wrong', 'confirm': 'on'})
        self.client.post(url, {'password': self.password})
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
        self.assertEqual(self.client.post(url, {'password': self.password, 'confirm': 'on'}).status_code, 302)
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())
        self.assertFalse(PrivateEvent.objects.filter(owner_id=self.user.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.other.pk).exists())
        visit.refresh_from_db()
        self.assertIsNone(visit.account)
        self.assertEqual(visit.status, 'pending')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_master_cannot_delete_self(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('delete_account'), {'password': self.password, 'confirm': 'on'}).status_code, 403)
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_email_change_requires_current_password(self):
        self.client.force_login(self.user)
        self.client.post(reverse('recovery_email'), {'password': 'wrong', 'email': 'new@example.com'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'anna@example.com')
        self.client.post(reverse('recovery_email'), {'password': self.password, 'email': 'new@example.com'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@example.com')

    def test_password_change_keeps_current_session_invalidates_other(self):
        other_session = HttpClient()
        other_session.login(username=self.user.username, password=self.password)
        self.client.force_login(self.user)
        new = 'Another-long-password-735!'
        response = self.client.post(reverse('password_change'), {'old_password': self.password, 'new_password1': new, 'new_password2': new})
        self.assertRedirects(response, reverse('preferences'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new))
        self.assertEqual(other_session.get(reverse('preferences')).status_code, 302)

    @override_settings(PASSWORD_RESET_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_recovery_email_and_one_use_token_end_to_end(self):
        url = reverse('password_reset')
        self.client.post(url, {'username': self.other.username, 'email': self.user.email})
        self.assertEqual(len(mail.outbox), 0)
        self.assertRedirects(self.client.post(url, {'username': self.user.username, 'email': self.user.email}), reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        token = default_token_generator.make_token(self.user)
        confirm = reverse('password_reset_confirm', kwargs={'uidb64': urlsafe_base64_encode(force_bytes(self.user.pk)), 'token': token})
        response = self.client.get(confirm)
        new = 'A-reset-password-654!'
        self.assertRedirects(self.client.post(response.url, {'new_password1': new, 'new_password2': new}), reverse('password_reset_complete'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new))
        self.assertContains(self.client.get(confirm), 'Ссылка недействительна')

    @override_settings(PASSWORD_RESET_ENABLED=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_recovery_disabled_is_honest(self):
        response = self.client.post(reverse('password_reset'), {'username': self.user.username, 'email': self.user.email})
        self.assertContains(response, 'Почта пока не подключена')
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(PASSWORD_RESET_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_mail_failure_does_not_disclose_account_or_crash(self):
        with patch('django.contrib.auth.forms.PasswordResetForm.send_mail', side_effect=OSError('offline')):
            response = self.client.post(reverse('password_reset'), {'username': self.user.username, 'email': self.user.email})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_comfort_settings_saved_and_rendered(self):
        self.client.force_login(self.user)
        self.client.post(reverse('preferences'), {'design': 'glass', 'theme': 'ocean', 'glass_mode': 'calm', 'color_mode': 'dark', 'large_text': 'on'})
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.large_text)
        self.assertEqual(self.profile.glass_mode, 'calm')
        response = self.client.get(reverse('preferences'))
        self.assertContains(response, 'data-color-mode="dark"')
        self.assertContains(response, 'data-large-text="1"')

    def test_export_schedule_contains_only_available_start_times(self):
        visit = self.visit()
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse('schedule'), {'date': future().date(), 'service': self.service.pk, 'share': '1'})
        exported = response.context['share_data']
        self.assertTrue(exported['start_times'])
        windows = response.context['free_days'][0]['windows']
        self.assertFalse(any(a <= visit.start <= b for a, b in windows))
        self.assertNotContains(response, visit.phone)
        self.assertNotContains(response, visit.name)
        self.assertNotContains(response, visit.token)
        self.assertContains(response, 'Сохранить картинку')
        self.assertNotIn('127.0.0.1', exported['url'])
