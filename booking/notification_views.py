import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.conf import settings

from .models import AccountPreferences, Notification, PushSubscription
from .notifications import configured, notify, validate_subscription


@login_required(login_url='/account/login/')
def inbox(request):
    notices = Notification.objects.filter(owner=request.user).order_by('-created_at', '-pk')
    return render(request, 'notifications.html', {
        'page': Paginator(notices, 30).get_page(request.GET.get('page')),
        'master_area': request.user.is_staff,
    })


@login_required(login_url='/account/login/')
@require_POST
def mark_read(request):
    notices = Notification.objects.filter(owner=request.user, read_at__isnull=True)
    if request.POST.get('id'):
        try:
            notice_id = int(request.POST['id'])
        except ValueError:
            return JsonResponse({'error': 'Неверный номер оповещения.'}, status=400)
        notice = get_object_or_404(notices, pk=notice_id)
        notice.read_at = timezone.now()
        notice.save(update_fields=['read_at'])
    else:
        notices.update(read_at=timezone.now())
    return redirect('notifications')


@login_required(login_url='/account/login/')
def state(request):
    return JsonResponse({'unread': Notification.objects.filter(owner=request.user, read_at__isnull=True).count(),
                         'configured': configured(), 'public_key': settings.WEBPUSH_PUBLIC_KEY if configured() else ''})


@login_required(login_url='/account/login/')
@require_POST
@transaction.atomic
def subscription(request):
    if len(request.body) > 5000:
        return JsonResponse({'error': 'Слишком большой запрос.'}, status=400)
    try:
        data = json.loads(request.body)
        values = validate_subscription(data)
    except (ValueError, ValidationError):
        return JsonResponse({'error': 'Не удалось распознать подписку этого браузера.'}, status=400)
    # Serialize subscriptions per account; endpoint cannot be taken from another account.
    get_user_model().objects.select_for_update().get(pk=request.user.pk)
    existing = PushSubscription.objects.filter(endpoint_hash=values['endpoint_hash']).first()
    if data.get('action') == 'remove':
        PushSubscription.objects.filter(owner=request.user, endpoint_hash=values['endpoint_hash']).delete()
        return JsonResponse({'ok': True})
    if not configured():
        return JsonResponse({'error': 'Push на сервере пока не настроены.'}, status=503)
    if existing and existing.owner_id != request.user.pk:
        return JsonResponse({'error': 'На этом устройстве есть подписка другого аккаунта. Сбрось разрешение уведомлений для сайта и подключи заново.'}, status=409)
    if not existing and PushSubscription.objects.filter(owner=request.user).count() >= 5:
        return JsonResponse({'error': 'Уже подключено пять устройств. Отключи ненужные в настройках.'}, status=400)
    if existing:
        PushSubscription.objects.filter(pk=existing.pk, owner=request.user).update(**values)
    else:
        try:
            with transaction.atomic():
                PushSubscription.objects.create(owner=request.user, **values)
        except IntegrityError:
            return JsonResponse({'error': 'Подписка уже изменилась. Обнови страницу.'}, status=409)
    return JsonResponse({'ok': True})


@login_required(login_url='/account/login/')
@require_POST
def options(request):
    prefs, _ = AccountPreferences.objects.get_or_create(owner=request.user)
    prefs.reminders = request.POST.get('reminders') == 'on'
    prefs.save(update_fields=['reminders'])
    messages.success(request, 'Настройки напоминаний сохранены.')
    return redirect('preferences')


@login_required(login_url='/account/login/')
@require_POST
def disconnect_all(request):
    PushSubscription.objects.filter(owner=request.user).delete()
    messages.success(request, 'Push отключены на всех устройствах. Оповещения в приложении остаются.')
    return redirect('preferences')


@login_required(login_url='/account/login/')
@require_POST
def test_push(request):
    if not configured() or not PushSubscription.objects.filter(owner=request.user).exists():
        return JsonResponse({'error': 'Сначала подключи уведомления на устройстве.'}, status=400)
    key = f'test:{request.user.pk}:{int(timezone.now().timestamp()) // 60}'
    notify(request.user.pk, 'Проверка оповещений. Всё начинается с маленького «дзынь».', '/notifications/', dedupe_key=key)
    return JsonResponse({'ok': True})
