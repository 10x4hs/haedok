// 공개 페이지의 짧은 영상: 화면에 보일 때만 소리 없이 돌고, 벗어나면 멈춰요.
// 움직임 줄이기를 켠 기기는 저절로 돌지 않고 멈춘 그림 + 기본 재생 버튼(controls)만 보여요.
(() => {
  const clips = [...document.querySelectorAll('video[data-clip]')];
  if (!clips.length) return;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');

  const label = (btn, playing) => {
    btn.setAttribute('aria-label', playing ? '영상 멈추기' : '영상 재생');
    btn.dataset.state = playing ? 'playing' : 'paused';
  };

  if (reduce.matches || !('IntersectionObserver' in window)) {
    clips.forEach(v => { v.controls = true; v.preload = 'none'; });
    return;
  }

  const seen = new Map();  // video → 지금 화면에 보이는지
  const play = v => {
    if (v.dataset.userPaused || document.hidden) return;
    v.muted = true;
    v.play().catch(() => {});
  };

  const io = new IntersectionObserver(entries => {
    entries.forEach(({ target: v, isIntersecting }) => {
      seen.set(v, isIntersecting);
      if (isIntersecting) play(v); else v.pause();
    });
  }, { threshold: 0.4 });

  clips.forEach(v => {
    const btn = v.parentElement.querySelector('[data-clip-toggle]');
    if (btn) {
      btn.hidden = false;
      label(btn, false);
      v.addEventListener('play', () => label(btn, true));
      v.addEventListener('pause', () => label(btn, false));
      btn.addEventListener('click', () => {
        if (v.paused) { delete v.dataset.userPaused; play(v); }
        else { v.dataset.userPaused = '1'; v.pause(); }
      });
    }
    io.observe(v);
  });

  document.addEventListener('visibilitychange', () => {
    clips.forEach(v => { if (document.hidden) v.pause(); else if (seen.get(v)) play(v); });
  });
})();
