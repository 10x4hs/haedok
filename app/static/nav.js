// 아래 막대 · 레일: 지금 칸을 다시 누르면 맨 위로(이미 맨 위면 그 칸 첫 화면을 새로 연다)
(() => {
  const bar = document.querySelector('.tabbar');
  if (!bar) return;
  const roots = { home: ['/'], discover: ['/discover'], feed: ['/feed'], library: ['/library', '/races'], chat: ['/chat'], me: ['/profile', '/me'] };
  bar.addEventListener('click', e => {
    const a = e.target.closest('a.on[data-nav]');
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
    if (!(roots[a.dataset.nav] || []).includes(location.pathname)) return;  // 깊은 화면이면 그 칸 첫 화면으로 간다
    if (scrollY < 4) return;  // 맨 위면 링크 그대로 = 새로 불러오기
    e.preventDefault();
    scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  });
})();
