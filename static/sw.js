const CACHE = 'luxe-radar-shell-v400-image-persistence';
const SHELL = [
  '/static/app.css?v=20260830-400',
  '/static/app.js?v=20260830-400',
  '/static/risk.js?v=20260830-400',
  '/static/app-icon.svg',
  '/static/app-icon-192.png?v=20260830-400',
  '/static/app-icon-512.png?v=20260830-400',
  '/static/manifest.webmanifest?v=20260830-400',
  '/static/offline.html',
  '/static/offline.css',
  '/static/offline.js'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys
          .filter(key => key.startsWith('luxe-radar-shell-') && key !== CACHE)
          .map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Les API de recherche ne sont jamais mises en cache : un téléphone ne doit
  // pas réutiliser un état de recherche ou un token périmé.
  if (url.origin === self.location.origin && url.pathname.startsWith('/api/')) return;

  // Navigation : toujours le serveur d'abord, l'écran hors-ligne seulement si
  // le réseau est réellement indisponible.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
        .catch(() => caches.match('/static/offline.html'))
    );
    return;
  }

  if (
    event.request.method !== 'GET' ||
    url.origin !== self.location.origin ||
    !url.pathname.startsWith('/static/')
  ) return;

  // Les assets sont versionnés. Cache-first évite de retélécharger le JS/CSS
  // sur mobile, tandis qu'une nouvelle version change automatiquement l'URL.
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put(event.request, copy));
        }
        return response;
      });
    })
  );
});
