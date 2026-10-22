// Cache only a generic offline screen. Never cache HTML with client data or tokens.
const CACHE = 'nailbook-offline-v2';
// Push payload contains only an opaque deduplication tag, never personal data.
self.addEventListener('push', event => {
  let tag = 'neilmania-update';
  try { const data = event.data?.json(); if (/^neilmania-\d+$/.test(data?.tag)) tag = data.tag; } catch (_) {}
  event.waitUntil(self.registration.showNotification('NeilMania', {
    body: 'Есть обновление. Открой приложение, чтобы посмотреть.',
    icon: '/static/icon-192.png', tag, data: {url: '/notifications/'}
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(async windows => {
    const target = new URL('/notifications/', self.location.origin).href;
    const existing = windows.find(client => new URL(client.url).origin === self.location.origin);
    if (existing) { await existing.navigate(target); return existing.focus(); }
    return self.clients.openWindow(target);
  }));
});
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.add('/offline/')));
  self.skipWaiting();
});
self.addEventListener('activate', event => event.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith('nailbook-') && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())
));
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(() => caches.match('/offline/')));
});
