import hashlib
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import RateBucket


def limited(request, scope, limit=12):
    # REMOTE_ADDR only: untrusted X-Forwarded-For must never bypass limits.
    now = timezone.now()
    raw = f'{settings.SECRET_KEY}:{scope}:{request.META.get("REMOTE_ADDR", "")}:{int(now.timestamp()) // 600}'
    key = hashlib.sha256(raw.encode()).hexdigest()
    with transaction.atomic():
        bucket, _ = RateBucket.objects.get_or_create(key=key, defaults={'expires': now + timedelta(minutes=11)})
        bucket = RateBucket.objects.select_for_update().get(pk=bucket.pk)
        bucket.hits += 1
        bucket.save(update_fields=['hits'])
        over = bucket.hits > limit
    RateBucket.objects.filter(expires__lt=now).delete()
    return over
