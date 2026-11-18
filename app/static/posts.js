/* 읽은 책 게시물 — 가리기 열기 · 좋아요(새로고침 없이) · 글자 수 · 올리기 화면의 미리 보기.
 * JS가 없어도 모든 폼은 그대로 동작한다(서버가 처리하고 돌아온다). DOM은 textContent 로만 만든다. */
(() => {
  const SPOILER = /\|\|([\s\S]+?)\|\|/g; // 채팅(chat.js)과 같은 가리기 표시

  // ---------- 가리기: 탭하면 흐림이 걷힌다(예의일 뿐 보안이 아니다)
  function openVeil(v) {
    v.classList.add('open');
    v.removeAttribute('role');
    v.removeAttribute('aria-label');
    v.tabIndex = -1;
  }
  document.addEventListener('click', e => {
    const v = e.target.closest('.ps-veil:not(.open)');
    if (v) { e.preventDefault(); e.stopPropagation(); openVeil(v); }
  }, true);
  document.addEventListener('keydown', e => {
    const v = e.target.closest && e.target.closest('.ps-veil:not(.open)');
    if (v && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openVeil(v); }
  });

  // ---------- 좋아요: 서버가 JSON으로 켜짐 · 수를 알려 준다. 실패하면 보통 폼으로 보낸다
  // want=1 은 '켜기만'(그림 두 번 탭) — 몇 번 보내도 같다
  function paint(form, j) {
    const btn = form.querySelector('button');
    btn.setAttribute('aria-pressed', j.liked ? 'true' : 'false');
    btn.setAttribute('aria-label', j.liked ? '좋아요 취소' : '좋아요');
    btn.querySelector('.ps-heart').classList.toggle('on', j.liked);
    const line = (form.closest('.ps-post') || document).querySelector('[data-like-line]');
    if (!line) return;
    line.replaceChildren();
    if (j.count) {
      const b = document.createElement('b');
      b.dataset.likeCount = '';
      b.textContent = String(j.count);
      line.append(document.createTextNode('좋아요 '), b, document.createTextNode('개'));
    } else {
      const z = document.createElement('span');
      z.dataset.likeZero = '';
      z.textContent = '가장 먼저 좋아요를 눌러 보세요';
      line.append(z);
    }
  }
  async function sendLike(form, want) {
    const btn = form.querySelector('button');
    if (btn.disabled) return;
    btn.disabled = true;
    const body = new FormData(form);
    if (want) body.set('want', '1');
    try {
      const r = await fetch(form.action, { method: 'POST', body, headers: { Accept: 'application/json' }, credentials: 'same-origin' });
      if (r.status === 429) {
        const j = await r.json().catch(() => ({}));
        if (window.haedokToast) window.haedokToast(j.detail || '잠시 뒤에 다시 해 주세요');
        return;
      }
      if (!r.ok) throw new Error(String(r.status));
      paint(form, await r.json());
    } catch (_) {
      if (!want) form.submit();
    } finally {
      btn.disabled = false;
    }
  }
  document.querySelectorAll('form[data-like]').forEach(form => {
    form.addEventListener('submit', e => { e.preventDefault(); sendLike(form, false); });
  });

  // ---------- 그림 두 번 탭(더블클릭) = 좋아요. 하트가 잠깐 떴다 사라진다(움직임 줄이기를 따른다)
  document.querySelectorAll('[data-dbl-like]').forEach(pic => {
    const post = pic.closest('.ps-post');
    const form = post && post.querySelector('form[data-like]');
    if (!form) return;
    const pop = pic.querySelector('.ps-pop');
    let last = 0;
    pic.addEventListener('click', e => {
      if (e.target.closest('.ps-veil')) return;
      const now = Date.now();
      if (now - last < 350) {
        last = 0;
        if (pop) { pop.classList.remove('show'); void pop.offsetWidth; pop.classList.add('show'); }
        sendLike(form, true);
      } else {
        last = now;
      }
    });
  });

  // ---------- 공유 = 링크 복사
  document.addEventListener('click', async e => {
    const b = e.target.closest('[data-share]');
    if (!b) return;
    const url = new URL(b.dataset.share, location.origin).href;
    let ok = false;
    try { await navigator.clipboard.writeText(url); ok = true; } catch (_) { ok = false; }
    if (window.haedokToast) window.haedokToast(ok ? '링크를 복사했어요' : url);
    const menu = b.closest('details');
    if (menu) menu.open = false;
  });

  // ---------- 긴 글은 두 줄에서 접고 '더 보기'
  document.querySelectorAll('[data-clamp]').forEach(cap => {
    const more = cap.nextElementSibling;
    if (!more || !more.matches('[data-more]')) return;
    if (cap.scrollHeight - cap.clientHeight > 2) {
      more.hidden = false;
      more.addEventListener('click', () => { cap.classList.add('open'); more.remove(); });
    }
  });

  // ---------- ⋯ 메뉴는 바깥을 누르면 닫힌다
  document.addEventListener('click', e => {
    document.querySelectorAll('details.ps-menu[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
  });

  // ---------- 글자 수
  document.querySelectorAll('[data-count]').forEach(input => {
    const out = document.getElementById(input.dataset.count);
    if (!out) return;
    const sync = () => { out.textContent = `${input.value.length}/${input.maxLength}`; };
    input.addEventListener('input', sync);
    sync();
  });

  // ---------- 댓글 칸은 쓰는 만큼 늘어난다
  document.querySelectorAll('.ps-cm-form textarea').forEach(t => {
    const fit = () => { t.style.height = 'auto'; t.style.height = `${Math.min(t.scrollHeight + 2, 160)}px`; };
    t.addEventListener('input', fit);
  });

  // ---------- 올리기 화면
  const form = document.querySelector('form[data-compose]');
  if (!form) return;
  const card = document.querySelector('[data-preview]');
  const $ = s => card.querySelector(s);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const value = name => { const c = document.querySelector(`input[name="${name}"]:checked`); return c ? c.value : ''; };

  function rich(parent, text) {
    parent.replaceChildren();
    let last = 0;
    text.replace(SPOILER, (m, inner, at) => {
      if (at > last) parent.append(document.createTextNode(text.slice(last, at)));
      const v = el('span', 'ps-veil');
      v.append(el('span', 'ps-veil-t', inner));
      parent.append(v);
      last = at + m.length;
      return m;
    });
    if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
  }

  function art(pick) {
    const box = el('div', `ps-art p${pick.dataset.palette || 0}`);
    const title = pick.dataset.title, cover = pick.dataset.cover;
    if (cover) {
      const bg = el('img', 'ps-bg'); bg.src = cover; bg.alt = ''; bg.setAttribute('aria-hidden', 'true');
      const cv = el('img', 'ps-cv'); cv.src = cover; cv.alt = `${title} 표지`;
      box.append(bg, cv);
    } else {
      const cv = el('div', `ps-cv cover p${pick.dataset.palette || 0} m${pick.dataset.pattern || 0}`);
      cv.setAttribute('role', 'img'); cv.setAttribute('aria-label', `${title} 표지`);
      cv.append(el('span', 'cv-art'), el('span', 'cv-t', title), el('span', 'cv-a', pick.dataset.author || ''));
      box.append(cv);
    }
    return box;
  }

  function book() {
    const pick = document.querySelector('input[name="book"]:checked');
    if (!pick) return;
    $('[data-pv-title]').textContent = pick.dataset.title;
    $('[data-pv-author]').textContent = pick.dataset.author || '';
    $('[data-pv-art]').replaceChildren(art(pick));
  }

  const rateRow = form.querySelector('.ps-rate-row');
  function stars() {
    const n = Number(value('rating')) || 0;
    rateRow.dataset.n = String(n);
    const s = $('[data-pv-stars]');
    s.hidden = !n;
    s.setAttribute('aria-label', `별점 ${n}점`);
    s.querySelectorAll('i').forEach((i, k) => i.classList.toggle('on', k < n));
  }

  const caption = form.querySelector('#psCaption'), quote = form.querySelector('#psQuote');
  function text() {
    const c = caption.value.trim(), q = quote.value.trim();
    const cp = $('[data-pv-caption]'), qp = $('[data-pv-quote]');
    cp.hidden = !c; rich(cp, c);
    qp.hidden = !q; rich(qp, q);
  }

  function meta() {
    const st = form.querySelector('input[name="status"]:checked');
    if (st) $('[data-pv-status]').textContent = st.nextElementSibling.textContent;
    $('[data-pv-vis]').hidden = value('visibility') !== 'friends';
  }

  document.addEventListener('change', e => {
    const n = e.target.name;
    if (n === 'book') book();
    else if (n === 'rating') stars();
    else if (n === 'status' || n === 'visibility') meta();
  });
  caption.addEventListener('input', text);
  quote.addEventListener('input', text);
  stars();

  // 검색해도 쓰던 글이 남는다: 검색 폼의 숨은 칸에 지금 값을 옮겨 보낸다
  const search = document.querySelector('form[data-draft-form]');
  if (search) search.addEventListener('submit', () => {
    const now = { book: value('book'), status: value('status'), rating: value('rating'), caption: caption.value, quote: quote.value, visibility: value('visibility') };
    search.querySelectorAll('[data-draft]').forEach(h => { h.value = now[h.dataset.draft] || ''; });
  });
})();
