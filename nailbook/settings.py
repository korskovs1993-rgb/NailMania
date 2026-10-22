import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')
DEBUG = os.getenv('DEBUG', '0') == '1'
SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    raise ImproperlyConfigured('Set SECRET_KEY in .env; see README.md')
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
CSRF_TRUSTED_ORIGINS = [x for x in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if x]
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes',
                 'django.contrib.sessions', 'django.contrib.messages',
                 'django.contrib.staticfiles', 'booking']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware',
              'whitenoise.middleware.WhiteNoiseMiddleware',
              'django.contrib.sessions.middleware.SessionMiddleware',
              'django.middleware.common.CommonMiddleware',
              'django.middleware.csrf.CsrfViewMiddleware',
              'django.contrib.auth.middleware.AuthenticationMiddleware',
              'django.contrib.messages.middleware.MessageMiddleware',
              'django.middleware.clickjacking.XFrameOptionsMiddleware',
              'booking.middleware.PrivatePagesMiddleware']
ROOT_URLCONF = 'nailbook.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
              'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
              'OPTIONS': {'context_processors': [
                  'django.template.context_processors.request',
                  'django.contrib.auth.context_processors.auth',
                  'django.contrib.messages.context_processors.messages',
                  'booking.context.app_context']}}]
WSGI_APPLICATION = 'nailbook.wsgi.application'
if os.getenv('DEMO_SQLITE') == '1':
    if not DEBUG:
        raise ImproperlyConfigured('SQLite demo cannot run with DEBUG=0. Configure PostgreSQL.')
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
                            'NAME': BASE_DIR / 'demo.sqlite3'}}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('PGDATABASE', 'nailbook'), 'USER': os.getenv('PGUSER', 'nailbook'),
        'PASSWORD': os.getenv('PGPASSWORD', ''), 'HOST': os.getenv('PGHOST', 'localhost'),
        'PORT': os.getenv('PGPORT', '5432'), 'CONN_MAX_AGE': 60,
        'OPTIONS': {'sslmode': os.getenv('PGSSLMODE', 'prefer')}}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE = 'ru'
TIME_ZONE = 'Asia/Vladivostok'  # Komsomolsk-on-Amur: UTC+10
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/studio/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 43200
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'strict-origin'
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 2592000 if not DEBUG else 0
if os.getenv('TRUST_PROXY', '0') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
BACKUP_KEY = os.getenv('BACKUP_KEY', '')
PUBLIC_BOOKING_ENABLED = os.getenv('PUBLIC_BOOKING_ENABLED', '0') == '1'
OPERATOR_NAME = os.getenv('OPERATOR_NAME', '')
OPERATOR_ADDRESS = os.getenv('OPERATOR_ADDRESS', '')
PRIVACY_CONTACT = os.getenv('PRIVACY_CONTACT', '')
CONSENT_VERSION = '2026-10-11-v1'
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', '1') == '1'
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', '0') == '1'
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@localhost')
PASSWORD_RESET_ENABLED = os.getenv('PASSWORD_RESET_ENABLED', '0') == '1'
PASSWORD_RESET_TIMEOUT = 3600
PUBLIC_BOOKING_URL = os.getenv('PUBLIC_BOOKING_URL', '')

WEBPUSH_PUBLIC_KEY = os.getenv('WEBPUSH_PUBLIC_KEY', '')
WEBPUSH_PRIVATE_KEY = os.getenv('WEBPUSH_PRIVATE_KEY', '')
WEBPUSH_SUBJECT = os.getenv('WEBPUSH_SUBJECT', '')
WEBPUSH_ALLOWED_HOSTS = ('web.push.apple.com', 'fcm.googleapis.com', 'updates.push.services.mozilla.com')
