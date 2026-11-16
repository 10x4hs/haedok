// 오프라인 화면 — CSP가 인라인 스크립트·onclick을 막으므로 따로 둔다
(() => {
  document.getElementById('retry').addEventListener('click', () => location.reload());
  // 연결이 돌아오면 알려 주고 1초 뒤 자동으로 다시 연다
  addEventListener('online', () => {
    const s = document.getElementById('st');
    s.textContent = '연결됐어요 · 다시 여는 중';
    s.className = 'st on';
    setTimeout(() => location.reload(), 1000);
  });
})();
