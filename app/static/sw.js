// 해독 서비스 워커 — 화면·CSS·JS는 네트워크 우선(배포 직후 옛 스타일이 남지 않게), 끊기면 저장본. 이미지·폰트만 저장본 우선.
// VERSION은 서버(app/pwa.py)가 배포 도장(커밋 SHA 또는 정적 파일 해시)으로 채운다 — 손으로 올리지 않는다.
const VERSION = 'haedok-__BUILD__';
const RUNTIME = VERSION + '-runtime';
const RUNTIME_MAX = 60;           // 이미지·표지 등 런타임 캐시는 최근 60개만 (오래된 것부터 지운다)
const OFFLINE = '/static/offline.html';
const PRECACHE = [OFFLINE, '/static/app.css', '/static/looks.css', '/static/icons/icon-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== VERSION && k !== RUNTIME).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// 런타임 캐시에 넣고, 개수가 넘치면 가장 오래 전에 넣은 것부터 뺀다 (Cache.keys()는 넣은 순서)
async function putLimited(cache, req, res) {
  await cache.put(req, res);
  const keys = await cache.keys();
  if (keys.length > RUNTIME_MAX) await Promise.all(keys.slice(0, keys.length - RUNTIME_MAX).map(k => cache.delete(k)));
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  // API·실시간·APK·영상(Range 요청)은 건드리지 않는다
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws') || url.pathname.endsWith('.apk') || url.pathname.endsWith('.mp4')) return;

  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(async () => (await caches.match(req)) || caches.match(OFFLINE)));
    return;
  }
  if (url.pathname.startsWith('/static/') && /\.(css|js|json|html)$/.test(url.pathname)) {
    e.respondWith(caches.open(VERSION).then(cache =>
      fetch(req).then(res => { if (res.ok) cache.put(req, res.clone()); return res; })
        .catch(async () => (await cache.match(req)) || Response.error())
    ));
    return;
  }
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(caches.open(RUNTIME).then(async cache => {
      const hit = await cache.match(req);
      const fresh = fetch(req).then(async res => { if (res.ok) await putLimited(cache, req, res.clone()); return res; }).catch(() => hit);
      e.waitUntil(fresh.catch(() => {}));   // 저장본을 먼저 돌려준 뒤에도 새 것을 끝까지 받아 넣는다
      return hit || fresh;
    }));
  }
});
