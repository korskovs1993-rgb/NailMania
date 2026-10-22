from django.core.management.base import BaseCommand
from booking.models import Studio, WeeklyDay, Service, PersonalTemplate

class Command(BaseCommand):
    help = 'Create initial studio schedule and services (never overwrites existing data).'
    def handle(self, *args, **options):
        Studio.objects.get_or_create(pk=1)
        for title, minutes in [('Поликлиника', 90), ('Магазин', 60), ('Спортзал', 90)]:
            PersonalTemplate.objects.get_or_create(title=title, defaults={'minutes': minutes})
        for weekday in range(7):
            WeeklyDay.objects.get_or_create(weekday=weekday, defaults={'closed': weekday == 6})
        if not Service.objects.exists():
            for name, minutes in [('Маникюр', 60), ('Покрытие гель-лаком', 45), ('Снятие покрытия', 15), ('Дизайн', 15)]:
                Service.objects.create(name=name, minutes=minutes)
        self.stdout.write(self.style.SUCCESS('Studio ready. Create the master account with createsuperuser.'))
