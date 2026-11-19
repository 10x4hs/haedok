// AI 화면 머리줄: 내리면 뒤가 흐려지고 가는 선이 생긴다(커뮤니티 막대와 같다). 막대는 늘 위에 붙어 있다.
(() => {
  const top = document.querySelector('body.ai-page .top');
  if (!top) return;
  const paint = () => top.classList.toggle('is-scrolled', scrollY > 4);
  addEventListener('scroll', paint, { passive: true });
  paint();
})();
