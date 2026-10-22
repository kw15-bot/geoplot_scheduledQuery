// 警報状況表示: Web Push を受けて通知を出す(webpush.py が送る。HANDOFF_NOTAM.md §30)。
// 通知を押すとビューアを開く(開いていれば前に出す)。データのキャッシュなどはしない。
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || '警報状況表示', {
    body: d.body || '', tag: d.tag || 'geoplot', renotify: true,
    icon: 'icon-192.png', badge: 'icon-192.png', data: { url: d.url || './geoplot-mil.html' },
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || './geoplot-mil.html', self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    const open = list.find(c => c.url.split('#')[0].split('?')[0] === url.split('#')[0].split('?')[0]);
    return open ? open.focus() : self.clients.openWindow(url);
  }));
});
