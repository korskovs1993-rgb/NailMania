(() => {
  if (document.documentElement.dataset.account !== '1') return;
  const status = document.querySelector('#push-status');
  const enable = document.querySelector('#push-enable');
  const disable = document.querySelector('#push-disable');
  const test = document.querySelector('#push-test');
  let config, registration;
  const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value;
  async function post(url, data) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось сохранить настройки. Попробуй ещё раз.');
    return result;
  }
  async function refresh() {
    try {
      const response = await fetch('/notifications/state/', {cache: 'no-store'});
      if (!response.ok || response.redirected) return;
      config = await response.json();
      document.querySelectorAll('[data-unread-count]').forEach(node => {
        node.textContent = config.unread > 99 ? '99+' : config.unread;
        node.hidden = !config.unread;
      });
    } catch (_) { /* Offline: keep the last known count. */ }
  }
  function decodeKey(value) {
    const raw = atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4));
    return Uint8Array.from(raw, char => char.charCodeAt(0));
  }
  async function inspect() {
    if (!status) return;
    if (!window.isSecureContext || !('serviceWorker' in navigator) || !('PushManager' in window)) {
      status.textContent = 'В этом браузере push пока недоступны. Оповещения внутри приложения работают.';
      return;
    }
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    if (ios && !matchMedia('(display-mode: standalone)').matches && !navigator.standalone) {
      status.textContent = 'Сначала добавь NeilMania на экран Домой и открой с иконки.';
      return;
    }
    if (!config?.configured) {
      status.textContent = 'Оповещения в приложении работают. Push на телефон станут доступны после настройки сервера.';
      return;
    }
    registration = await navigator.serviceWorker.register('/sw.js');
    await Promise.race([navigator.serviceWorker.ready, new Promise((_, reject) => setTimeout(() => reject(new Error('Обнови страницу и попробуй ещё раз.')), 8000))]);
    const sub = await registration.pushManager.getSubscription();
    enable.hidden = Notification.permission === 'denied';
    disable.hidden = !sub;
    test.hidden = !sub;
    enable.textContent = sub ? 'Подключить / восстановить' : 'Включить уведомления';
    status.textContent = Notification.permission === 'denied' ? 'Разрешение отключено. Измени его в настройках браузера или iPhone.' : sub ? 'Разрешение на устройстве есть. При смене аккаунта или отключении всех устройств нажми «Подключить / восстановить».' : 'Разреши push, чтобы получать общий сигнал даже при закрытом приложении.';
  }
  enable?.addEventListener('click', async () => {
    enable.disabled = true;
    try {
      // Permission request must start directly from the user gesture on iOS.
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') throw new Error('Разрешение не получено. Оповещения останутся внутри приложения.');
      const sub = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: decodeKey(config.public_key)});
      await post('/notifications/push/', sub.toJSON());
      disable.hidden = false; test.hidden = false;
      status.textContent = 'Устройство подключено. Нажми «Проверить доставку»: сигнал придёт после обработки очереди.';
    } catch (error) { status.textContent = error.message; }
    finally { enable.disabled = false; }
  });
  disable?.addEventListener('click', async () => {
    disable.disabled = true;
    try {
      const sub = await registration.pushManager.getSubscription();
      if (sub) { await post('/notifications/push/', {...sub.toJSON(), action: 'remove'}); await sub.unsubscribe(); }
      disable.hidden = true; test.hidden = true; enable.textContent = 'Включить уведомления';
      status.textContent = 'Push на этом устройстве отключены. Оповещения остаются в приложении.';
    } catch (error) { status.textContent = error.message; }
    finally { disable.disabled = false; }
  });
  test?.addEventListener('click', async () => {
    test.disabled = true;
    try { await post('/notifications/push/test/', {}); status.textContent = 'Проверка в очереди. При запущенном обработчике ожидай сигнал примерно в течение минуты.'; await refresh(); }
    catch (error) { status.textContent = error.message; }
    finally { test.disabled = false; }
  });
  refresh().then(inspect).catch(error => { if (status) status.textContent = error.message; });
  setInterval(() => { if (!document.hidden) refresh(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
