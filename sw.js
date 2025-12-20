const CACHE_NAME = 'zenith-v3';
const ASSETS_TO_CACHE = [
  '/',
  '/index.html',
  '/magic.css',
  '/magic.js',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
  'https://fonts.googleapis.com/css2?family=Outfit:wght@300;500;700;800&display=swap'
];

// Installation : On met en cache les fichiers vitaux
self.addEventListener('install', (event) => {
  self.skipWaiting(); // Force l'activation immédiate du nouveau SW
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[Service Worker] Mise en cache globale v3');
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

// Interception : On sert le cache si dispo, sinon réseau
self.addEventListener('fetch', (event) => {
  // On ne cache pas les appels API (musique, recherche)
  if (event.request.url.includes('/api/') || event.request.url.includes('/search') || event.request.url.includes('/stream') || event.request.url.includes('/recommend')) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});