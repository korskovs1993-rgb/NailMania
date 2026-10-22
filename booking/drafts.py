"""Short-lived booking choices, never contact details or reserved slots."""
from datetime import datetime
from django.utils import timezone
from .models import Service

KEY = 'booking_choices'


def save_choices(request, data):
    raw = data.getlist('services') if hasattr(data, 'getlist') else data.get('services', [])
    ids = [int(x) for x in raw[:100] if str(x).isdigit() and len(str(x)) < 10]
    value = {'services': list(Service.objects.filter(pk__in=ids, active=True).values_list('pk', flat=True)), 'saved_at': timezone.now().timestamp()}
    try:
        date = datetime.strptime(data.get('date', ''), '%Y-%m-%d').date()
        if 1900 <= date.year <= 2100:
            value['date'] = date.isoformat()
    except (ValueError, TypeError):
        pass
    try:
        time = datetime.strptime(data.get('time', ''), '%H:%M').time()
        if time.minute % 15 == 0:
            value['time'] = time.strftime('%H:%M')
    except (ValueError, TypeError):
        pass
    request.session[KEY] = value


def choices(request):
    value = request.session.get(KEY, {})
    if timezone.now().timestamp() - value.get('saved_at', 0) > 1800:
        request.session.pop(KEY, None)
        return {}
    return {key: value[key] for key in ('services', 'date', 'time') if key in value}
