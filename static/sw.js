self.addEventListener('install', (e) => {
  console.log('[Service Worker] Install');
});

self.addEventListener('fetch', (e) => {
  // 오프라인 기능은 없지만 앱 설치를 위해 필요함
  e.respondWith(fetch(e.request));
});
