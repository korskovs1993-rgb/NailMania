from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .tests import setup_calendar, future, data
from .models import Client, Booking, PersonalBlock, PersonalTemplate, DayOverride
from .forms import QuarterTimeField
from . import domain
from django.core.exceptions import ValidationError


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False)
class ScheduleUpdatesTests(TestCase):
    def setUp(self):
        self.service = setup_calendar()
        self.client.force_login(get_user_model().objects.create_user('master', is_staff=True))

    def test_quarter_hours_only(self):
        field = QuarterTimeField()
        for value in ('10:01', '12:59', '10:15:01'):
            with self.assertRaises(ValidationError):
                field.clean(value)
        self.assertEqual(field.clean('10:15').minute, 15)

    def test_existing_client_booking_preserves_card(self):
        person = Client.objects.create(name='Анна', phone='+79991234567', notes='Постоянная клиентка')
        response = self.client.post(reverse('staff_book'), {'client': person.pk, 'services': [self.service.pk], 'date': future().date().isoformat(), 'time': '10:00', 'consent': 'on'})
        self.assertEqual(response.status_code, 302)
        visit = Booking.objects.get()
        self.assertEqual(visit.client_id, person.pk)
        self.assertEqual(visit.name, person.name)
        self.assertEqual(visit.status, 'confirmed')
        person.refresh_from_db()
        self.assertEqual(person.notes, 'Постоянная клиентка')
        self.assertEqual(Client.objects.count(), 1)

    def test_new_client_requires_details(self):
        response = self.client.post(reverse('staff_book'), {'services': [self.service.pk], 'date': future().date().isoformat(), 'time': '10:00', 'consent': 'on'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Booking.objects.exists())

    def test_personal_template_and_split_date_creation(self):
        response = self.client.post(reverse('services'), {'section': 'template', 'template-title': 'Прогулка', 'template-minutes': '45'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PersonalTemplate.objects.get(title='Прогулка').minutes, 45)
        day = future().date().isoformat()
        response = self.client.post(reverse('block'), {'title': 'Прогулка', 'start_0': day, 'start_1': '10:15', 'end_0': day, 'end_1': '11:00'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PersonalBlock.objects.get().end - PersonalBlock.objects.get().start, timedelta(minutes=45))

    def test_free_windows_exclude_pending_and_personal_and_share_no_names(self):
        domain.create_booking(data(self.service, future(10)))
        domain.create_block({'title': 'Секретное дело', 'start': future(12), 'end': future(13)})
        self.assertEqual(domain.free_windows(future().date()), [(future(9), future(10)), (future(11), future(12)), (future(13), future(19))])
        response = self.client.get(reverse('schedule'), {'date': future().date().isoformat(), 'share': '1'})
        self.assertContains(response, '09:00–10:00')
        self.assertNotContains(response, 'Секретное дело')
        self.assertNotContains(response, 'Тестовая клиентка')
        self.assertNotContains(response, '+79990000001')
        DayOverride.objects.create(date=future().date(), closed=True)
        self.assertEqual(domain.free_windows(future().date()), [])

    def test_public_calendar_has_no_master_navigation_even_for_master(self):
        domain.create_booking(data(self.service))
        domain.create_block({'title': 'Личное скрытое дело', 'start': future(12), 'end': future(13)})
        for authenticated in (True, False):
            if not authenticated:
                self.client.logout()
            response = self.client.get(reverse('book'))
            self.assertContains(response, 'NeilMania')
            self.assertContains(response, 'Календарь записи')
            for url in ('/studio/clients/', '/studio/services/', '/studio/inbox/', '/studio/schedule/', '/login/'):
                self.assertNotContains(response, f'href="{url}"')
            self.assertNotContains(response, 'Тестовая клиентка')
            self.assertNotContains(response, 'Личное скрытое дело')

    def test_templates_managed_in_services_only(self):
        self.assertContains(self.client.get(reverse('services'), {'tab': 'personal'}), 'Шаблоны личных дел')
        self.assertNotContains(self.client.get(reverse('block')), 'name="template-title"')
        self.client.logout()
        response = self.client.post(reverse('services'), {'section': 'template', 'template-title': 'Запрещено', 'template-minutes': '60'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PersonalTemplate.objects.filter(title='Запрещено').exists())

    def test_edit_template_preserves_existing_personal_event(self):
        template = PersonalTemplate.objects.create(title='Спорт', minutes=60)
        event = domain.create_block({'title': 'Спорт', 'start': future(12), 'end': future(13)})
        response = self.client.post(reverse('template_edit', args=[template.pk]), {'section': 'template', 'template-title': 'Спортзал', 'template-minutes': '90', 'template-icon': '🏋️'})
        self.assertRedirects(response, reverse('services') + '?tab=personal')
        template.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual((template.title, template.minutes, template.icon), ('Спортзал', 90, '🏋️'))
        self.assertEqual(event.title, 'Спорт')
        self.assertEqual(event.end - event.start, timedelta(hours=1))
        self.assertEqual(PersonalTemplate.objects.count(), 1)

    def test_template_error_keeps_editor_open_and_client_cannot_edit(self):
        template = PersonalTemplate.objects.create(title='Спорт', minutes=60)
        url = reverse('template_edit', args=[template.pk])
        response = self.client.post(url, {'section': 'template', 'template-title': '', 'template-minutes': '17'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_editor'])
        self.assertTrue(response.context['editor'].errors)
        template.refresh_from_db()
        self.assertEqual(template.minutes, 60)
        self.client.logout()
        self.assertEqual(self.client.post(url, {'template-title': 'Чужое'}).status_code, 302)
