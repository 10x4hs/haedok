// 커뮤니티 위 막대 — 갈래 아이콘(피드 · 메시지 · 같이 읽기)과 자리 기억.
// - 내리면 막대 뒤가 흐려지고 가는 선이 생긴다(막대는 늘 보인다 — 맨 위로 올라가지 않아도 갈래를 바꾼다).
// - 아이콘으로 갈래를 바꾸기 전에 지금 자리를 sessionStorage 에 적고, 돌아오면 base.html 의 작은 스크립트가 그 자리로 둔다.
// - 피드 아이콘은 마지막으로 보던 거르기(전체 · 친구 · 탐색)로 돌아간다. 지금 갈래 아이콘을 다시 누르면 맨 위로.
(() => {
  const ss = {
    get(k) { try { return sessionStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, v); } catch (_) { /* 비공개 창 */ } },
  };
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const here = location.pathname + location.search;
  const sec = document.body.dataset.cm;
  const save = () => ss.set('cm-y:' + here, String(Math.round(scrollY)));
  if (sec === 'feed') ss.set('cm-url:feed', here);

  const nav = document.querySelector('.cm-nav');
  if (nav) {
    const last = ss.get('cm-url:feed');
    const feed = nav.querySelector('a[data-cm="feed"]');
    if (feed && sec !== 'feed' && last && /^\/community(\?|$)/.test(last)) feed.setAttribute('href', last);
    nav.addEventListener('click', e => {
      const a = e.target.closest('a[data-cm]');
      if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
      if (a.getAttribute('aria-current') === 'page' && a.pathname === location.pathname) {
        e.preventDefault();
        scrollTo({ top: 0, behavior: reduced ? 'auto' : 'smooth' });
        return;
      }
      save();
      ss.set('cm-go', a.pathname + a.search);
    });
  }
  addEventListener('pagehide', save);

  const top = document.querySelector('.top');
  if (top) {
    const paint = () => top.classList.toggle('is-scrolled', scrollY > 4);
    addEventListener('scroll', paint, { passive: true });
    paint();
  }
})();
