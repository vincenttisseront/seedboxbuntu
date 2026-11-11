// Service worker de la console : garde l'interface (HTML, CSS, JS, polices, icônes) pour qu'elle
// s'ouvre même sans réseau. Les données (/api) ne sont jamais mises en cache : elles viennent
// toujours du serveur, et sans réseau l'interface le dit.
const VERSION = 'v2';
const CACHE = `seedbox-console-${VERSION}`;
const SHELL = ['./', 'app.css', 'app.js', 'features.js', 'logo.svg', 'icon-192.png', 'manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()).catch(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys()
    .then((names) => Promise.all(names.filter((n) => n.startsWith('seedbox-console-') && n !== CACHE).map((n) => caches.delete(n))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.includes('/api/')) return;            // données et téléchargements : toujours le serveur
  event.respondWith((async () => {
    try {
      const response = await fetch(request);
      // Une redirection (connexion Authelia expirée) ou une erreur n'est jamais gardée
      if (response.ok && !response.redirected && response.type === 'basic') {
        const cache = await caches.open(CACHE);
        cache.put(request, response.clone());
      }
      return response;
    } catch (error) {
      const cached = await caches.match(request, { ignoreSearch: true });
      if (cached) return cached;
      if (request.mode === 'navigate') {
        const shell = await caches.match('./');
        if (shell) return shell;
      }
      throw error;
    }
  })());
});
