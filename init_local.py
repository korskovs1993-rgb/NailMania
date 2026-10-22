"""Generate local secrets without printing them. Does not overwrite an existing .env."""
import secrets
from pathlib import Path
from cryptography.fernet import Fernet

root = Path(__file__).resolve().parent
target = root / '.env'
if target.exists():
    raise SystemExit('.env already exists; left unchanged.')
text = (root / '.env.example').read_text(encoding='utf-8')
text = text.replace('SECRET_KEY=\n', f'SECRET_KEY={secrets.token_urlsafe(64)}\n')
text = text.replace('BACKUP_KEY=\n', f'BACKUP_KEY={Fernet.generate_key().decode()}\n')
text = text.replace('PGPASSWORD=\n', f'PGPASSWORD={secrets.token_urlsafe(32)}\n')
target.write_text(text, encoding='utf-8')
print('Local .env created. Secrets remain on this computer.')
