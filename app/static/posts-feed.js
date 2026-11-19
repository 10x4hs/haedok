/* 피드(/feed) — 새 게시물 알약 · 바닥 가까이에서 자동 이어 읽기 · 동그라미 '봤음'.
 *
 * - 새 게시물: 화면이 보이는 동안만 60초마다 /feed/new?after=<가장 새 게시물 id> 로 숫자만 묻는다(presence.js 와 같은 규칙:
 *   숨으면 멈추고, 실패하면 간격을 늘린다). 알약을 누르면 맨 위로 올라가 /feed/fresh 조각을 끼워 넣는다.
 * - 이어 읽기: '더 보기' 링크가 화면 600px 안에 들어오면 /feed/more 조각을 받아 그 자리에 붙인다. JS 가 없으면 보통 링크.
 * - 동그라미: '봤음'은 서버에 남기지 않고 이 브라우저(localStorage)에만 둔다 — 본 고리는 회색으로, 뒤로 보낸다.
 * 조각은 서버가 이스케이프해 그린 HTML 이라 DOMParser 로 옮겨 붙인다(사용자 글을 문자열로 만들지 않는다). */
(() => {
  const P = window.haedokPosts || { enhance: () => {}, reduced: false };
  const stream = document.querySelector('[data-stream]');

  async function fragment(url) {
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    return Array.from(doc.body.childNodes).map(n => document.adoptNode(n));
  }

  // ---------- 바닥 가까이에서 다음 쪽
  if (stream && 'IntersectionObserver' in window) {
    let busy = false;
    const io = new IntersectionObserver(entries => {
      entries.forEach(async e => {
        const link = e.target;
        if (!e.isIntersecting || busy || !link.isConnected) return;
        busy = true;
        io.unobserve(link);
        link.textContent = '불러오는 중…';
        try {
          const nodes = await fragment(link.dataset.nextPage);
          const box = document.createElement('div');
          box.append(...nodes);
          // 이미 화면에 있는 게시물(알약으로 끼운 것 등)은 다시 붙이지 않는다
          box.querySelectorAll('article.ps-post').forEach(a => { if (document.getElementById(a.id)) a.remove(); });
          P.enhance(box);
          link.replaceWith(...Array.from(box.childNodes));
          watch();
        } catch (_) {
          link.textContent = '더 보기';
        } finally {
          busy = false;
        }
      });
    }, { rootMargin: '600px 0px' });
    const watch = () => stream.querySelectorAll('a[data-next-page]').forEach(a => io.observe(a));
    watch();
  }

  // ---------- 새 게시물 알약(전체 · 친구 탭)
  const pill = document.querySelector('[data-new-pill]');
  if (stream && pill && (stream.dataset.tab === 'friends' || stream.dataset.tab === 'mix')) {
    const tabq = `&tab=${stream.dataset.tab}`;
    const BASE = 60_000;
    let newest = Number(stream.dataset.newest) || 0;
    let wait = BASE, timer = null;
    const n = pill.querySelector('[data-new-n]');
    const schedule = () => { clearTimeout(timer); if (!document.hidden) timer = setTimeout(tick, wait); };
    async function tick() {
      if (!newest || document.hidden) return;
      try {
        const r = await fetch(`/feed/new?after=${newest}${tabq}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        if (!r.ok) throw new Error(String(r.status));
        const j = await r.json();
        n.textContent = String(j.count);
        pill.hidden = !j.count;
        wait = BASE;
      } catch (_) {
        wait = Math.min(wait * 2, 10 * BASE);
      }
      schedule();
    }
    document.addEventListener('visibilitychange', () => { if (document.hidden) clearTimeout(timer); else { wait = BASE; tick(); } });
    pill.addEventListener('click', async () => {
      pill.disabled = true;
      try {
        const nodes = await fragment(`/feed/fresh?after=${newest}${tabq}`);
        const box = document.createElement('div');
        box.append(...nodes);
        box.querySelectorAll('article.ps-post').forEach(a => { const old = document.getElementById(a.id); if (old) old.remove(); });
        P.enhance(box);
        const ids = Array.from(box.querySelectorAll('article.ps-post')).map(a => Number(a.id.replace('post-', '')) || 0);
        newest = Math.max(newest, ...ids);
        stream.dataset.newest = String(newest);
        stream.prepend(...Array.from(box.childNodes));
        pill.hidden = true;
        window.scrollTo({ top: Math.max(0, stream.getBoundingClientRect().top + window.scrollY - 120), behavior: P.reduced ? 'auto' : 'smooth' });
        const first = stream.querySelector('article.ps-post a.ps-who-n');
        if (first) first.focus({ preventScroll: true });
      } catch (_) {
        location.href = '/feed';
      } finally {
        pill.disabled = false;
      }
    });
    schedule();
  }

  // ---------- 동그라미: 본 고리는 회색으로, 안 본 것 먼저
  const row = document.querySelector('[data-stories]');
  if (row) {
    let seen = {};
    try { seen = JSON.parse(localStorage.getItem('ps-seen') || '{}') || {}; } catch (_) { seen = {}; }
    const lis = Array.from(row.querySelectorAll('li[data-story]'));
    lis.forEach(li => {
      if (li.hasAttribute('data-ring') && Number(li.dataset.newest) && Number(seen[li.dataset.story] || 0) >= Number(li.dataset.newest)) {
        li.classList.add('seen');
      }
    });
    lis.filter(li => li.classList.contains('seen') || !li.hasAttribute('data-ring')).forEach(li => row.append(li));
    row.addEventListener('click', e => {
      const li = e.target.closest('li[data-story]');
      if (!li || !Number(li.dataset.newest)) return;
      seen[li.dataset.story] = Number(li.dataset.newest);
      try { localStorage.setItem('ps-seen', JSON.stringify(seen)); } catch (_) { /* 비공개 창 */ }
    });
  }

  // ---------- 친구 신청('새로운 사람' 게시물 머리 · 모듈) — 화면을 넘기지 않고 보내고 '신청함'으로 바꾼다
  document.addEventListener('submit', async e => {
    const f = e.target;
    if (!f.matches('form[data-follow], form[data-hide]')) return;
    e.preventDefault();
    const btn = f.querySelector('button');
    if (btn) btn.disabled = true;
    try {
      const r = await fetch(f.action, { method: 'POST', body: new FormData(f), credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(String(r.status));
      if (f.matches('[data-hide]')) {
        const mod = f.closest('[data-mod]');
        if (mod) mod.remove();
        if (window.haedokToast) window.haedokToast('오늘 하루 이 칸을 숨겨요');
      } else {
        const done = document.createElement('span');
        done.className = 'ps-follow-done';
        done.textContent = '신청함';
        f.replaceWith(done);
        if (window.haedokToast) window.haedokToast('친구 신청을 보냈어요');
      }
    } catch (_) {
      f.submit();
    }
  });

  // ---------- 같이 읽기: 머리의 사람 아이콘 → 아래에서 올라오는 시트(없으면 /together 로 그대로)
  const tg = document.getElementById('psTogether');
  document.addEventListener('click', e => {
    const a = e.target.closest('[data-together]');
    if (!a || !tg || typeof tg.showModal !== 'function') return;
    e.preventDefault();
    tg.showModal();
  });
})();
