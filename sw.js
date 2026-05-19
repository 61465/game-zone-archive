const GZ_ARCH_CACHE = 'gz-archive-v1';
const GZ_BASE = '/game-zone-archive';
const GZ_ASSETS = [
  GZ_BASE + '/',
  GZ_BASE + '/index.html',
  GZ_BASE + '/quiz.html',
  GZ_BASE + '/manifest.json'
];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(GZ_ARCH_CACHE).then(c => c.addAll(GZ_ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== GZ_ARCH_CACHE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.hostname.includes('gstatic') || url.hostname.includes('googleapis') || url.hostname.includes('firebase') || url.hostname.includes('fonts')) return;
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request).then(res => {
      if (res.ok && url.origin === self.location.origin) {
        caches.open(GZ_ARCH_CACHE).then(c => c.put(e.request, res.clone()));
      }
      return res;
    }).catch(() => caches.match(GZ_BASE + '/index.html')))
  );
});
