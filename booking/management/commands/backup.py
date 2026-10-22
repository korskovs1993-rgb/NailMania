import io
import json
from pathlib import Path
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from booking.domain import locked_studio

class Command(BaseCommand):
    help = 'Create an encrypted logical backup. Store BACKUP_KEY separately.'
    def add_arguments(self, parser):
        parser.add_argument('--output', required=True)
    def handle(self, *args, **options):
        if not settings.BACKUP_KEY:
            raise CommandError('Set BACKUP_KEY in .env first.')
        target = Path(options['output']).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        output = io.StringIO()
        with transaction.atomic():
            locked_studio()
            call_command('dumpdata', 'booking', 'auth.user', 'auth.group',
                         exclude=['booking.ratebucket'], use_natural_foreign_keys=True, stdout=output)
        envelope = json.dumps({'format': 'nailbook-v1', 'created_at': timezone.now().isoformat(),
                               'data': json.loads(output.getvalue())}, ensure_ascii=False).encode('utf-8')
        encrypted = Fernet(settings.BACKUP_KEY.encode()).encrypt(envelope)
        # Exclusive creation: a new backup never destroys a previous one.
        with target.open('xb') as destination:
            destination.write(encrypted)
        self.stdout.write(self.style.SUCCESS(f'Encrypted backup created: {target.name}'))
