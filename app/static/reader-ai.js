// 읽는 중의 AI — 문단에서 묻기 · AI 노트(✦) · 지난 이야기 · 등장인물 · 생각해 볼 질문.
// 서버가 읽은 쪽까지만 보낸다(app/services/reader_ai.py). 글은 모두 textContent 로 넣는다.
// 리더(reader.js)와는 이벤트로만 이야기한다: haedok:page(쪽이 바뀜) · haedok:paint(문단을 다시 그림)
// · haedok:side(오른쪽 열을 다시 그림) · haedok:verified(다 읽은 쪽이 늘어남).
(() => {
  const root = document.getElementById('aiReader');
  const stage = document.getElementById('stage');
  if (!root || !stage) return;
  const $ = id => document.getElementById(id);
  const book = root.dataset.book, csrf = root.dataset.csrf;
  const base = `/api/v1/books/${book}/ai`;
  let verified = +root.dataset.verified, chunk = +stage.dataset.start || 0, anchorNow = 0;
  const LABEL = { explain: '이 부분 쉽게 설명', word: '이 단어 뜻', summary: '여기까지 요약' };
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const short = (s, n = 40) => (s.length > n ? s.slice(0, n) + '…' : s);
  const toast = t => (window.haedokToast ? window.haedokToast(t) : null);

  async function post(path, body, method = 'POST') {
    const r = await fetch(base + path, {
      method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, Accept: 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : '지금은 답하기 어려워요. 잠시 뒤에 다시 해 주세요.');
    return d;
  }

  document.addEventListener('haedok:page', e => { chunk = e.detail.chunk; hideSel(); });
  document.addEventListener('haedok:verified', e => { verified = e.detail.verified; offerReflect(); });
  document.addEventListener('haedok:paint', () => paintMarks());
  document.addEventListener('haedok:side', () => paintSide());

  const paraText = a => {
    const ps = [...stage.querySelectorAll(`p[data-a="${a}"]`)];
    const seen = new Set();
    return ps.map(p => p.textContent).filter(t => !seen.has(t) && seen.add(t)).join(' ').replace(/[❤️😂😮😢👏💬\d\s]+$/u, '').trim();
  };

  // ---------------- 답을 책에게 물어보기 시트에 ----------------
  async function showAnswer(q, run) {
    if (!window.haedokAsk) return;
    const wait = await window.haedokAsk.show(q, { kind: 'ok', answer: '읽은 곳에서 찾는 중…' });
    wait.classList.add('wait');
    try {
      const node = await run();
      wait.replaceWith(node);
      node.scrollIntoView({ block: 'nearest' });
    } catch (err) {
      wait.replaceWith(window.haedokAsk.answer({ kind: 'down', answer: err.message }));
    }
  }

  function closeReact() { const r = $('react'); if (r && !r.hidden) { r.hidden = true; document.body.classList.remove('locked'); } }

  function quick(act, anchor, selection = '') {
    closeReact(); hideSel();
    const quote = act === 'summary' ? '' : (selection || paraText(anchor));
    const at = chunk;
    showAnswer(`✦ ${LABEL[act]}${quote ? ` · “${short(quote)}”` : ` · ${at + 1}쪽까지`}`, async () => {
      const d = await post('/quick', { chunk_index: at, anchor, action: act, selection });
      const a = window.haedokAsk.answer({ kind: d.kind, answer: d.answer, pages: d.pages });
      if (d.kind === 'ok' && d.note_id) a.append(saveBar(d.note_id, at));
      return a;
    });
  }

  // 저장 → 문단에 ✦ 가 생긴다 → (원하면) 반응으로 공유
  function saveBar(noteId, at, saved = false, shared = false) {
    const bar = el('div', 'ai-save');
    const msg = el('span', 'hint');
    const save = el('button', 'pill', '노트로 저장');
    const share = el('button', 'pill', '반응으로 공유');
    save.type = share.type = 'button';
    const paint = () => {
      save.hidden = saved;
      share.hidden = !saved || shared;
      msg.textContent = shared ? '반응으로 공유했어요' : saved ? '✦ 이 문단에 남겼어요' : '';
    };
    save.onclick = async () => {
      save.disabled = true;
      try { await post(`/notes/${noteId}/save`); saved = true; refresh(at); }
      catch (err) { msg.textContent = err.message; }
      save.disabled = false; paint();
    };
    share.onclick = async () => {
      share.disabled = true;
      try { await post(`/notes/${noteId}/share`); saved = shared = true; refresh(at); if (at === chunk && typeof loadReactions === 'function') loadReactions(chunk, { quiet: true }); }
      catch (err) { msg.textContent = err.message; }
      share.disabled = false; paint();
    };
    paint();
    bar.append(save, share, msg);
    return bar;
  }

  // ---------------- 1. 반응 시트 안의 '✦ AI에게' ----------------
  const row = $('aiRow'), quote = $('reactQuote');
  if (row && quote) {
    quote.after(row);
    row.hidden = false;
    // 반응 시트가 열린 문단: reader.js 의 sheetAnchor(전역)를 읽는다. 없으면 마지막으로 누른 문단
    const sheetA = () => (typeof sheetAnchor === 'number' ? sheetAnchor : anchorNow);
    row.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => {
      if (b.dataset.act === 'word') {
        const f = $('aiWordForm');
        f.hidden = !f.hidden;
        b.setAttribute('aria-expanded', String(!f.hidden));
        if (!f.hidden) $('aiWord').focus();
        return;
      }
      quick(b.dataset.act, sheetA());
    }));
    $('aiWordForm').addEventListener('submit', e => {
      e.preventDefault();
      const w = $('aiWord').value.trim();
      if (!w) return;
      $('aiWord').value = ''; $('aiWordForm').hidden = true;
      quick('word', sheetA(), w);
    });
    new MutationObserver(() => { if ($('react').hidden) { $('aiWordForm').hidden = true; } })
      .observe($('react'), { attributes: true, attributeFilter: ['hidden'] });
  }

  // ---------------- 1'. 글을 고르면 뜨는 막대 ----------------
  const bar = $('aiSel');
  let sel = null, selT;
  const paraOf = n => (n && (n.nodeType === 1 ? n : n.parentElement))?.closest?.('p[data-a]');
  function readSel() {
    const s = getSelection();
    if (!s || s.isCollapsed || !s.rangeCount) return null;
    const r = s.getRangeAt(0), p = paraOf(r.startContainer);
    if (!p || p !== paraOf(r.endContainer) || !stage.contains(p)) return null;
    const text = s.toString().replace(/\s+/g, ' ').trim();
    return text ? { anchor: +p.dataset.a, text: text.slice(0, 300), rect: r.getBoundingClientRect() } : null;
  }
  function hideSel() { if (bar) bar.hidden = true; sel = null; }
  function showSel() {
    bar.hidden = false;
    const w = bar.offsetWidth, h = bar.offsetHeight, r = sel.rect;
    const left = Math.max(12, Math.min(innerWidth - w - 12, r.left + r.width / 2 - w / 2));
    const top = r.top - h - 10 > 70 ? r.top - h - 10 : r.bottom + 10;
    bar.style.left = `${left}px`; bar.style.top = `${top}px`;
  }
  if (bar) {
    document.addEventListener('selectionchange', () => {
      clearTimeout(selT);
      selT = setTimeout(() => { sel = readSel(); if (sel) showSel(); else hideSel(); }, 200);
    });
    bar.addEventListener('pointerdown', e => e.preventDefault());   // 누르는 순간 고른 글이 풀리지 않게
    bar.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => {
      if (!sel) return;
      const { anchor, text } = sel, act = b.dataset.act;
      if (act === 'word' && text.length > 30) { toast('낱말 하나만 골라 주세요'); return; }
      getSelection()?.removeAllRanges();
      quick(act, anchor, act === 'summary' ? '' : text);
    }));
  }

  // ---------------- 2. AI 노트: 문단 여백의 ✦ · 다시 열기 · 오른쪽 열 ----------------
  const notes = new Map();
  const notesOf = c => {
    if (!notes.has(c)) notes.set(c, fetch(`${base}/notes?chunk=${c}`, { credentials: 'same-origin' })
      .then(r => (r.ok ? r.json() : { items: [] })).then(d => d.items || []).catch(() => []));
    return notes.get(c);
  };
  function refresh(c) { notes.delete(c); if (c === chunk) { paintMarks(); paintSide(); } }

  async function paintMarks() {
    const c = chunk, items = await notesOf(c);
    if (c !== chunk) return;
    const by = new Map();
    items.forEach(n => by.set(n.anchor, (by.get(n.anchor) || 0) + 1));
    stage.querySelectorAll('p[data-a]').forEach(p => {
      p.querySelector(':scope > .ax')?.remove();
      p.classList.remove('has-ax');
      const n = by.get(+p.dataset.a);
      // 문단이 여러 페이지에 걸치면 첫 조각의 여백에만 붙인다
      if (!n || p.classList.contains('cont')) return;
      const b = el('button', 'ax');
      b.type = 'button';
      b.dataset.a = p.dataset.a;
      b.setAttribute('aria-label', `내 AI 노트 ${n}개 보기`);
      b.title = '내 AI 노트';
      p.classList.add('has-ax');
      p.prepend(b);
    });
  }
  // 문단을 누르면 반응 시트가 열리지만 ✦ 를 누르면 노트가 열린다(같은 클릭을 먼저 가로챈다)
  stage.addEventListener('click', e => {
    const ax = e.target.closest('.ax');
    const p = e.target.closest('p[data-a]');
    if (p) anchorNow = +p.dataset.a;
    if (!ax) return;
    e.stopPropagation(); e.preventDefault();
    openNotes(+ax.dataset.a);
  }, true);

  // ✦ 를 누를 때는 쪽 넘김(모서리 접기)이 시작되지 않게 넘김 라이브러리보다 먼저 막는다
  ['pointerdown', 'mousedown', 'touchstart', 'mousemove', 'pointermove'].forEach(t => stage.addEventListener(t, e => {
    if (e.target.closest && e.target.closest('.ax')) e.stopPropagation();
  }, { capture: true, passive: true }));

  const noteWrap = $('aiNote');
  function closeNotes() { noteWrap.hidden = true; document.body.classList.remove('locked'); }
  noteWrap.addEventListener('click', e => { if (e.target === noteWrap || e.target.closest('[data-close]')) closeNotes(); });
  noteWrap.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Escape') closeNotes(); });

  async function openNotes(anchor) {
    const at = chunk, items = (await notesOf(at)).filter(n => n.anchor === anchor);
    const list = $('aiNoteList');
    list.replaceChildren();
    $('aiNoteMsg').textContent = '';
    items.forEach(n => {
      const q = el('li', 'ask-q');
      q.append(el('p', '', `✦ ${n.label}${n.quote ? ` · “${short(n.quote, 60)}”` : ''}`));
      const a = window.haedokAsk ? window.haedokAsk.answer({ kind: 'ok', answer: n.answer, pages: n.pages }) : el('li', 'ask-a', n.answer);
      a.append(saveBar(n.id, at, true, n.shared));
      const del = el('button', 'link-btn ai-del', '노트 지우기');
      del.type = 'button';
      del.onclick = async () => {
        if (!confirm('이 AI 노트를 지울까요? 이미 공유한 반응은 리더에서 따로 지울 수 있어요.')) return;
        try { await post(`/notes/${n.id}`, null, 'DELETE'); q.remove(); a.remove(); refresh(at); $('aiNoteMsg').textContent = '지웠어요.'; }
        catch (err) { $('aiNoteMsg').textContent = err.message; }
      };
      a.append(del);
      list.append(q, a);
    });
    noteWrap.hidden = false;
    document.body.classList.add('locked');
    noteWrap.querySelector('.sheet').focus();
  }

  async function paintSide() {
    const side = $('sideReacts');
    if (!side || getComputedStyle($('side')).display === 'none') return;
    const c = chunk, items = await notesOf(c);
    if (c !== chunk || !items.length) return;
    side.querySelector('.side-ai')?.remove();
    side.querySelector('li.muted')?.remove();
    const li = el('li', 'side-ai');
    li.append(el('b', 'side-ai-h', `✦ 내 AI 노트 ${items.length}`));
    items.forEach(n => {
      const b = el('button', 'ai-side-n', `${n.label} · ${short(n.answer, 70)}`);
      b.type = 'button';
      b.onclick = () => openNotes(n.anchor);
      li.append(b);
    });
    side.prepend(li);
    const cnt = $('sideCount');
    if (cnt) cnt.textContent = String((+cnt.textContent || 0) + items.length);
  }

  // ---------------- 3. 지난 이야기 · 등장인물 (시트의 빠른 동작 + 위 카드) ----------------
  const quickRow = $('aiQuick'), scope = document.querySelector('#askWrap .ask-scope');
  if (quickRow && scope) { scope.after(quickRow); quickRow.hidden = false; }

  const recapNode = d => window.haedokAsk.answer({ kind: d.kind, answer: d.answer });
  function charsNode(d) {
    if (d.kind !== 'ok') return window.haedokAsk.answer({ kind: d.kind, answer: d.answer });
    const a = window.haedokAsk.answer({ kind: 'ok', answer: d.items.length ? '' : '아직 읽은 부분에서 등장인물을 찾지 못했어요.' });
    if (d.items.length) {
      a.querySelector('p')?.remove();
      const ul = el('ul', 'ai-chars');
      d.items.forEach(c => { const li = el('li'); li.append(el('b', '', c.name), el('span', '', c.line)); ul.append(li); });
      a.append(ul);
    }
    return a;
  }
  const bookAct = {
    recap: () => showAnswer(`✦ 지난 이야기 · ${verified + 1}쪽까지`, async () => recapNode(await post('/recap'))),
    characters: () => showAnswer(`✦ 등장인물 · ${verified + 1}쪽까지`, async () => charsNode(await post('/characters'))),
  };
  quickRow?.querySelectorAll('[data-book-act]').forEach(b => b.addEventListener('click', () => bookAct[b.dataset.bookAct]()));

  const recapCard = $('aiRecap'), reflectCard = $('aiReflect');
  const recapKey = `ai-recap:${book}:${verified}`;
  async function showRecap() {
    recapCard.hidden = false;
    $('aiRecapUpto').textContent = `1~${verified + 1}쪽`;
    try {
      const d = await post('/recap');
      if (d.kind !== 'ok') { recapCard.hidden = true; if (d.kind === 'capped') toast(d.answer); return; }
      $('aiRecapText').textContent = d.answer;
    } catch (err) { recapCard.hidden = true; }
  }
  $('aiRecapX').onclick = () => { recapCard.hidden = true; store.set(recapKey, '1'); };
  $('aiRecapChars').onclick = () => { recapCard.hidden = true; store.set(recapKey, '1'); bookAct.characters(); };
  // 하루 넘게 쉬었다가 펼쳤고, 두 쪽 넘게 읽었고, 이 위치의 지난 이야기를 닫은 적이 없으면
  if (root.dataset.away === '1' && verified >= 1 && !store.get(recapKey)) showRecap();

  // ---------------- 3c. 10쪽마다 생각해 볼 질문 하나(누를 때만 AI) ----------------
  let reflectAt = null;
  function offerReflect() {
    const done = verified + 1;
    if (done < 10 || done % 10 || !recapCard.hidden) return;
    const key = `ai-reflect:${book}:${done}`;
    if (store.get(key)) return;
    reflectAt = done - 1;
    $('aiReflectUpto').textContent = `${done}쪽`;
    $('aiReflectText').textContent = `${done}쪽까지 읽었어요. 생각해 볼 질문을 하나 받아 볼까요?`;
    $('aiReflectGet').hidden = false;
    $('aiReflectForm').hidden = true;
    reflectCard.hidden = false;
    reflectCard.dataset.key = key;
  }
  const closeReflect = () => { reflectCard.hidden = true; store.set(reflectCard.dataset.key, '1'); };
  $('aiReflectX').onclick = closeReflect;
  $('aiReflectGet').onclick = async () => {
    $('aiReflectGet').disabled = true;
    try {
      const d = await post('/reflect');
      if (d.kind !== 'ok') { $('aiReflectText').textContent = d.answer; return; }
      reflectAt = d.pages[0] - 1;
      $('aiReflectText').textContent = d.answer;
      $('aiReflectGet').hidden = true;
      $('aiReflectForm').hidden = false;
      $('aiReflectIn').focus();
    } catch (err) { $('aiReflectText').textContent = err.message; }
    finally { $('aiReflectGet').disabled = false; }
  };
  $('aiReflectForm').addEventListener('submit', async e => {
    e.preventDefault();
    const t = $('aiReflectIn').value.trim();
    if (!t || reflectAt == null) return;
    const r = await fetch(`/api/v1/books/${book}/reactions`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ chunk_index: reflectAt, anchor: 0, comment: t }),
    }).catch(() => null);
    if (!r || !r.ok) { toast('남기지 못했어요'); return; }
    toast(`${reflectAt + 1}쪽에 반응으로 남겼어요`);
    closeReflect();
    if (reflectAt === chunk && typeof loadReactions === 'function') loadReactions(chunk, { quiet: true });
  });
  offerReflect();
})();
