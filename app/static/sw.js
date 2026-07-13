/* Budget Buddy service worker (v10.13) — deliberately MINIMAL, no offline
 * promises. Only same-origin GETs under /static/ are handled, with
 * stale-while-revalidate (NOT cache-first: htmx.min.js and chart.umd.min.js
 * carry no cache-bust param, so SWR keeps them at most one page-load stale
 * across deploys; style.css?v=<hash> is safe either way). Everything else —
 * pages, POSTs, auth — falls straight through to the network untouched.
 * Served at /sw.js by a Flask route so its scope covers '/', which
 * installability requires. Bump the cache name to force a purge. */
const CACHE = 'bb-static-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith('bb-static-') && k !== CACHE)
            .map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin ||
      !url.pathname.startsWith('/static/')) {
    return; // no respondWith — pure network passthrough
  }
  event.respondWith(
    caches.open(CACHE).then((cache) =>
      cache.match(event.request).then((cached) => {
        const refresh = fetch(event.request).then((resp) => {
          if (resp.ok) cache.put(event.request, resp.clone());
          return resp;
        });
        return cached || refresh;
      })
    )
  );
});
