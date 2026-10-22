from datetime import datetime, time, timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from booking.models import Booking, Client, Event, PersonalBlock, Service

class Command(BaseCommand):
    help = 'Synthetic local SQLite demo ONLY. Creates a publicly documented demo password.'
    def handle(self, *args, **options):
        if not settings.DEBUG or settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
            raise CommandError('Demo seeding is permitted only with DEBUG=1 and local SQLite.')
        User = get_user_model()
        if not User.objects.filter(username='demo').exists():
            User.objects.create_user('demo', password='NailBook-demo-2026!', is_staff=True)
        if Booking.objects.exists():
            self.stdout.write('Existing visits found; no demo appointments added.')
            return
        day = timezone.localdate() + timedelta(days=1)
        def at(hour, minute=0):
            return timezone.make_aware(datetime.combine(day, time(hour, minute)))
        services = list(Service.objects.filter(active=True)[:2])
        for name, phone, hour, state in [('Демо · Анна', '+79990000001', 10, 'confirmed'), ('Демо · Мария', '+79990000002', 14, 'pending')]:
            client = Client.objects.create(name=name, phone=phone, notes='Вымышленная карточка для знакомства с интерфейсом.') if state == 'confirmed' else None
            booking = Booking.objects.create(name=name, phone=phone, client=client, start=at(hour),
                end=at(hour, 0) + timedelta(minutes=sum(x.minutes for x in services)),
                status=state, service_summary=' + '.join(s.name for s in services))
            booking.services.set(services)
            Event.objects.create(booking=booking, text='Демонстрационная запись — вымышленные данные.')
        PersonalBlock.objects.create(title='Кофе и немного тишины', start=at(12), end=at(13))
        self.stdout.write(f'Synthetic demo ready. Calendar date: {day.isoformat()}')
