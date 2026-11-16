// 홈 3D 띠: 책들이 둥근 띠를 이루고, 가운데 책만 또렷하다. 옆 책은 뒤로 물러나며 흐리고 어두워진다.
// 끌기(손가락·마우스), 이전/다음 버튼, 키보드 화살표로 넘긴다. 세로 스크롤은 그대로 페이지를 내린다.
// 가운데 책을 누르면 바로 읽기, 옆 책을 누르면 그 책을 가운데로 데려온다.
(() => {
  const stage = document.getElementById('hmStage');
  if (!stage) return;
  const books = [...stage.querySelectorAll('.hm-book')];
  const n = books.length;
  const title = document.getElementById('hmTitle');
  const meta = document.getElementById('hmMeta');
  const cta = document.getElementById('hmRead');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let cur = 0, pos = 0, drag = null, moved = false, infoTimer = 0;

  // 가장 짧은 쪽으로 잰 거리: 띠가 둥글게 이어진다
  const wrapD = d => ((d % n) + n + n / 2) % n - n / 2;

  function place(p) {
    const w = stage.clientWidth;
    const step = Math.min(w * 0.3, 170);
    books.forEach((el, i) => {
      const d = wrapD(i - p);
      const a = Math.abs(d);
      if (a > 3.4) { el.style.visibility = 'hidden'; return; }
      el.style.visibility = 'visible';
      // 가까운 책은 넓게, 먼 책은 촘촘히 겹친다
      const x = Math.sign(d) * (Math.min(a, 1) * step + Math.max(0, a - 1) * step * 0.55);
      el.style.transform = `translateX(${x}px) translateZ(${-a * 120}px) rotateY(${Math.max(-1.5, Math.min(1.5, d)) * 28}deg)`;
      el.style.setProperty('--blur', `${Math.min(a, 2.5) * 2.4}px`);
      el.style.setProperty('--shade', String(1 - Math.min(a, 3) * 0.2));
      el.style.zIndex = String(100 - Math.round(a * 10));
      const img = el.firstElementChild;                  // 먼 책의 표지는 다가올 때 받는다
      if (img.dataset.src) { img.src = img.dataset.src; delete img.dataset.src; }
    });
  }

  function setInfo(i) {
    const el = books[i];
    const apply = () => {
      title.textContent = el.dataset.title;
      meta.textContent = el.dataset.meta;
      cta.href = el.getAttribute('href');
      title.parentElement.classList.remove('swap');
    };
    clearTimeout(infoTimer);
    if (reduced) { apply(); return; }
    title.parentElement.classList.add('swap');
    infoTimer = setTimeout(apply, 140);
  }

  function go(i, animate = true) {
    i = ((i % n) + n) % n;
    // 지금 자리에서 가까운 쪽으로 돈다
    const target = cur + wrapD(i - cur);
    cur = i;
    books.forEach((el, j) => {
      const on = j === i;
      el.classList.toggle('on', on);
      el.tabIndex = on ? 0 : -1;
      if (on) el.removeAttribute('aria-hidden'); else el.setAttribute('aria-hidden', 'true');
    });
    stage.classList.toggle('moving', animate && !reduced);
    pos = target;
    place(pos);
    setInfo(i);
  }

  document.getElementById('hmPrev').addEventListener('click', () => go(cur - 1));
  document.getElementById('hmNext').addEventListener('click', () => go(cur + 1));
  stage.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); go(cur - 1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); go(cur + 1); }
    else if (e.key === 'Enter' && e.target === stage) location.href = books[cur].getAttribute('href');
  });

  books.forEach((el, i) => {
    el.addEventListener('click', e => {
      if (moved) { e.preventDefault(); moved = false; return; }   // 끌다 놓은 것은 누름이 아니다
      if (i !== cur) { e.preventDefault(); go(i); }               // 옆 책 → 가운데로
    });
    el.addEventListener('dragstart', e => e.preventDefault());
  });

  // --- 끌기: 가로로만 잡는다. 세로로 움직이면 페이지 스크롤에 맡긴다(touch-action: pan-y) ---
  stage.addEventListener('pointerdown', e => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, p0: pos, on: false };
    moved = false;
  });
  addEventListener('pointermove', e => {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.on) {
      if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy)) return;
      drag.on = true; moved = true;
      stage.classList.remove('moving');
      stage.setPointerCapture?.(e.pointerId);
    }
    const step = Math.min(stage.clientWidth * 0.3, 170);
    pos = drag.p0 - dx / step;
    place(pos);
  });
  const end = e => {
    if (!drag || e.pointerId !== drag.id) return;
    const was = drag.on; drag = null;
    if (!was) return;
    go(Math.round(pos));
    setTimeout(() => { moved = false; }, 300);
  };
  addEventListener('pointerup', end);
  addEventListener('pointercancel', end);

  addEventListener('resize', () => place(pos));
  go(0, false);
  requestAnimationFrame(() => stage.classList.add('ready'));
})();
