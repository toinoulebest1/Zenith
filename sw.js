const CACHE_NAME = 'zenith-v4';
const ASSETS_TO_CACHE = [
  '/',
  '/index.html',
  '/style.css',
  '/magic.css',
  '/magic.js',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
  'https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500..800&family=Manrope:wght@400..800&display=swap'
];

// Installation : On met en cache les fichiers vitaux
self.addEventListener('install', (event) => {
  self.skipWaiting(); // Force l'activation immédiate du nouveau SW
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[Service Worker] Mise en cache globale v4');
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
});

// Activation : Nettoyage des vieux caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keyList) => {
      return Promise.all(keyList.map((key) => {
        if (key !== CACHE_NAME) {
          console.log('[Service Worker] Suppression ancien cache', key);
          return caches.delete(key);
        }
      }));
    })
  );
  self.clients.claim(); // Prend le contrôle immédiat des pages
});

const STATIC_EXT = /\.(html|css|js|json|png|svg|ico|webp)$/;
const STATIC_HOSTS = /(^|\.)fonts\.(googleapis|gstatic)\.com$|^cdnjs\.cloudflare\.com$/;

function putInCache(request, response) {
  const copy = response.clone();
  caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
  return response;
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    // Appels dynamiques (musique, recherche, paroles, manifestes…) : jamais interceptés
    if (url.pathname !== '/' && !STATIC_EXT.test(url.pathname)) return;
    // Fichiers du site : réseau d'abord (une mise à jour est visible tout de suite), cache si hors ligne
    event.respondWith(
      fetch(request)
        .then((response) => (response.ok ? putInCache(request, response) : response))
        .catch(() => caches.match(request).then((cached) => cached || caches.match('/')))
    );
    return;
  }

  // Polices et icônes externes : cache d'abord (elles ne changent pas)
  if (STATIC_HOSTS.test(url.hostname)) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request).then((response) => putInCache(request, response)))
    );
  }
});
