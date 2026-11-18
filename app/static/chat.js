// 대화 화면(/chat/room/:id?ch=book|chat · /chat/dm/:id).
// 처음에 최근 50개, 그다음은 3초마다 새 메시지만 묻는다(presence.js 규칙: 탭이 보일 때만, 실패하면 간격 두 배 · 최대 60초).
// 잠긴 메시지(도장보다 덜 읽음)는 서버가 본문을 아예 주지 않는다. 그 id를 폴링 때 함께 보내면 따라잡았을 때
// 본문이 담긴 모양이 'updates'로 온다. 반응 · 삭제처럼 바뀐 메시지도 updates로 온다.
// 묶음(같은 사람 · 5분 안) · 날짜 구분선 · '여기부터 안 읽음' · 반응 · 답장 · ||스포일러|| · @멘션 · 읽음.
// DOM은 textContent 로만 만든다(사용자 글을 innerHTML 에 넣지 않는다).
(() => {
  const root = document.getElementById('chat');
  if (!root) return;
  const $ = s => root.querySelector(s);
  const api = root.dataset.api;
  const kind = root.dataset.kind;
  const channel = root.dataset.channel;
  const owner = root.dataset.owner === 'true';
  const roomId = root.dataset.room;
  const csrf = (document.querySelector('meta[name="csrf"]') || {}).content || '';
  let data = { names: [], emojis: [] };
  try { data = JSON.parse(document.getElementById('chatData').textContent); } catch (e) { /* 기본값 */ }
  const names = (data.names || []).slice().sort((a, b) => b.length - a.length);
  const EMOJIS = data.emojis || [];
  const log = $('#log'), older = $('#older'), empty = $('#empty'), form = $('#compose');
  const text = $('#text'), hint = $('#hint'), sendBtn = $('#send');
  const replying = $('#replying'), replyWho = $('#replyWho'), replyText = $('#replyText');
  const BASE = 3000, MAX = 60000, GROUP_MS = 5 * 60 * 1000;
  const msgs = new Map();   // id -> 마지막으로 받은 메시지 모양
  const locked = new Set(); // 잠긴 채 들고 있는 id
  let lastId = 0, oldest = 0, since = '', delay = BASE, timer = 0, inFlight = false;
  let readUpto = null, seenUpto = 0, replyTo = null, openBar = null;

  const toast = (msg, k = 'info') => (window.haedokToast ? window.haedokToast(msg, { kind: k }) : null);
  const el = (tag, cls, txt) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  };
  const SPOILER = /\|\|([\s\S]+?)\|\|/g;
  const masked = s => s.replace(SPOILER, '[스포일러]');

  // ---------- 시간 · 날짜
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  function clock(d) { return d.toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' }); }
  function dayLabel(d) {
    const now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
    if (sameDay(d, now)) return '오늘';
    if (sameDay(d, y)) return '어제';
    return d.toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
  }

  // ---------- 본문: ||스포일러||와 @닉네임
  function mentions(parent, s) {
    if (!names.length || s.indexOf('@') < 0) { parent.append(document.createTextNode(s)); return; }
    let i = 0, buf = '';
    while (i < s.length) {
      if (s[i] === '@') {
        const hit = names.find(n => s.startsWith(n, i + 1));
        if (hit) {
          if (buf) { parent.append(document.createTextNode(buf)); buf = ''; }
          parent.append(el('span', 'ch-mention', '@' + hit));
          i += hit.length + 1;
          continue;
        }
      }
      buf += s[i]; i += 1;
    }
    if (buf) parent.append(document.createTextNode(buf));
  }
  function spoiler(parent, s) {
    // 가리기는 예의일 뿐 보안이 아니다 — 본문은 이미 와 있고, 탭하면 흐림이 걷힌다
    const sp = el('span', 'ch-spoiler');
    sp.tabIndex = 0;
    sp.setAttribute('role', 'button');
    sp.setAttribute('aria-label', '스포일러 · 눌러서 보기');
    const inner = el('span', 'ch-spoiler-t');
    mentions(inner, s);
    sp.append(inner);
    const open = ev => { ev.stopPropagation(); sp.classList.add('open'); sp.removeAttribute('role'); sp.removeAttribute('aria-label'); sp.tabIndex = -1; };
    sp.addEventListener('click', open, { once: true });
    sp.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(ev); } });
    parent.append(sp);
  }
  function richText(parent, body, veiledAll) {
    if (veiledAll) { spoiler(parent, body); return; }
    let last = 0;
    body.replace(SPOILER, (m, inner, at) => {
      if (at > last) mentions(parent, body.slice(last, at));
      spoiler(parent, inner);
      last = at + m.length;
      return m;
    });
    if (last < body.length) mentions(parent, body.slice(last));
  }

  // ---------- 한 메시지
  function bubble(m) {
    if (m.deleted) return el('p', 'ch-bubble ch-gone', '삭제된 메시지');
    if (m.sealed) {
      const s = el('p', 'ch-bubble ch-seal');
      s.append(el('span', '', '🔒'), document.createTextNode(` ${m.page}쪽을 읽으면 열려요`));
      return s;
    }
    const b = el('p', 'ch-bubble');
    richText(b, m.body || '', !!m.veiled);
    return b;
  }

  function quote(m) {
    if (!m.reply) return null;
    const q = el('button', 'ch-quote' + (m.reply.locked || m.reply.gone ? ' locked' : ''));
    q.type = 'button';
    q.append(el('b', '', (m.reply.mine ? '나' : m.reply.nickname) + '에게 답장'), el('span', '', m.reply.text));
    q.addEventListener('click', () => {
      const t = log.querySelector(`li[data-id="${m.reply.id}"]`);
      if (t) { t.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); t.classList.add('flash'); setTimeout(() => t.classList.remove('flash'), 1200); }
    });
    return q;
  }

  function chips(m) {
    if (!m.reactions || !m.reactions.length) return null;
    const box = el('div', 'ch-reacts');
    m.reactions.forEach(r => {
      const c = el('button', 'ch-react' + (r.mine ? ' mine' : ''));
      c.type = 'button';
      c.setAttribute('aria-pressed', String(!!r.mine));
      c.setAttribute('aria-label', `${r.emoji} ${r.count}명${r.mine ? ' · 내 반응(누르면 빼기)' : ''}`);
      c.append(el('span', '', r.emoji), el('span', 'mono', String(r.count)));
      c.addEventListener('click', () => react(m, r.mine ? null : r.emoji));
      box.append(c);
    });
    return box;
  }

  function bar(m) {
    // 빠른 막대: 반응 이모지 · 답장 · 지우기 · 신고
    const b = el('div', 'ch-bar');
    b.setAttribute('role', 'toolbar');
    b.setAttribute('aria-label', '메시지 반응과 메뉴');
    const canTouch = !m.deleted && !m.sealed;
    if (canTouch) {
      const mine = (m.reactions || []).find(r => r.mine);
      EMOJIS.forEach(e => {
        const x = el('button', 'ch-emo' + (mine && mine.emoji === e ? ' mine' : ''), e);
        x.type = 'button';
        x.setAttribute('aria-label', `${e} 반응${mine && mine.emoji === e ? ' 빼기' : ''}`);
        x.addEventListener('click', () => react(m, mine && mine.emoji === e ? null : e));
        b.append(x);
      });
      const r = el('button', 'ch-act', '답장');
      r.type = 'button';
      r.addEventListener('click', () => startReply(m));
      b.append(r);
    }
    if (!m.deleted && (m.mine || (kind === 'room' && owner))) {
      const d = el('button', 'ch-act', m.mine ? '지우기' : '방장 지우기');
      d.type = 'button';
      d.addEventListener('click', () => remove(m.id));
      b.append(d);
    }
    if (!m.mine && !m.deleted) {
      const p = el('button', 'ch-act', '신고');
      p.type = 'button';
      p.addEventListener('click', () => report(m.id));
      b.append(p);
    }
    return b.children.length ? b : null;
  }

  function avatarOf(m) {
    const a = el('span', 'avatar ch-face');
    if (m.avatar) { const i = el('img'); i.src = m.avatar; i.alt = ''; a.append(i); } else a.textContent = (m.nickname || '?').slice(0, 1);
    return a;
  }

  function node(m, first, lastOfGroup, seen) {
    const li = el('li', 'ch-msg' + (m.mine ? ' me' : '') + (first ? ' first' : ' cont') + (lastOfGroup ? ' last' : ''));
    li.dataset.id = m.id;
    if (!m.mine) li.append(first ? avatarOf(m) : el('span', 'ch-face-gap'));
    const col = el('div', 'ch-col');
    if (first) {
      const head = el('div', 'ch-head');
      if (!m.mine) head.append(el('b', 'ch-who', m.nickname));
      const t = el('time', 'mono', m.at ? clock(new Date(m.at)) : '');
      if (m.at) t.dateTime = m.at;
      head.append(t);
      col.append(head);
    }
    const q = quote(m);
    if (q) col.append(q);
    const line = el('div', 'ch-line');
    line.append(bubble(m));
    if (m.kind === 'book' && m.page && !m.deleted && !m.sealed) line.append(el('em', 'ch-pg mono', `${m.page}쪽`));
    const more = el('button', 'ch-more', '☺');
    more.type = 'button';
    more.setAttribute('aria-label', `${m.mine ? '내' : m.nickname + '의'} 메시지 반응 · 답장 · 메뉴`);
    more.setAttribute('aria-expanded', String(openBar === m.id));
    more.addEventListener('click', () => { openBar = openBar === m.id ? null : m.id; draw(); });
    line.append(more);
    col.append(line);
    const c = chips(m);
    if (c) col.append(c);
    if (openBar === m.id) { const b = bar(m); if (b) col.append(b); }
    if (seen) col.append(el('span', 'ch-seen', '읽음'));
    li.append(col);
    return li;
  }

  // ---------- 전체 그리기(묶음 · 날짜 · 안 읽음 · 읽음)
  function draw() {
    const list = [...msgs.values()].sort((a, b) => a.id - b.id);
    let seenId = 0;
    if (kind === 'dm' && seenUpto) list.forEach(m => { if (m.mine && m.id <= seenUpto) seenId = m.id; });
    const frag = document.createDocumentFragment();
    let prev = null, dividerDone = false;
    list.forEach((m, i) => {
      const d = new Date(m.at || Date.now());
      if (!prev || !sameDay(new Date(prev.at || Date.now()), d)) {
        const sep = el('li', 'ch-day');
        sep.setAttribute('role', 'separator');
        sep.append(el('span', '', dayLabel(d)));
        frag.append(sep);
        prev = null;
      }
      if (!dividerDone && readUpto !== null && m.id > readUpto && !m.mine) {
        const u = el('li', 'ch-new');
        u.setAttribute('role', 'separator');
        u.append(el('span', '', '여기부터 안 읽음'));
        frag.append(u);
        dividerDone = true;
        prev = null;
      }
      const first = !prev || prev.user_id !== m.user_id || (new Date(m.at) - new Date(prev.at)) > GROUP_MS;
      const next = list[i + 1];
      const lastOfGroup = !next || next.user_id !== m.user_id || (new Date(next.at) - new Date(m.at)) > GROUP_MS || !sameDay(new Date(next.at), d);
      frag.append(node(m, first, lastOfGroup, m.id === seenId));
      prev = m;
      if (m.sealed) locked.add(m.id); else locked.delete(m.id);
    });
    log.replaceChildren(frag);
    empty.hidden = list.length > 0;
  }

  function nearBottom() { return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160; }
  function toBottom() { window.scrollTo({ top: document.documentElement.scrollHeight }); }

  // ---------- 서버
  async function get(params) {
    const u = new URL(api, location.origin);
    if (kind === 'room') u.searchParams.set('channel', channel);
    Object.entries(params).forEach(([k, v]) => { if (v !== '' && v != null) u.searchParams.set(k, v); });
    const r = await fetch(u, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }
  async function send_(url, method, body) {
    const r = await fetch(url, {
      method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: body ? JSON.stringify(body) : undefined,
    });
    const d = r.status === 204 ? {} : await r.json().catch(() => ({}));
    return { ok: r.ok, d };
  }

  function side(d) {
    if (d.seen_upto != null) seenUpto = d.seen_upto;
    const other = channel === 'book' ? 'chat' : 'book';
    const cnt = root.querySelector(`[data-ch-count="${other}"]`);
    if (cnt) { cnt.textContent = d.other_unread; cnt.hidden = !d.other_unread; }
    const notice = $('#notice'), nt = $('#noticeText');
    if (notice && d.notice !== undefined) { nt.textContent = d.notice || ''; notice.hidden = !d.notice; }
  }

  async function first() {
    try {
      const d = await get({});
      d.items.forEach(m => msgs.set(m.id, m));
      lastId = d.last_id; since = d.now; readUpto = d.read_upto;
      oldest = d.items.length ? d.items[0].id : 0;
      older.hidden = !d.has_more;
      side(d);
      draw();
      log.setAttribute('aria-busy', 'false');
      const divider = log.querySelector('.ch-new');
      if (divider) divider.scrollIntoView({ block: 'center' }); else toBottom();
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
      d.items.forEach(m => msgs.set(m.id, m));
      if (d.items.length) oldest = d.items[0].id;
      older.hidden = !d.has_more;
      draw();
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
      const before = seenUpto;
      const d = await get({ after: lastId, locked: [...locked].slice(-50).join(','), deleted_since: since });
      d.items.forEach(m => msgs.set(m.id, m));
      d.updates.forEach(m => msgs.set(m.id, m));
      d.deleted.forEach(id => { const m = msgs.get(id); if (m) msgs.set(id, Object.assign({}, m, { deleted: true, body: undefined, reactions: [], reply: undefined })); });
      lastId = d.last_id; since = d.now;
      side(d);
      if (d.items.length || d.updates.length || d.deleted.length || seenUpto !== before) draw();
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

  // ---------- 동작
  function startReply(m) {
    replyTo = m.id;
    replyWho.textContent = m.mine ? '나' : m.nickname;
    const t = masked(m.body || '');
    replyText.textContent = t.length > 60 ? t.slice(0, 60) + '…' : t;
    replying.hidden = false;
    openBar = null; draw();
    text.focus();
  }
  function cancelReply() { replyTo = null; replying.hidden = true; }

  async function react(m, emoji) {
    const url = `/api/v1/chat/messages/${m.id}/reaction`;
    const { ok, d } = emoji ? await send_(url, 'PUT', { emoji }) : await send_(url, 'DELETE');
    if (!ok) { toast(d.detail || '반응을 남기지 못했어요', 'error'); return; }
    msgs.set(d.id, d);
    openBar = null;
    draw();
  }

  async function sendMsg(ev) {
    ev.preventDefault();
    const body = text.value.trim();
    if (!body) { hint.textContent = '메시지를 적어 주세요'; text.focus(); return; }
    sendBtn.disabled = true;
    try {
      const payload = { text: body, reply_to: replyTo };
      if (kind === 'room') payload.channel = channel;  // 채널만 알린다 — 쪽 도장은 서버가 찍는다
      const { ok, d } = await send_(api, 'POST', payload);
      if (!ok) { hint.textContent = d.detail || '보내지 못했어요. 잠시 뒤 다시 보내 주세요'; return; }
      msgs.set(d.id, d);
      text.value = ''; hint.textContent = ''; grow(); cancelReply();
      draw(); toBottom();
    } catch (e) {
      hint.textContent = '보내지 못했어요. 연결을 확인해 주세요';
    } finally {
      sendBtn.disabled = false;
      text.focus();
    }
  }

  async function remove(id) {
    if (!window.confirm('이 메시지를 지울까요? 모두에게 ‘삭제된 메시지’로 보여요.')) return;
    const { ok } = await send_(`/api/v1/chat/messages/${id}`, 'DELETE');
    if (ok) { const m = msgs.get(id); if (m) msgs.set(id, Object.assign({}, m, { deleted: true, reactions: [] })); openBar = null; draw(); }
    else toast('지우지 못했어요', 'error');
  }

  async function report(id) {
    const reason = window.prompt('신고할까요? 이유를 적어 주면 관리자가 봐요(비워 둬도 돼요).', '');
    if (reason === null) return;
    const { ok, d } = await send_(`/api/v1/chat/messages/${id}/report`, 'POST', { reason });
    toast(ok ? '신고했어요. 관리자가 확인할게요' : (d.detail || '신고하지 못했어요'), ok ? 'success' : 'error');
    openBar = null; draw();
  }

  function grow() {
    text.style.height = 'auto';
    text.style.height = Math.min(text.scrollHeight, 160) + 'px';
    const left = 500 - text.value.length;
    if (left <= 50) hint.textContent = `${left}자 남았어요`;
    else if (/자 남았어요$/.test(hint.textContent)) hint.textContent = '';
  }

  // 공지(방장)
  const nf = document.getElementById('noticeForm');
  if (nf && roomId) {
    nf.addEventListener('submit', async ev => {
      ev.preventDefault();
      const v = document.getElementById('noticeIn').value;
      const { ok, d } = await send_(`/api/v1/chat/room/${roomId}/notice`, 'PUT', { text: v });
      if (!ok) { toast(d.detail || '공지를 저장하지 못했어요', 'error'); return; }
      side({ notice: d.notice, other_unread: 0 });
      toast(d.notice ? '공지를 올렸어요' : '공지를 내렸어요', 'success');
      const det = nf.closest('details'); if (det) det.open = false;
    });
  }

  // 멤버 패널(좁은 화면에서는 접어 둔다)
  const mb = document.getElementById('membersBtn'), panel = document.getElementById('members');
  if (mb && panel) mb.addEventListener('click', () => {
    const open = panel.classList.toggle('open');
    mb.setAttribute('aria-expanded', String(open));
    if (open) panel.scrollIntoView({ block: 'nearest' });
  });

  document.addEventListener('keydown', e => { if (e.key === 'Escape') { if (openBar !== null) { openBar = null; draw(); } else if (replyTo) cancelReply(); } });
  $('#replyCancel').addEventListener('click', cancelReply);
  text.addEventListener('input', grow);
  text.addEventListener('keydown', e => {
    // 한글 조합 중(isComposing) Enter는 글자 확정이라 보내지 않는다. Shift+Enter는 줄바꿈
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !matchMedia('(pointer: coarse)').matches) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener('submit', sendMsg);
  older.addEventListener('click', loadOlder);
  first();
})();
