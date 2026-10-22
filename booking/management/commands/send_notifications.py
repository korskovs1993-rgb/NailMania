import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from booking.notifications import create_reminders, deliver_pending


class Command(BaseCommand):
    help = 'Create app reminders and deliver Web Push. Telegram is no longer used.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            create_reminders()
            sent = deliver_pending()
            if sent or not options['loop']:
                self.stdout.write(f'Web Push sent: {sent}; app reminders checked.')
            if not options['loop']:
                break
            time.sleep(30)
