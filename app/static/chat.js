// 대화 화면(/chat/room/:id · /chat/dm/:id) — 처음에 최근 50개, 그다음은 3초마다 새 메시지만 묻는다.
// presence.js 와 같은 규칙: 탭이 보일 때만 폴링하고, 실패하면 간격을 두 배씩(최대 60초) 늘린다.
// 잠긴 메시지(도장보다 덜 읽음)는 서버가 본문을 아예 주지 않는다. 그 id를 폴링 때 함께 보내면,
// 그 사이 따라잡았을 때 본문이 담긴 새 모양이 'updates' 로 온다. DOM은 textContent 로만 만든다.
(() => {
  const root = document.getElementById('chat');
  if (!root) return;
  const $ = s => root.querySelector(s);
  const api = root.dataset.api;
  const kind = root.dataset.kind;
  const owner = root.dataset.owner === 'true';
  const csrf = (document.querySelector('meta[name="csrf"]') || {}).content || '';
  const log = $('#log'), older = $('#older'), empty = $('#empty'), form = $('#compose');
  const text = $('#text'), hint = $('#hint'), sendBtn = $('#send'), veil = $('#veil');
  const BASE = 3000, MAX = 60000;
  const byId = new Map();   // id -> 마지막으로 받은 메시지 모양
  const locked = new Set(); // 잠긴 채 들고 있는 id
  let lastId = 0, oldest = 0, since = '', delay = BASE, timer = 0, inFlight = false, mode = 'book';

  const toast = (msg, kind = 'info') => (window.haedokToast ? window.haedokToast(msg, { kind }) : null);
  const el = (tag, cls, txt) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  };

  function when(iso) {
    if (!iso) return '';
    const d = new Date(iso), now = new Date();
    const t = d.toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' });
    return d.toDateString() === now.toDateString() ? t : `${d.getMonth() + 1}.${d.getDate()} ${t}`;
  }

  function nearBottom() {
    return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
  }
  function toBottom() { window.scrollTo({ top: document.documentElement.scrollHeight }); }

  function actions(m) {
    if (m.deleted) return null;
    const canDelete = m.mine || (kind === 'room' && owner);
    const canReport = !m.mine;
    if (!canDelete && !canReport) return null;
    const box = el('details', 'ch-acts');
    const sum = el('summary', '', '⋯');
    sum.setAttribute('aria-label', `${m.nickname}의 메시지 메뉴`);
    box.append(sum);
    const menu = el('div', 'ch-acts-menu');
    if (canDelete) {
      const b = el('button', '', m.mine ? '지우기' : '방장 권한으로 지우기');
      b.type = 'button';
      b.addEventListener('click', () => remove(m.id));
      menu.append(b);
    }
    if (canReport) {
      const b = el('button', '', '신고');
      b.type = 'button';
      b.addEventListener('click', () => report(m.id));
      menu.append(b);
    }
    box.append(menu);
    return box;
  }

  function build(m) {
    const li = el('li', 'ch-msg' + (m.mine ? ' me' : ''));
    li.dataset.id = m.id;
    const head = el('div', 'ch-head');
    head.append(el('b', 'ch-who', m.mine ? '나' : m.nickname));
    const t = el('time', 'mono muted', when(m.at));
    if (m.at) t.dateTime = m.at;
    head.append(t);
    if (m.kind === 'book' && m.page && !m.deleted) head.append(el('em', 'tag', `${m.page}쪽`));
    const a = actions(m);
    if (a) head.append(a);
    li.append(head);
    locked.delete(m.id);
    if (m.deleted) {
      li.append(el('p', 'ch-body ch-gone', '삭제된 메시지'));
    } else if (m.sealed) {
      locked.add(m.id);
      const s = el('p', 'ch-body ch-seal');
      s.append(el('span', '', '🔒'), document.createTextNode(` ${m.page}쪽을 읽으면 열려요`));
      li.append(s);
    } else if (m.veiled) {
      // 가리기: 본문은 이미 와 있다(예의일 뿐 보안이 아니다). 탭하면 흐림이 걷힌다
      const b = el('button', 'ch-body ch-veiled');
      b.type = 'button';
      b.setAttribute('aria-label', '가린 메시지 · 탭해서 보기');
      b.append(el('span', 'ch-veil-text', m.body));
      b.addEventListener('click', () => {
        b.classList.add('open');
        b.removeAttribute('aria-label');
      }, { once: true });
      li.append(b);
    } else {
      li.append(el('p', 'ch-body', m.body));
    }
    return li;
  }

  function put(m, where = 'end') {
    byId.set(m.id, m);
    const node = build(m);
    const old = log.querySelector(`li[data-id="${m.id}"]`);
    if (old) old.replaceWith(node);
    else if (where === 'start') log.prepend(node);
    else log.append(node);
  }

  function refreshEmpty() { empty.hidden = log.children.length > 0; }

  async function get(params) {
    const u = new URL(api, location.origin);
    Object.entries(params).forEach(([k, v]) => { if (v !== '' && v != null) u.searchParams.set(k, v); });
    const r = await fetch(u, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }

  async function first() {
    try {
      const d = await get({});
      d.items.forEach(m => put(m));
      lastId = d.last_id; since = d.now;
      oldest = d.items.length ? d.items[0].id : 0;
      older.hidden = !d.has_more;
      log.setAttribute('aria-busy', 'false');
      refreshEmpty();
      toBottom();
    } catch (e) {
      hint.textContent = '메시지를 불러오지 못했어요. 잠시 뒤 다시 시도해요';
    }
    schedule(BASE);
  }

  async function loadOlder() {
    older.disabled = true;
    const h = document.documentElement.scrollHeight;
    try {
      const d = await get({ before: oldest });
      d.items.slice().reverse().forEach(m => put(m, 'start'));
      if (d.items.length) oldest = d.items[0].id;
      older.hidden = !d.has_more;
      window.scrollBy(0, document.documentElement.scrollHeight - h);  // 보던 자리 유지
    } catch (e) {
      toast('이전 메시지를 불러오지 못했어요', 'error');
    }
    older.disabled = false;
  }

  async function poll() {
    if (inFlight || document.hidden) return true;
    inFlight = true;
    try {
      const stick = nearBottom();
      const d = await get({ after: lastId, locked: [...locked].slice(-50).join(','), deleted_since: since });
      d.items.forEach(m => put(m));
      d.updates.forEach(m => put(m));
      d.deleted.forEach(id => { const m = byId.get(id); if (m) put(Object.assign({}, m, { deleted: true, body: undefined, sealed: false, veiled: false })); });
      lastId = d.last_id; since = d.now;
      refreshEmpty();
      if (d.items.length && stick) toBottom();
      return true;
    } catch (e) {
      return false;
    } finally {
      inFlight = false;
    }
  }

  function schedule(ms) {
    clearTimeout(timer); timer = 0;
    if (document.hidden) return;  // 가려진 동안은 타이머도 두지 않는다
    timer = setTimeout(async () => {
      const ok = await poll();
      delay = ok ? BASE : Math.min(delay * 2, MAX);
      schedule(delay);
    }, ms);
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(timer); timer = 0; } else { delay = BASE; schedule(0); }
  });

  async function send(ev) {
    ev.preventDefault();
    const body = text.value.trim();
    if (!body) { hint.textContent = '메시지를 적어 주세요'; text.focus(); return; }
    sendBtn.disabled = true;
    try {
      const r = await fetch(api, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ text: body, mode, veiled: !!(veil && veil.checked) }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { hint.textContent = d.detail || '보내지 못했어요. 잠시 뒤 다시 보내 주세요'; return; }
      put(d);
      text.value = ''; hint.textContent = ''; grow();
      if (veil) veil.checked = false;
      refreshEmpty(); toBottom();
    } catch (e) {
      hint.textContent = '보내지 못했어요. 연결을 확인해 주세요';
    } finally {
      sendBtn.disabled = false;
      text.focus();
    }
  }

  async function remove(id) {
    if (!window.confirm('이 메시지를 지울까요? 모두에게 ‘삭제된 메시지’로 보여요.')) return;
    const r = await fetch(`/api/v1/chat/messages/${id}`, { method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf } });
    if (r.ok) { const m = byId.get(id); if (m) put(Object.assign({}, m, { deleted: true })); }
    else toast('지우지 못했어요', 'error');
  }

  async function report(id) {
    const reason = window.prompt('신고할까요? 이유를 적어 주면 관리자가 봐요(비워 둬도 돼요).', '');
    if (reason === null) return;
    const r = await fetch(`/api/v1/chat/messages/${id}/report`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ reason }),
    });
    const d = await r.json().catch(() => ({}));
    toast(r.ok ? '신고했어요. 관리자가 확인할게요' : (d.detail || '신고하지 못했어요'), r.ok ? 'success' : 'error');
    const open = log.querySelector(`li[data-id="${id}"] details[open]`);
    if (open) open.open = false;
  }

  function grow() {
    text.style.height = 'auto';
    text.style.height = Math.min(text.scrollHeight, 160) + 'px';
    const left = 500 - text.value.length;
    if (left <= 50) hint.textContent = `${left}자 남았어요`;
    else if (/자 남았어요$/.test(hint.textContent)) hint.textContent = '';
  }

  // 방 채팅의 두 가지 보내기: 책 이야기(쪽 도장) · 수다(모두에게)
  root.querySelectorAll('.ch-mode [role="radio"]').forEach(b => {
    b.addEventListener('click', () => {
      mode = b.dataset.mode;
      root.querySelectorAll('.ch-mode [role="radio"]').forEach(x => x.setAttribute('aria-checked', String(x === b)));
    });
  });

  text.addEventListener('input', grow);
  text.addEventListener('keydown', e => {
    // 한글 조합 중(isComposing) Enter는 글자 확정이라 보내지 않는다. Shift+Enter는 줄바꿈
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !matchMedia('(pointer: coarse)').matches) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener('submit', send);
  older.addEventListener('click', loadOlder);
  first();
})();
