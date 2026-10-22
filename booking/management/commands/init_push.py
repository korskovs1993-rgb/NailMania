import base64
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


class Command(BaseCommand):
    help = 'Generate local VAPID keys without printing the private key. Never replaces existing keys.'

    def handle(self, *args, **options):
        folder = settings.BASE_DIR / '.keys'
        folder.mkdir(exist_ok=True)
        target = folder / 'webpush.pem'
        if target.exists():
            raise CommandError('Keys already exist; keep them. Do not rotate keys of active subscriptions.')
        key = ec.generate_private_key(ec.SECP256R1())
        with target.open('xb') as stream:
            stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        public = base64.urlsafe_b64encode(key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b'=').decode()
        self.stdout.write(f'WEBPUSH_PUBLIC_KEY={public}\nWEBPUSH_PRIVATE_KEY={target.as_posix()}')
        self.stdout.write('Add these two values and WEBPUSH_SUBJECT=mailto:your-contact-address to .env locally.')
