// AI 서점 도우미 — 폼은 JS 없이도 돈다. 여기서는 (1) 보내면 바로 내 말풍선을 띄우고 서버가 그린 화면으로
// 대화 칸만 바꿔 끼우고, (2) 대화(서명된 state)를 이 탭에만(sessionStorage) 기억해 새로고침해도 이어지게 하고,
// (3) 서재 담기 · 친구에게 보내기를 제자리에서 처리한다. 사용자 글은 textContent 로만 넣는다.
(() => {
  const root = document.getElementById('aiShop');
  if (!root) return;
  const KEY = 'haedok-ai-shop';
  const store = {
    get() { try { return sessionStorage.getItem(KEY) || ''; } catch (_) { return ''; } },
    set(v) { try { if (v) sessionStorage.setItem(KEY, v); else sessionStorage.removeItem(KEY); } catch (_) { /* 저장 못 해도 화면은 돈다 */ } },
  };
  const $ = s => root.querySelector(s);
  const newBtn = document.querySelector('[data-ai-new]');
  let busy = false;

  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }

  function wire() {
    const ta = $('#aiQ'), send = $('.ai-send');
    if (!ta) return;
    const fit = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 132) + 'px'; send.disabled = !ta.value.trim(); };
    ta.addEventListener('input', fit);
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (ta.value.trim()) $('#aiForm').requestSubmit(); }
    });
    fit();
    const st = $('[data-ai-state]');
    store.set(st && st.value);
    if (newBtn) newBtn.hidden = !st || !st.value;
  }

  function scrollEnd() {
    const last = root.querySelector('.ai-log > .ai-msg:last-child');
    if (last) last.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }

  async function post(body, showText) {
    if (busy) return;
    busy = true;
    root.setAttribute('aria-busy', 'true');
    const log = $('#aiLog');
    if (showText) {
      const me = el('div', 'ai-msg me first pending'); me.append(el('p', 'ai-bubble', showText));
      const bot = el('div', 'ai-msg bot first pending');
      const ava = $('.ai-ava').cloneNode(true);
      const typing = el('p', 'ai-bubble ai-typing'); typing.setAttribute('aria-label', '고르는 중');
      typing.append(el('i'), el('i'), el('i'));
      bot.append(ava, typing);
      log.append(me, bot);
      $('.ai-quick')?.remove();
      scrollEnd();
    }
    try {
      const res = await fetch('/assistant', { method: 'POST', body, credentials: 'same-origin', headers: { Accept: 'text/html' } });
      if (!res.ok) throw new Error(String(res.status));
      const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
      const fresh = doc.getElementById('aiShop');
      if (!fresh) throw new Error('no page');
      root.replaceChildren(...Array.from(fresh.childNodes, n => document.importNode(n, true)));
      wire();
      if (showText) scrollEnd(); else root.querySelector('.ai-log > .ai-msg:last-child')?.scrollIntoView({ block: 'start' });
    } catch (_) {
      root.querySelectorAll('.pending').forEach(n => n.remove());
      const status = $('#aiStatus');
      if (status) status.textContent = '보내지 못했어요. 연결을 확인하고 다시 보내 주세요.';
    } finally {
      busy = false;
      root.removeAttribute('aria-busy');
    }
  }

  root.addEventListener('submit', async e => {
    const form = e.target;
    if (form.id === 'aiForm') {
      e.preventDefault();
      const body = new FormData(form, e.submitter || undefined);
      const text = (body.get('chip') || body.get('q') || '').toString().trim();
      if (!text) return;
      if (body.get('chip')) body.set('q', '');
      $('#aiQ').value = '';
      post(body, text);
      return;
    }
    const act = form.dataset.aiAct;
    if (!act) return;
    e.preventDefault();
    const btn = form.querySelector('button');
    btn.disabled = true;
    try {
      const res = await fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || '잠시 뒤에 다시 해 주세요');
      const target = act === 'send' ? form.closest('details').querySelector('summary') : btn;
      target.textContent = form.dataset.done;
      target.classList.add('is-done');
      if (act === 'send') form.closest('details').open = false;
    } catch (err) {
      btn.disabled = false;
      const status = $('#aiStatus');
      if (status) status.textContent = err.message || '잠시 뒤에 다시 해 주세요';
    }
  });

  // 되묻기 칩: 입력칸으로 옮겨 가며 그 질문을 자리표시로 보여 준다(label for 라서 JS 없이도 입력칸으로 간다)
  root.addEventListener('click', e => {
    const q = e.target.closest('[data-ai-followup]');
    if (q) $('#aiQ').placeholder = q.dataset.aiFollowup;
  });

  if (newBtn) newBtn.addEventListener('click', () => store.set(''));

  wire();
  // 새로고침 · 뒤로 오기: 이 탭에 기억해 둔 대화가 있고 화면이 비어 있으면 되살린다(AI 는 부르지 않는다)
  const saved = store.get();
  if (root.dataset.turns === '0' && saved && !$('#aiQ').value) {
    const body = new FormData($('#aiForm'));
    body.set('state', saved);
    body.set('q', '');
    post(body, null);
  } else if (root.dataset.turns !== '0') {
    scrollEnd();
  }
})();
