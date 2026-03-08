/**
 * Travel Agent — Service Worker
 *
 * Strategy:
 *  - App shell (HTML): stale-while-revalidate so the app always loads instantly,
 *    even offline, and silently refreshes in the background.
 *  - Static assets (icons, manifest): cache-first with long TTL.
 *  - API calls (/api/*, /s/*): network-only — never cache chat/SSE responses.
 *  - Offline fallback: if navigation fails and no cache exists, return a minimal
 *    offline page rather than the browser's default error screen.
 */

const CACHE_VERSION = 'v1';
const CACHE_NAME = `travel-agent-${CACHE_VERSION}`;

const APP_SHELL = ['/'];

const STATIC_ASSETS = [
  '/manifest.json',
  '/icons/icon-192.svg',
  '/icons/icon-512.svg',
  '/icons/icon-maskable-192.svg',
  '/icons/icon-maskable-512.svg',
];

const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Travel Agent — Offline</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;
         min-height:100dvh;display:flex;align-items:center;justify-content:center;
         flex-direction:column;gap:20px;padding:24px;text-align:center}
    .icon{font-size:64px;line-height:1}
    h1{font-size:22px;font-weight:700;color:#f8fafc}
    p{font-size:15px;color:#94a3b8;max-width:340px;line-height:1.6}
    button{margin-top:8px;padding:12px 28px;border-radius:10px;border:none;
           background:#0ea5e9;color:#fff;font-size:14px;font-weight:700;
           cursor:pointer}
    button:hover{background:#0284c7}
  </style>
</head>
<body>
  <div class="icon">✈️</div>
  <h1>You're offline</h1>
  <p>Your itinerary board is still available — open the app when you're connected to plan your next trip.</p>
  <button onclick="location.reload()">Try again</button>
</body>
</html>`;

// ── Install: cache app shell + static assets ──────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(async cache => {
      // Cache static assets (best-effort — don't fail install if one 404s)
      await Promise.allSettled(
        STATIC_ASSETS.map(url => cache.add(url).catch(() => {}))
      );
      // Cache the app shell
      await cache.add('/').catch(() => {});
    })
  );
  // Activate immediately — don't wait for old SW to finish
  self.skipWaiting();
});

// ── Activate: delete old caches ───────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k.startsWith('travel-agent-') && k !== CACHE_NAME)
          .map(k => caches.delete(k))
      )
    )
  );
  // Take control of all open clients immediately
  self.clients.claim();
});

// ── Fetch: route-based caching strategy ──────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // API calls, SSE streams, share pages — always network, never cache
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/s/') ||
    url.pathname.startsWith('/help')
  ) {
    return; // fall through to browser default (network)
  }

  // Static icons + manifest — cache-first
  if (
    url.pathname.startsWith('/icons/') ||
    url.pathname === '/manifest.json'
  ) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response.ok) {
            caches.open(CACHE_NAME).then(c => c.put(request, response.clone()));
          }
          return response;
        });
      })
    );
    return;
  }

  // Navigation (HTML) — stale-while-revalidate with offline fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      caches.open(CACHE_NAME).then(cache =>
        cache.match('/').then(cached => {
          const networkFetch = fetch(request)
            .then(response => {
              if (response.ok) cache.put('/', response.clone());
              return response;
            })
            .catch(() => {
              // Offline: return cached shell or inline offline page
              return cached || new Response(OFFLINE_HTML, {
                headers: { 'Content-Type': 'text/html' },
              });
            });

          // Return cached immediately, update in background
          return cached || networkFetch;
        })
      )
    );
    return;
  }
});

// ── Push notifications (future-ready stub) ───────────────────────────────
self.addEventListener('push', event => {
  if (!event.data) return;
  const data = event.data.json();
  self.registration.showNotification(data.title || 'Travel Agent', {
    body: data.body || '',
    icon: '/icons/icon-192.svg',
    badge: '/icons/icon-192.svg',
    tag: data.tag || 'travel-agent',
    data: { url: data.url || '/' },
  });
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    clients.openWindow(event.notification.data?.url || '/')
  );
});
