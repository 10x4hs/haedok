// 대화 화면(/chat/room/:id?ch=book|chat · /chat/dm/:id) — 화면 높이를 채우는 앱 셸, 메시지 칸(#scroll)만 스크롤한다.
// 처음에 최근 50개, 그다음은 3초마다 새 메시지만 묻는다(presence.js 규칙: 탭이 보일 때만, 실패하면 간격 두 배 · 최대 60초).
// 잠긴 메시지(도장보다 덜 읽음)는 서버가 본문을 아예 주지 않는다. 그 id를 폴링 때 함께 보내면 따라잡았을 때
// 본문이 담긴 모양이 'updates'로 온다. 반응 · 삭제처럼 바뀐 메시지도 updates로 온다.
// 묶음(같은 사람 · 5분 안): 아바타는 묶음 끝, 이름은 방에서 묶음 처음, 시각은 묶음마다 한 번.
// 반응 · 답장은 마우스를 올리거나(키보드 포커스) 길게 누를 때만. DOM은 textContent 로만 만든다.
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
  const scroller = $('#scroll'), log = $('#log'), older = $('#older'), intro = $('#intro'), empty = $('#empty'), loadErr = $('#loadErr');
  const form = $('#compose'), text = $('#text'), hint = $('#hint'), sendBtn = $('#send'), typingHint = $('#typingHint');
  const replying = $('#replying'), replyWho = $('#replyWho'), replyText = $('#replyText');
  const helpBtn = $('#helpBtn'), helpPop = $('#helpPop');
  const BASE = 3000, MAX = 60000, GROUP_MS = 5 * 60 * 1000, TIME_GAP_MS = 60 * 60 * 1000, LONG_MS = 450;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const msgs = new Map();   // id -> 마지막으로 받은 메시지 모양
  const locked = new Set(); // 잠긴 채 들고 있는 id
  let lastId = 0, oldest = 0, since = '', delay = BASE, timer = 0, inFlight = false, loaded = false;
  let readUpto = null, seenUpto = 0, replyTo = null, openPop = null, quietUntil = 0;

  const toast = (msg, k = 'info') => (window.haedokToast ? window.haedokToast(msg, { kind: k }) : null);
  const el = (tag, cls, txt) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  };
  const btn = (cls, label, txt) => { const b = el('button', cls, txt); b.type = 'button'; if (label) b.setAttribute('aria-label', label); return b; };
  const ICONS = {
    lock: ['M8 11V8a4 4 0 0 1 8 0v3', 'rect:5,11,14,9'],
    reply: ['M10 8 5 12l5 4', 'M5 12h9a5 5 0 0 1 5 5v1'],
    smile: ['circle:12,12,9', 'M8.5 14a4 4 0 0 0 7 0', 'M9 9.5h.01', 'M15 9.5h.01'],
    book: ['M5 5a2 2 0 0 1 2-2h12v15H7a2 2 0 0 0-2 2z', 'M5 20a2 2 0 0 1 2-2h12', 'M9 7h6'],
  };
  function svg(name) {
    const NS = 'http://www.w3.org/2000/svg';
    const s = document.createElementNS(NS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('class', 'ch-svg'); s.setAttribute('aria-hidden', 'true');
    ICONS[name].forEach(d => {
      let n;
      if (d.startsWith('rect:')) { const [x, y, w, h] = d.slice(5).split(','); n = document.createElementNS(NS, 'rect'); Object.entries({ x, y, width: w, height: h, rx: 2 }).forEach(([k, v]) => n.setAttribute(k, v)); }
      else if (d.startsWith('circle:')) { const [cx, cy, r] = d.slice(7).split(','); n = document.createElementNS(NS, 'circle'); Object.entries({ cx, cy, r }).forEach(([k, v]) => n.setAttribute(k, v)); }
      else { n = document.createElementNS(NS, 'path'); n.setAttribute('d', d); }
      s.append(n);
    });
    return s;
  }
  const SPOILER = /\|\|([\s\S]+?)\|\|/g;
  const masked = s => s.replace(SPOILER, '[스포일러]');

  // ---------- 시간 · 날짜
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  const clock = d => d.toLocaleTimeString('ko-KR', { hour: 'numeric', minute: '2-digit' });
  const stamp = d => `${dayLabel(d)} ${clock(d)}`;
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
    // 가리기는 예의일 뿐 보안이 아니다 — 본문은 이미 와 있고, 누르면 걷힌다
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
    if (m.deleted) return el('div', 'ch-bubble ch-gone', '삭제된 메시지');
    if (m.sealed) {
      const s = el('div', 'ch-bubble ch-seal');
      const lk = el('span', 'ch-lock'); lk.append(svg('lock'));
      s.append(lk, el('span', '', `${m.page}쪽을 읽으면 열려요`));
      return s;
    }
    const b = el('div', 'ch-bubble');
    richText(b, m.body || '', !!m.veiled);
    if (m.kind === 'book' && m.page) b.append(el('span', 'ch-pg', `${m.page}쪽`));
    return b;
  }

  function quote(m) {
    // 답장: "OO에게 답장" 한 줄 + 옅은 작은 말풍선(잠겼으면 '잠긴 메시지'만) + 가는 연결선(CSS)
    if (!m.reply) return null;
    const box = document.createDocumentFragment();
    const who = m.reply.mine ? '나에게 답장' : `${m.reply.nickname}에게 답장`;
    box.append(el('span', 'ch-rlabel', who));
    const q = btn('ch-quote' + (m.reply.locked || m.reply.gone ? ' locked' : ''), `원문 보기: ${m.reply.text}`, m.reply.text);
    q.addEventListener('click', () => {
      const t = log.querySelector(`li[data-id="${m.reply.id}"]`);
      if (!t) return;
      centerIn(t, true);
      t.classList.add('flash'); setTimeout(() => t.classList.remove('flash'), 1200);
    });
    box.append(q);
    return box;
  }

  function cards(m) {
    // 읽기 게시물 링크(/p/숫자) — 서버가 볼 수 있는 게시물만 cards 로 준다(그 밖에는 링크 글자 그대로)
    if (!m.cards || !m.cards.length) return null;
    const frag = document.createDocumentFragment();
    m.cards.forEach(c => {
      const a = el('a', 'ch-card');
      a.href = c.href;
      const cv = el('span', 'ch-card-cover');
      if (c.cover) { const i = el('img'); i.src = c.cover; i.alt = ''; i.width = 36; i.height = 54; i.loading = 'lazy'; cv.append(i); }
      else cv.append(svg('book'));
      const t = el('span', 'ch-card-t');
      t.append(el('b', '', c.title), el('span', '', `${c.author}님의 읽기 기록`));
      a.append(cv, t);
      frag.append(a);
    });
    return frag;
  }

  function chips(m) {
    if (!m.reactions || !m.reactions.length) return null;
    const box = el('div', 'ch-reacts');
    m.reactions.forEach(r => {
      const c = btn('ch-react' + (r.mine ? ' mine' : ''), `${r.emoji} ${r.count}명${r.mine ? ' · 내 반응(누르면 빼기)' : ''}`);
      c.setAttribute('aria-pressed', String(!!r.mine));
      c.append(el('span', '', r.emoji), el('span', 'mono', String(r.count)));
      c.addEventListener('click', () => react(m, r.mine ? null : r.emoji));
      box.append(c);
    });
    return box;
  }

  function pop(m) {
    // 반응 막대 + 메뉴(답장 · 지우기 · 신고) — 길게 누르기 · ☺ 버튼 · 키보드 Enter 로 연다
    const p = el('div', 'ch-pop');
    p.setAttribute('role', 'group');
    p.setAttribute('aria-label', '반응과 메뉴');
    if (m.at) p.append(el('span', 'ch-pop-time', stamp(new Date(m.at))));
    const canTouch = !m.deleted && !m.sealed;
    if (canTouch) {
      const mine = (m.reactions || []).find(r => r.mine);
      const row = el('div', 'ch-pop-emo');
      EMOJIS.forEach(e => {
        const on = mine && mine.emoji === e;
        const x = btn('ch-emo' + (on ? ' mine' : ''), `${e} 반응${on ? ' 빼기' : ''}`, e);
        x.addEventListener('click', () => react(m, on ? null : e));
        row.append(x);
      });
      p.append(row);
    }
    const acts = el('div', 'ch-pop-acts');
    if (canTouch) { const r = btn('', null, '답장'); r.addEventListener('click', () => startReply(m)); acts.append(r); }
    if (!m.deleted && (m.mine || (kind === 'room' && owner))) {
      const d = btn('bad', null, m.mine ? '지우기' : '방장 권한으로 지우기');
      d.addEventListener('click', () => remove(m.id));
      acts.append(d);
    }
    if (!m.mine && !m.deleted) { const r = btn('', null, '신고'); r.addEventListener('click', () => report(m.id)); acts.append(r); }
    if (acts.children.length) p.append(acts);
    return p;
  }

  function tools(m) {
    const t = el('div', 'ch-tools');
    if (m.at) { const tm = el('time', '', clock(new Date(m.at))); tm.dateTime = m.at; t.append(tm); }
    if (!m.deleted && !m.sealed) {
      const r = btn('', '답장');
      r.append(svg('reply'));
      r.addEventListener('click', () => startReply(m));
      t.append(r);
    }
    const more = btn('', `${m.mine ? '내' : m.nickname + '의'} 메시지 반응 · 메뉴`);
    more.append(svg('smile'));
    more.setAttribute('aria-expanded', String(openPop === m.id));
    more.addEventListener('click', () => togglePop(m.id));
    t.append(more);
    return t;
  }

  function avatarOf(m) {
    const a = el('span', 'avatar');
    if (m.avatar) { const i = el('img'); i.src = m.avatar; i.alt = ''; i.width = 30; i.height = 30; i.loading = 'lazy'; a.append(i); } else a.textContent = (m.nickname || '?').slice(0, 1);
    return a;
  }

  function longPress(target, m) {
    let t = 0, sx = 0, sy = 0;
    target.addEventListener('pointerdown', e => {
      if (e.pointerType === 'mouse') return;
      sx = e.clientX; sy = e.clientY;
      t = setTimeout(() => { t = 0; quietUntil = Date.now() + 700; togglePop(m.id, true); if (navigator.vibrate) navigator.vibrate(8); }, LONG_MS);
    });
    const cancel = e => { if (t && (e.type !== 'pointermove' || Math.hypot(e.clientX - sx, e.clientY - sy) > 10)) { clearTimeout(t); t = 0; } };
    ['pointerup', 'pointercancel', 'pointerleave', 'pointermove'].forEach(ev => target.addEventListener(ev, cancel));
    target.addEventListener('contextmenu', e => { if (e.pointerType !== 'mouse') e.preventDefault(); });
    // 길게 누르기만 있는 동작이 되지 않게: 터치에서는 한 번 눌러도 반응 · 메뉴가 열린다
    target.addEventListener('click', e => {
      if (Date.now() < quietUntil) return;  // 길게 누른 뒤 따라오는 click 은 무시(그 사이 다시 그려졌을 수 있다)
      if (e.pointerType && e.pointerType !== 'mouse' && !e.target.closest('.ch-spoiler')) togglePop(m.id);
    });
  }

  function node(m, first, last, seen) {
    const li = el('li', 'ch-msg' + (m.mine ? ' me' : '') + (first ? ' first' : '') + (last ? ' last' : ''));
    li.dataset.id = m.id;
    if (m.at) li.title = stamp(new Date(m.at));
    const stack = el('div', 'ch-stack');
    if (first && !m.mine && kind === 'room') stack.append(el('span', 'ch-name', m.nickname));
    const q = quote(m);
    if (q) stack.append(q);
    const b = bubble(m);
    b.tabIndex = 0;
    b.setAttribute('aria-label', `${m.mine ? '나' : m.nickname}, ${m.at ? stamp(new Date(m.at)) : ''}. Enter로 반응·메뉴`);
    b.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target === b) { e.preventDefault(); togglePop(m.id, true); } });
    longPress(b, m);
    const line = el('div', 'ch-bline');
    line.append(b);
    if (!m.mine && last) { const a = avatarOf(m); a.classList.add('ch-ava'); line.append(a); }  // 아바타는 묶음 끝 말풍선 옆
    stack.append(line);
    const c = chips(m);
    if (c) stack.append(c);
    const cd = cards(m);
    if (cd) stack.append(cd);
    if (openPop === m.id) stack.append(pop(m));
    if (seen) stack.append(el('span', 'ch-seen', '읽음'));
    li.append(stack);
    li.append(tools(m));
    return li;
  }

  // ---------- 전체 그리기(묶음 · 날짜 · 안 읽음 · 읽음)
  function draw() {
    const list = [...msgs.values()].sort((a, b) => a.id - b.id);
    // '읽음'은 내 마지막 메시지 아래에만(개인 채팅) — 친구가 거기까지 봤을 때
    let seenId = 0;
    if (kind === 'dm' && seenUpto) {
      const mine = list.filter(m => m.mine);
      const lastMine = mine[mine.length - 1];
      if (lastMine && lastMine.id <= seenUpto) seenId = lastMine.id;
    }
    const focusedLi = document.activeElement && document.activeElement.closest ? document.activeElement.closest('li[data-id]') : null;
    const focusedId = focusedLi ? focusedLi.dataset.id : null;
    const frag = document.createDocumentFragment();
    let dividerAt = null;
    if (readUpto !== null) { const u = list.find(m => m.id > readUpto && !m.mine); if (u) dividerAt = u.id; }
    // 시각은 Instagram처럼: 날이 바뀌거나 한 시간 넘게 비면 가운데 작은 시각 한 줄. 묶음마다 시각을 붙이지 않는다
    const gap = (a, b) => !a || !sameDay(new Date(a.at), new Date(b.at)) || (new Date(b.at) - new Date(a.at)) > TIME_GAP_MS;
    const brk = (a, b) => !a || !b || a.user_id !== b.user_id || (new Date(b.at) - new Date(a.at)) > GROUP_MS || gap(a, b) || b.id === dividerAt;
    list.forEach((m, i) => {
      const prev = list[i - 1], next = list[i + 1];
      if (gap(prev, m)) {
        const t = el('li', 'ch-time', stamp(new Date(m.at || Date.now())));
        t.setAttribute('role', 'separator');
        frag.append(t);
      }
      if (m.id === dividerAt) {
        const u = el('li', 'ch-new');
        u.setAttribute('role', 'separator');
        u.append(el('span', '', '여기부터 안 읽음'));
        frag.append(u);
      }
      frag.append(node(m, brk(prev, m), brk(m, next), m.id === seenId));
      if (m.sealed) locked.add(m.id); else locked.delete(m.id);
    });
    log.replaceChildren(frag);
    empty.hidden = !loaded || list.length > 0;
    if (focusedId) { const f = log.querySelector(`li[data-id="${focusedId}"] .ch-bubble`); if (f) f.focus({ preventScroll: true }); }
  }

  function togglePop(id, keyboard) {
    openPop = openPop === id ? null : id;
    draw();
    if (openPop !== null && keyboard) { const first = log.querySelector(`li[data-id="${id}"] .ch-pop button`); if (first) first.focus(); }
  }

  // scrollIntoView 는 overflow:hidden 인 바깥 칸까지 밀어 올려 머리가 잘린다 — 메시지 칸만 직접 스크롤한다
  function centerIn(node, smooth) {
    const top = node.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
    scroller.scrollTo({ top: Math.max(0, top - scroller.clientHeight / 2), behavior: smooth && !reduce ? 'smooth' : 'auto' });
  }
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  const toBottom = () => { scroller.scrollTop = scroller.scrollHeight; };

  // ---------- 서버
  async function get(params) {
    const u = new URL(api, location.origin);
    if (kind === 'room') u.searchParams.set('channel', channel);
    Object.entries(params).forEach(([k, v]) => { if (v !== '' && v != null) u.searchParams.set(k, v); });
    const r = await fetch(u, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }
  async function call(url, method, body) {
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
    if (cnt && d.other_unread != null) cnt.hidden = !d.other_unread;  // 다른 채널의 새 글은 점 하나로
    const notice = $('#notice'), nt = $('#noticeText');
    if (notice && nt && d.notice !== undefined) { nt.textContent = d.notice || ''; notice.hidden = !d.notice && !owner; }
  }

  async function first() {
    loadErr.hidden = true;
    try {
      const d = await get({});
      d.items.forEach(m => msgs.set(m.id, m));
      lastId = d.last_id; since = d.now; readUpto = d.read_upto; loaded = true;
      oldest = d.items.length ? d.items[0].id : 0;
      older.hidden = !d.has_more;
      intro.hidden = d.has_more;  // 대화의 맨 처음이 보일 때만 소개
      side(d);
      draw();
      log.setAttribute('aria-busy', 'false');
      const divider = log.querySelector('.ch-new');
      if (divider) centerIn(divider); else toBottom();
      schedule(BASE);
    } catch (e) {
      log.replaceChildren();
      loadErr.hidden = false;
    }
  }

  async function loadOlder() {
    older.disabled = true;
    const h = scroller.scrollHeight;
    try {
      const d = await get({ before: oldest });
      d.items.forEach(m => msgs.set(m.id, m));
      if (d.items.length) oldest = d.items[0].id;
      older.hidden = !d.has_more;
      intro.hidden = d.has_more;
      draw();
      scroller.scrollTop += scroller.scrollHeight - h;  // 보던 자리 유지
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
    if (document.hidden) { clearTimeout(timer); timer = 0; } else if (loaded) { delay = BASE; schedule(0); }
  });

  // ---------- 동작
  function startReply(m) {
    replyTo = m.id;
    replyWho.textContent = m.mine ? '나' : m.nickname;
    const t = masked(m.body || '');
    replyText.textContent = t.length > 60 ? t.slice(0, 60) + '…' : t;
    const stick = nearBottom();
    replying.hidden = false;
    openPop = null; draw();
    if (stick) toBottom();  // 답장 줄이 생겨도 마지막 메시지가 가려지지 않게
    text.focus();
  }
  function cancelReply() { replyTo = null; replying.hidden = true; }

  async function react(m, emoji) {
    const url = `/api/v1/chat/messages/${m.id}/reaction`;
    const { ok, d } = emoji ? await call(url, 'PUT', { emoji }) : await call(url, 'DELETE');
    if (!ok) { toast(d.detail || '반응을 남기지 못했어요', 'error'); return; }
    msgs.set(d.id, d);
    openPop = null;
    draw();
  }

  async function sendMsg(ev) {
    ev.preventDefault();
    const body = text.value.trim();
    if (!body) { text.focus(); return; }
    sendBtn.disabled = true;
    try {
      const payload = { text: body, reply_to: replyTo };
      if (kind === 'room') payload.channel = channel;  // 채널만 알린다 — 쪽 도장은 서버가 찍는다
      const { ok, d } = await call(api, 'POST', payload);
      if (!ok) { hint.textContent = d.detail || '보내지 못했어요. 잠시 뒤 다시 보내 주세요'; return; }
      msgs.set(d.id, d);
      text.value = ''; hint.textContent = ''; cancelReply(); grow();
      draw(); toBottom();
    } catch (e) {
      hint.textContent = '보내지 못했어요. 연결을 확인해 주세요';
    } finally {
      sendBtn.disabled = !text.value.trim();
      text.focus();
    }
  }

  async function remove(id) {
    if (!window.confirm('이 메시지를 지울까요? 모두에게 ‘삭제된 메시지’로 보여요.')) return;
    const { ok } = await call(`/api/v1/chat/messages/${id}`, 'DELETE');
    if (ok) { const m = msgs.get(id); if (m) msgs.set(id, Object.assign({}, m, { deleted: true, reactions: [] })); openPop = null; draw(); }
    else toast('지우지 못했어요', 'error');
  }

  async function report(id) {
    const reason = window.prompt('신고할까요? 이유를 적어 주면 관리자가 봐요(비워 둬도 돼요).', '');
    if (reason === null) return;
    const { ok, d } = await call(`/api/v1/chat/messages/${id}/report`, 'POST', { reason });
    toast(ok ? '신고했어요. 관리자가 확인할게요' : (d.detail || '신고하지 못했어요'), ok ? 'success' : 'error');
    openPop = null; draw();
  }

  // 입력칸: 다섯 줄까지 늘어나고, 비어 있으면 보내기가 꺼진다. ||나 @를 칠 때만 짧은 힌트
  function grow() {
    const stick = nearBottom();
    text.style.height = 'auto';
    text.style.height = Math.min(text.scrollHeight, 132) + 'px';
    const v = text.value;
    sendBtn.disabled = !v.trim();
    const left = 500 - v.length;
    let tip = '';
    if (left <= 50) tip = `${left}자 남았어요`;
    else if (/\|\|[^|]*$/.test(v) || /\|\|$/.test(v)) tip = '||로 감싼 부분은 상대가 눌러야 보여요';
    else if (/(^|\s)@\S*$/.test(v)) tip = names.length ? `부를 수 있어요: ${names.slice(0, 6).map(n => '@' + n).join(' ')}` : '';
    typingHint.textContent = tip;
    typingHint.hidden = !tip;
    if (stick) toBottom();
  }

  // 머리의 ⋯ 메뉴(멤버 · 대결 보기 · 프로필 · 쓰는 법)
  const moreBtn = $('#moreBtn'), moreMenu = $('#moreMenu');
  const setMenu = open => { moreMenu.hidden = !open; moreBtn.setAttribute('aria-expanded', String(open)); };
  moreBtn.addEventListener('click', () => {
    setMenu(moreMenu.hidden);
    if (!moreMenu.hidden) { const f = moreMenu.querySelector('a,button'); if (f) f.focus(); }
  });
  moreMenu.addEventListener('click', e => { if (e.target.closest('a,button')) setMenu(false); });

  // 입력칸 왼쪽 이모지: 앱의 반응 이모지 세트를 커서 자리에 넣는다
  const emojiBtn = $('#emojiBtn'), emojiPop = $('#emojiPop');
  EMOJIS.forEach(e => {
    const b = btn('ch-emo', `${e} 넣기`, e);
    b.addEventListener('click', () => {
      const a = text.selectionStart ?? text.value.length, z = text.selectionEnd ?? a;
      text.value = text.value.slice(0, a) + e + text.value.slice(z);
      text.focus();
      text.setSelectionRange(a + e.length, a + e.length);
      grow();
    });
    emojiPop.append(b);
  });
  const setEmoji = open => { emojiPop.hidden = !open; emojiBtn.setAttribute('aria-expanded', String(open)); };
  emojiBtn.addEventListener('click', () => setEmoji(emojiPop.hidden));

  helpBtn.addEventListener('click', () => {
    const open = helpPop.hidden;
    helpPop.hidden = !open;
    helpBtn.setAttribute('aria-expanded', String(open));
  });

  // 공지(방장)
  const nf = document.getElementById('noticeForm'), ne = document.getElementById('noticeEdit');
  if (nf && ne && roomId) {
    ne.addEventListener('click', () => {
      nf.hidden = !nf.hidden;
      ne.setAttribute('aria-expanded', String(!nf.hidden));
      if (!nf.hidden) document.getElementById('noticeIn').focus();
    });
    nf.addEventListener('submit', async ev => {
      ev.preventDefault();
      const v = document.getElementById('noticeIn').value;
      const { ok, d } = await call(`/api/v1/chat/room/${roomId}/notice`, 'PUT', { text: v });
      if (!ok) { toast(d.detail || '공지를 저장하지 못했어요', 'error'); return; }
      side({ notice: d.notice });
      ne.textContent = d.notice ? '바꾸기' : '공지 올리기';
      nf.hidden = true; ne.setAttribute('aria-expanded', 'false');
      toast(d.notice ? '공지를 올렸어요' : '공지를 내렸어요', 'success');
    });
  }

  // 멤버: 데스크톱 오른쪽 서랍 · 휴대폰 아래 시트
  const mb = document.getElementById('membersBtn'), panel = document.getElementById('members'), mc = document.getElementById('membersClose');
  const setMembers = open => {
    if (!panel) return;
    panel.hidden = !open;
    if (mb) mb.setAttribute('aria-expanded', String(open));
    if (open && mc) mc.focus(); else if (!open) moreBtn.focus();
  };
  if (mb && panel) mb.addEventListener('click', () => setMembers(panel.hidden));
  if (mc) mc.addEventListener('click', () => setMembers(false));

  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (!moreMenu.hidden) { setMenu(false); moreBtn.focus(); }
    else if (!emojiPop.hidden) { setEmoji(false); emojiBtn.focus(); }
    else if (panel && !panel.hidden) setMembers(false);
    else if (!helpPop.hidden) { helpPop.hidden = true; helpBtn.setAttribute('aria-expanded', 'false'); }
    else if (openPop !== null) { const id = openPop; openPop = null; draw(); const b = log.querySelector(`li[data-id="${id}"] .ch-bubble`); if (b) b.focus(); }
    else if (replyTo) cancelReply();
  });
  document.addEventListener('pointerdown', e => {
    if (openPop !== null && !e.target.closest('.ch-pop') && !e.target.closest('.ch-tools') && !e.target.closest('.ch-bubble')) { openPop = null; draw(); }
    if (!moreMenu.hidden && !e.target.closest('.ch-more-wrap')) setMenu(false);
    if (!emojiPop.hidden && !e.target.closest('#emojiPop') && !e.target.closest('#emojiBtn')) setEmoji(false);
    if (!helpPop.hidden && !e.target.closest('#helpPop') && e.target !== helpBtn) { helpPop.hidden = true; helpBtn.setAttribute('aria-expanded', 'false'); }
  });

  // 휴대폰 화면 키보드: 보이는 높이에 앱 셸을 맞춰 입력칸이 키보드 위에 남게
  const app = root.closest('.ch-app');
  if (window.visualViewport && app) {
    const fit = () => { app.style.height = matchMedia('(max-width: 767px)').matches ? `${window.visualViewport.height}px` : ''; };
    window.visualViewport.addEventListener('resize', fit);
    fit();
  }

  $('#replyCancel').addEventListener('click', cancelReply);
  $('#retry').addEventListener('click', first);
  text.addEventListener('input', grow);
  text.addEventListener('keydown', e => {
    // 한글 조합 중(isComposing) Enter는 글자 확정이라 보내지 않는다. Shift+Enter는 줄바꿈
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !matchMedia('(pointer: coarse)').matches) {
      e.preventDefault();
      if (text.value.trim()) form.requestSubmit();
    }
  });
  form.addEventListener('submit', sendMsg);
  older.addEventListener('click', loadOlder);
  first();
})();
