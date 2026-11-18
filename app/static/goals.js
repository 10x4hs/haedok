// 하루 목표 · 잔디 (feature goals) — /me 의 목표 칸과 홈의 칩을 /api/v1/me/goal 로 채운다.
// DOM 은 textContent 로만 만든다(사용자 글은 없지만 규칙대로).
(() => {
  if (window.__goals) return;  // 홈·내 정보에서 한 번만
  window.__goals = true;
  const csrf = document.querySelector('meta[name="csrf"]')?.content;
  const section = document.querySelector('[data-goal]');
  const chip = document.querySelector('[data-goal-chip]');
  if (!section && !chip) return;

  const WIDE = 520;  // 이보다 넓으면 26주, 좁으면(폰) 17주
  const weeksFor = () => (section && section.clientWidth >= WIDE ? 26 : 17);
  const $ = (sel) => section.querySelector(sel);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  async function load(weeks) {
    const r = await fetch(`/api/v1/me/goal?weeks=${weeks}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }

  function renderChip(d) {
    if (!chip) return;
    chip.querySelector('[data-goal-chip-text]').textContent = `오늘 ${d.today.pages}/${d.goal.pages}쪽`;
    chip.classList.toggle('met', d.today.pages >= d.goal.pages);
    chip.setAttribute('aria-label', `오늘 목표 ${d.goal.pages}쪽 중 ${d.today.pages}쪽 읽었어요. 내 정보에서 보기`);
  }

  function streakLine(d) {
    const s = d.streak;
    if (d.today.pages >= d.goal.pages * 2) return s.days > 1 ? `${s.days}일째 이어 읽는 중 · 오늘은 목표의 두 배예요` : '오늘은 목표의 두 배를 읽었어요';
    if (s.rested_yesterday && s.repair.open) return `어제는 쉬었어요 · 오늘 ${s.repair.pages}쪽 읽으면 연속이 이어져요`;
    if (s.rested_yesterday) return '어제는 쉬었어요 · 오늘 한 쪽부터 다시 시작해요';
    if (s.days > 0 && d.today.pages >= d.goal.pages) return `${s.days}일째 이어 읽는 중 · 오늘 목표를 채웠어요`;
    if (s.days > 0) return `${s.days}일째 이어 읽는 중이에요`;
    return '오늘 한 쪽부터 시작해 봐요';
  }

  function renderGrass(d) {
    const grid = $('[data-goal-grass]'), months = $('[data-goal-months]');
    const weeks = d.heatmap.weeks, days = d.heatmap.days;
    grid.style.setProperty('--weeks', weeks);
    months.style.setProperty('--weeks', weeks);
    grid.replaceChildren();
    months.replaceChildren();
    const tip = $('[data-goal-tip]');
    const cells = [];
    let lastMonth = null, lastLabel = -9;
    days.forEach((c, i) => {
      const b = el('button', c.kind === 'freeze' ? 'fz' : c.kind === 'repair' ? 'rp' : `l${c.level}`);
      b.type = 'button';
      b.tabIndex = -1;
      b.setAttribute('aria-label', c.label);
      b.dataset.label = c.label;
      if (c.date === d.today.date) b.setAttribute('aria-current', 'date');
      grid.appendChild(b);
      cells.push(b);
      if (i % 7 === 0) {  // 주(열)마다 달이 바뀌면 위에 달 이름 — 이름끼리 3열 안에 붙으면 앞엣것은 비운다
        const col = i / 7, m = Number(c.date.slice(5, 7));
        const span = el('span', '', m !== lastMonth ? `${m}월` : '');
        if (span.textContent && col - lastLabel < 3) months.children[lastLabel].textContent = '';
        if (span.textContent) lastLabel = col;
        months.appendChild(span);
        lastMonth = m;
      }
    });
    // 이번 주 남은 칸은 자리만 채운다(앞으로 올 날)
    for (let i = days.length; i < weeks * 7; i++) { const p = el('span', 'pad'); p.setAttribute('aria-hidden', 'true'); grid.appendChild(p); }
    const read = days.filter(c => c.pages > 0).length;
    $('[data-goal-grass-label]').textContent = `최근 ${weeks}주 읽은 날 기록 · ${read}일 읽었어요. 화살표 키로 날짜를 옮길 수 있어요`;
    // 한 칸만 탭 순서에 넣고 화살표로 옮긴다(로빙 tabindex)
    let at = cells.length - 1;
    const show = (i, focus) => {
      at = Math.max(0, Math.min(cells.length - 1, i));
      cells.forEach((b, j) => { b.tabIndex = j === at ? 0 : -1; });
      tip.textContent = cells[at].dataset.label;
      if (focus) cells[at].focus();
    };
    cells.forEach((b, i) => b.addEventListener('click', () => show(i, false)));
    grid.addEventListener('keydown', (e) => {
      const step = { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1, Home: -at, End: cells.length - 1 - at }[e.key];
      if (step === undefined) return;
      e.preventDefault();
      show(at + step, true);
    });
    if (cells.length) cells[at].tabIndex = 0;
  }

  function renderTiers(d) {
    // 버튼은 한 번만 만들고 이후엔 aria-pressed 만 바꾼다 — motion.js 가 .seg 에 넣은 미끄럼 배경(.pill)을 지키려고
    const box = $('[data-goal-tiers]');
    d.goal.tiers.forEach((t) => {
      let b = box.querySelector(`button[data-pages="${t.pages}"]`);
      if (!b) {
        b = el('button');
        b.type = 'button';
        b.dataset.pages = t.pages;
        b.append(`${t.pages}쪽`, el('span', '', t.name));
        b.addEventListener('click', () => save(t.pages));
        box.appendChild(b);
      }
      b.setAttribute('aria-label', `${t.name} ${t.pages}쪽, 약 ${t.minutes}분`);
      b.setAttribute('aria-pressed', String(t.pages === d.goal.pages));
    });
  }

  function render(d) {
    renderChip(d);
    if (!section) return;
    const pct = Math.min(100, Math.round((d.today.pages / d.goal.pages) * 100));
    $('[data-goal-ring]').style.strokeDasharray = `${pct} 100`;
    $('[data-goal-ring-text]').textContent = d.today.pages >= d.goal.pages ? '완료' : `${pct}%`;
    $('[data-goal-today]').textContent = `${d.today.pages}/${d.goal.pages}쪽`;
    const min = $('[data-goal-minutes]');
    min.textContent = `· 약 ${d.goal.minutes}분 예상`;
    min.title = '내 쪽당 읽기 시간으로 어림한 값이에요';
    $('[data-goal-streak]').textContent = streakLine(d);
    $('[data-goal-week]').textContent = `이번 주 ${d.week.read_days}/${d.week.total}일`;
    $('[data-goal-freezes]').textContent = `보호권 ${d.streak.freezes}/${d.streak.freeze_cap}`;
    renderGrass(d);
    renderTiers(d);
  }

  async function save(pages) {
    const tip = $('[data-goal-tip]');
    try {
      const r = await fetch(`/api/v1/me/goal?weeks=${weeksFor()}`, {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf || '' },
        body: JSON.stringify({ pages }),
      });
      if (!r.ok) throw new Error(String(r.status));
      render(await r.json());
      tip.textContent = `하루 목표를 ${pages}쪽으로 바꿨어요`;
    } catch {
      tip.textContent = '목표를 바꾸지 못했어요. 잠시 뒤에 다시 해 주세요';
    }
  }

  if (section) {
    const edit = $('[data-goal-edit]'), tiers = $('#goal-tiers');
    edit.addEventListener('click', () => {
      const open = edit.getAttribute('aria-expanded') !== 'true';
      edit.setAttribute('aria-expanded', String(open));
      tiers.hidden = !open;
    });
  }

  load(section ? weeksFor() : 1).then(render).catch(() => {
    if (section) $('[data-goal-streak]').textContent = '목표를 불러오지 못했어요. 새로고침해 주세요';
  });
})();
