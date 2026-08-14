const CACHE = 'luxe-radar-shell-v49';
const SHELL = ['/static/app.css?v=20260814-38', '/static/app.js?v=20260814-44', '/static/risk.js?v=20260814-1', '/static/app-icon.svg', '/static/app-icon-192.png?v=20260814-2', '/static/app-icon-512.png?v=20260814-2', '/static/manifest.webmanifest', '/static/offline.html', '/static/offline.css', '/static/offline.js'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/static/offline.html')));
    return;
  }
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || !url.pathname.startsWith('/static/') || url.pathname.startsWith('/static/campaign/')) return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); }
    return response;
  }).catch(() => caches.match(event.request)));
});
