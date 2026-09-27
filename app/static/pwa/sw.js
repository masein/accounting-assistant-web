/* Service worker (roadmap 2026-09 §4.10) — served at /sw.js so it controls
   the whole app. What it does, deliberately little:

   * versioned static files (/static/…?v=<hash>, immutable) come from a cache
     after the first load, so the installed app opens fast;
   * a page load that fails (no network) gets the offline page;
   * a photo or PDF shared to the installed app (share target) is kept in a
     cache and the chat picks it up.

   It never caches API responses or the app page itself: books are per
   company and per user, and must always be fresh. */
const VERSION = '__VERSION__';
const STATIC_CACHE = 'static-' + VERSION;
const OFFLINE_URL = '/offline';
const SHARE_CACHE = 'shared-files';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(STATIC_CACHE).then((c) => c.add(OFFLINE_URL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith('static-') && key !== STATIC_CACHE) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

async function keepShared(request) {
  const form = await request.formData();
  const files = form.getAll('file').filter((f) => f && typeof f === 'object' && f.size);
  const cache = await caches.open(SHARE_CACHE);
  let n = 0;
  for (const f of files.slice(0, 5)) {
    const key = '/shared/' + Date.now() + '-' + (n += 1) + '?name=' + encodeURIComponent(f.name || 'shared');
    await cache.put(key, new Response(f, { headers: { 'Content-Type': f.type || 'application/octet-stream' } }));
  }
  return Response.redirect('/?shared=' + n + '#ai-accountant', 303);
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (req.method === 'POST' && url.pathname === '/share-target') {
    event.respondWith(keepShared(req));
    return;
  }
  if (req.method !== 'GET') return;
  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  if (url.pathname.startsWith('/static/') && url.searchParams.has('v')) {
    event.respondWith((async () => {
      const cache = await caches.open(STATIC_CACHE);
      const hit = await cache.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) cache.put(req, res.clone());
      return res;
    })());
  }
  // everything else (the API above all) goes straight to the network
});

// Push (roadmap §4.10, part 2): an alert from the bell, shown by the system;
// a tap opens (or focuses) the app on the alert's page.
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = { body: event.data ? event.data.text() : '' }; }
  const page = typeof data.page === 'string' && /^[a-z0-9-]{1,40}$/.test(data.page) ? data.page : '';
  event.waitUntil(self.registration.showNotification(data.title || 'Accounting Assistant', {
    body: data.body || '',
    icon: '/static/pwa/icon-192.png',
    badge: '/static/pwa/icon-192.png',
    tag: data.tag || undefined,
    data: { url: page ? '/#' + page : '/' },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const w of windows) {
      if (new URL(w.url).origin === self.location.origin && 'focus' in w) {
        await w.focus();
        return 'navigate' in w ? w.navigate(url) : undefined;
      }
    }
    return self.clients.openWindow(url);
  })());
});
