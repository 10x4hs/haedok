// 책에게 물어보기 — 리더의 시트. 서버가 읽은 쪽까지만 보고 답한다. 글은 모두 textContent 로 넣는다.
(() => {
  const wrap = document.getElementById('askWrap');
  const btn = document.getElementById('askBtn');
  if (!wrap || !btn) return;
  const book = wrap.dataset.book, csrf = wrap.dataset.csrf;
  const url = `/api/v1/books/${book}/ask`;
  const $ = id => document.getElementById(id);
  const list = $('askList'), form = $('askForm'), input = $('askQ'), send = $('askSend');
  const msg = $('askMsg'), clear = $('askClear'), empty = $('askEmpty');
  let loaded = false, busy = false, lastFocus = null;

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  function question(text) {
    const li = el('li', 'ask-q');
    li.append(el('p', '', text));
    return li;
  }

  function answer(item) {
    const none = item.kind ? item.kind !== 'ok' : item.found === false;
    const li = el('li', 'ask-a' + (none ? ' none' : ''));
    li.append(el('span', 'ask-label mono', 'AI 답변 · 틀릴 수 있어요'));
    li.append(el('p', '', item.answer));
    if (item.pages && item.pages.length) {
      const pages = el('span', 'ask-pages');
      pages.setAttribute('aria-label', '근거가 된 쪽');
      item.pages.forEach(p => pages.append(el('em', 'mono', `${p}쪽`)));
      li.append(pages);
    }
    return li;
  }

  function sync() {
    const n = list.querySelectorAll('.ask-q').length;
    empty.hidden = n > 0;
    clear.hidden = n === 0;
  }

  function render(items) {
    list.replaceChildren();
    items.slice().reverse().forEach(it => list.append(question(it.question), answer(it)));
    sync();
    list.scrollTop = list.scrollHeight;
  }

  async function load() {
    try {
      const r = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(r.status);
      const data = await r.json();
      render(data.items || []);
      if (data.read_upto > 0) $('askScope').textContent = `${data.read_upto}쪽까지 읽었어요. 거기까지만 보고 답해요.`;
      else {
        $('askScope').textContent = '아직 다 읽은 쪽이 없어요. 몇 쪽 읽고 나서 물어봐 주세요.';
      }
      loaded = true;
    } catch {
      msg.textContent = '기록을 불러오지 못했어요.';
    }
  }

  let loading = null;
  function open({ focus = true } = {}) {
    lastFocus = document.activeElement;
    wrap.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    if (!loaded && !loading) loading = load().finally(() => { loading = null; });
    if (focus) setTimeout(() => input.focus(), 50);
    return loading || Promise.resolve();
  }

  // 리더의 다른 AI 기능(reader-ai.js)이 답을 이 시트에 보여 준다: 질문 줄 + 답 카드(+ 덧붙일 노드)
  window.haedokAsk = {
    async show(q, item, extra) {
      await open({ focus: false });
      const a = answer(item);
      if (extra) a.append(extra);
      list.append(question(q), a);
      sync();
      list.scrollTop = list.scrollHeight;
      return a;
    },
    open,
    answer,
  };

  function close() {
    wrap.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    (lastFocus || btn).focus?.();
  }

  btn.addEventListener('click', open);
  wrap.addEventListener('click', e => { if (e.target === wrap || e.target.closest('[data-close]')) close(); });
  wrap.addEventListener('keydown', e => {
    e.stopPropagation(); // 리더의 ← → 쪽 넘김이 시트 안 글쓰기를 가로채지 않게
    if (e.key === 'Escape') close();
  });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q || busy) return;
    busy = true; send.disabled = true; msg.textContent = '';
    list.setAttribute('aria-busy', 'true');
    const qi = question(q), wait = el('li', 'ask-a wait');
    wait.append(el('span', 'ask-label mono', 'AI 답변 · 틀릴 수 있어요'), el('p', '', '읽은 쪽에서 찾는 중…'));
    list.append(qi, wait); sync();
    list.scrollTop = list.scrollHeight;
    try {
      const r = await fetch(url, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, Accept: 'application/json' },
        body: JSON.stringify({ question: q }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || '');
      if (data.kind === 'ok' || data.kind === 'not_found') {
        wait.replaceWith(answer(data));
        input.value = '';
        // 최근 5개만 남는다 — 화면도 같게
        while (list.querySelectorAll('.ask-q').length > 5) { list.firstElementChild.remove(); list.firstElementChild.remove(); }
        if (data.building) msg.textContent = '아직 앞쪽만 살펴봤어요. 다시 물으면 더 넓게 찾아요.';
      } else {
        qi.remove(); wait.remove();
        msg.textContent = data.answer || '지금은 답하기 어려워요.';
      }
    } catch (err) {
      qi.remove(); wait.remove();
      msg.textContent = (err && err.message) || '지금은 답하기 어려워요. 잠시 뒤에 다시 물어봐 주세요.';
    } finally {
      busy = false; send.disabled = false; sync();
      list.setAttribute('aria-busy', 'false');
      list.scrollTop = list.scrollHeight;
    }
  });

  clear.addEventListener('click', async () => {
    if (!confirm('이 책에 물어본 기록을 지울까요?')) return;
    try {
      const r = await fetch(url, { method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf } });
      if (!r.ok) throw new Error();
      render([]);
      msg.textContent = '기록을 지웠어요.';
    } catch {
      msg.textContent = '지우지 못했어요.';
    }
  });
})();
