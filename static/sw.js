/* PWA service worker: app-shell cache-first, API network-first */
const VERSION = 'v3';
const SHELL = [
  '/static/style.css',
  '/static/home.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/manifest.json'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open('shell-' + VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => !k.startsWith('shell-' + VERSION)).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
    return;
  }
  // HTML/API: network-first z fallbackem offline
  e.respondWith(
    fetch(e.request).then(r => {
      if (url.pathname === '/' || url.pathname.startsWith('/gate') || url.pathname.startsWith('/login')) {
        const cp = r.clone();
        caches.open('pages-' + VERSION).then(c => c.put(e.request, cp));
      }
      return r;
    }).catch(() => caches.match(e.request).then(r => r || caches.match('/gate')))
  );
});