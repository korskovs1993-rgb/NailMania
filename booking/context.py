from django.conf import settings
from .models import Studio, AccountPreferences, Notification, PushSubscription

def app_context(request):
    prefs = AccountPreferences.objects.filter(owner=request.user).first() if request.user.is_authenticated else None
    return {'unread_notifications': Notification.objects.filter(owner=request.user, read_at__isnull=True).count() if request.user.is_authenticated else 0,
            'reminders_enabled': prefs.reminders if prefs else True,
            'push_device_count': PushSubscription.objects.filter(owner=request.user).count() if request.user.is_authenticated else 0,
            'account_theme': prefs.theme if prefs else 'lavender', 'account_design': prefs.design if prefs else 'glass',
            'glass_mode': prefs.glass_mode if prefs else 'glass', 'color_mode': prefs.color_mode if prefs else 'palette', 'large_text': prefs.large_text if prefs else False,
            'studio': Studio.objects.filter(pk=1).first(),
            'master_area': request.path.startswith('/studio/'),
            'demo_mode': settings.DEBUG, 'operator_name': settings.OPERATOR_NAME,
            'operator_address': settings.OPERATOR_ADDRESS,
            'privacy_contact': settings.PRIVACY_CONTACT}
