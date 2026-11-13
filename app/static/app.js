// 앱 공통: 토스트, 폼 보내는 중 표시, 오른쪽 위 메뉴, 화면 꾸미기 시트
(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // --- 토스트: haedokToast('저장했어요', { kind: 'success' | 'error' | 'info', ms }) ---
  // 자리(#toasts)는 _ui.html의 toast_region()이 그리거나, 없으면 여기서 만든다. aria-live라 화면 낭독기도 읽는다.
  const region = () => {
    let r = document.getElementById('toasts');
    if (!r) {
      r = document.createElement('div'); r.id = 'toasts'; r.className = 'toasts';
      r.setAttribute('role', 'status'); r.setAttribute('aria-live', 'polite'); r.setAttribute('aria-relevant', 'additions');
      document.body.appendChild(r);
    }
    return r;
  };
  window.haedokToast = (text, { kind = 'info', ms = 3200 } = {}) => {
    const r = region();
    [...r.children].slice(2).forEach(x => x.remove());   // 세 개까지만 쌓인다
    const t = document.createElement('div');
    t.className = `toast ${kind}`; t.textContent = text;
    r.prepend(t);
    const go = () => { if (!t.isConnected) return; t.classList.add('out'); setTimeout(() => t.remove(), reduced ? 0 : 260); };
    t.addEventListener('click', go);
    setTimeout(go, kind === 'error' ? Math.max(ms, 5000) : ms);
    return t;
  };

  // --- 폼 보내는 중: 버튼 잠그고 스피너, 두 번 눌러도 한 번만 간다 ---
  document.addEventListener('submit', e => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || e.defaultPrevented) return;
    if (form.classList.contains('sending')) { e.preventDefault(); return; }
    form.classList.add('sending'); form.setAttribute('aria-busy', 'true');
    const btn = e.submitter || form.querySelector('button:not([type=button]), [type=submit]');
    if (btn) {
      btn.setAttribute('aria-busy', 'true');
      if (btn.dataset.sending) { btn.dataset.label = btn.textContent; btn.textContent = btn.dataset.sending; }
      setTimeout(() => { btn.disabled = true; }, 0);   // 값이 폼 데이터에 실린 뒤에 잠근다
    }
    // 뒤로 가기(bfcache)로 돌아오면 풀어 준다
    addEventListener('pageshow', () => { form.classList.remove('sending'); form.removeAttribute('aria-busy'); if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); if (btn.dataset.label) btn.textContent = btn.dataset.label; } }, { once: true });
  });

  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const root = document.documentElement;
  const csrf = document.querySelector('meta[name="csrf"]')?.content;

  // --- 메뉴 ---
  const menuBtn = $('#menuBtn'), menu = $('#menu');
  const setMenu = open => { menu.hidden = !open; menuBtn.setAttribute('aria-expanded', open); menuBtn.classList.toggle('open', open); };
  menuBtn?.addEventListener('click', () => setMenu(menu.hidden));
  menu?.addEventListener('keydown', e => { if (e.key === 'Escape') { setMenu(false); menuBtn.focus(); } });
  menu?.addEventListener('click', e => { if (e.target === menu || e.target.closest('a,button')) setMenu(false); });
  addEventListener('keydown', e => { if (e.key === 'Escape') { setMenu(false); closeLook(true); } });

  // --- 화면 꾸미기 ---
  const sheet = $('#look');
  if (!sheet) return;
  const toneOf = bg => $(`[data-bgpick="${bg}"]`)?.dataset.tone || 'auto';
  const saved = { mode: root.dataset.theme || 'auto', bg: $('#fx').dataset.bg, accent: root.dataset.accent, hex: getComputedStyle(root).getPropertyValue('--accent').trim() };
  let pick = { ...saved };

  const apply = p => {
    if (p.mode === 'auto') delete root.dataset.theme; else root.dataset.theme = p.mode;
    $('#fx').dataset.bg = p.bg; $('#lookPreviewBg').dataset.bg = p.bg;
    root.dataset.bgTone = toneOf(p.bg);   // 글자색이 배경 밝기를 따라가게(app.css 끝)
    root.dataset.accent = p.accent; root.style.setProperty('--accent', p.hex);
    $$('#lookMode button').forEach(b => b.setAttribute('aria-pressed', b.dataset.mode === p.mode));
    $$('[data-bgpick]').forEach(b => b.setAttribute('aria-pressed', b.dataset.bgpick === p.bg));
    $$('#lookAccent button').forEach(b => b.setAttribute('aria-pressed', b.dataset.accent === p.accent));
  };
  let opener = null;
  function openLook() { opener = document.activeElement; pick = { ...saved }; apply(pick); sheet.hidden = false; document.body.classList.add('locked'); (sheet.querySelector('button, [href]') || sheet).focus(); }
  function closeLook(revert) { if (sheet.hidden) return; if (revert) apply(saved); sheet.hidden = true; document.body.classList.remove('locked'); opener?.focus?.(); }
  window.closeLook = closeLook;

  $$('[data-open-look]').forEach(b => b.addEventListener('click', openLook));
  $$('[data-close-look]').forEach(b => b.addEventListener('click', () => closeLook(true)));
  sheet.addEventListener('click', e => { if (e.target === sheet) closeLook(true); });
  $$('#lookMode button').forEach(b => b.onclick = () => apply(pick = { ...pick, mode: b.dataset.mode }));
  $$('[data-bgpick]').forEach(b => b.onclick = () => apply(pick = { ...pick, bg: b.dataset.bgpick }));
  $$('#lookAccent button').forEach(b => b.onclick = () => apply(pick = { ...pick, accent: b.dataset.accent, hex: b.dataset.hex }));
  $$('#bgTabs button').forEach(t => t.onclick = () => {
    $$('#bgTabs button').forEach(x => x.setAttribute('aria-pressed', x === t));
    $$('[data-group-panel]').forEach(p => p.hidden = p.dataset.groupPanel !== t.dataset.group);
  });
  $('#lookReset').onclick = () => {
    const d = $('#lookAccent button[data-accent="pink"]');
    apply(pick = { mode: 'auto', bg: 'plain', accent: 'pink', hex: d.dataset.hex });
  };
  $('#lookDone').onclick = async () => {
    const btn = $('#lookDone'); btn.disabled = true; btn.textContent = '저장 중…';
    try {
      const r = await fetch('/api/v1/me/settings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ mode: pick.mode, bg: pick.bg, accent: pick.accent }),
      });
      if (!r.ok) throw new Error(r.status);
      Object.assign(saved, pick);
      closeLook(false);
    } catch (e) { btn.textContent = '다시 시도'; btn.disabled = false; window.haedokToast('저장하지 못했어요. 연결을 확인해 주세요', { kind: 'error' }); return; }
    btn.textContent = '완료'; btn.disabled = false;
    window.haedokToast('화면 설정을 저장했어요', { kind: 'success' });
  };
  if (location.hash === '#look') openLook();
})();
