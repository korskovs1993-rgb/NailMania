import json
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connection
from django.core.management.color import no_style
from django.apps import apps

class Command(BaseCommand):
    help = 'Restore into an EMPTY migrated database only; no overwrite of existing studio/users.'
    def add_arguments(self, parser):
        parser.add_argument('file')
    @transaction.atomic
    def handle(self, *args, **options):
        models = list(apps.get_app_config('booking').get_models())
        User = apps.get_model('auth', 'User')
        Group = apps.get_model('auth', 'Group')
        if any(model.objects.exists() for model in models + [User, Group]):
            raise CommandError('Restore requires an empty database after migrate, before setup_studio/createsuperuser.')
        try:
            envelope = json.loads(Fernet(settings.BACKUP_KEY.encode()).decrypt(Path(options['file']).read_bytes()))
            if envelope.get('format') != 'nailbook-v1':
                raise ValueError()
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise CommandError('Invalid backup or wrong BACKUP_KEY.') from None
        objects = list(serializers.deserialize('json', json.dumps(envelope['data']), handle_forward_references=True))
        with connection.constraint_checks_disabled():
            for obj in objects:
                obj.save()
            for obj in objects:
                if obj.deferred_fields:
                    obj.save_deferred_fields()
        connection.check_constraints()
        with connection.cursor() as cursor:
            for sql in connection.ops.sequence_reset_sql(no_style(), models + [User, Group]):
                cursor.execute(sql)
        self.stdout.write(self.style.SUCCESS('Backup restored. Verify calendar and client counts before opening access.'))
