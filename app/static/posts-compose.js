/* 새 게시물 — 인스타그램의 세 단계(① 사진 고르기 · 자르기 → ② 책과 글 → ③ 공유).
 *
 * 사진: <input type=file multiple> 또는 끌어다 놓기 → 브라우저가 캔버스에 그려 고른 비율(1:1 · 4:5, 게시물 전체)로 자르고
 * 1080px 폭으로 줄여 JPEG(~0.82, 한 장 ≤ 350 KB 목표)로 다시 만든다. 캔버스를 거치면 EXIF · GPS 는 남지 않는다
 * (서버도 한 번 더 걷는다). 끌어서 위치를 옮기고, 막대로 확대하고, ‹ › 로 순서를 바꾸고, ✕ 로 뺀다.
 *
 * 보내기: 한 번의 multipart 요청(XHR, 올라가는 정도 표시). 서버 한도가 한 장 400 KB × 5장 = 2 MB 라서
 * 글까지 합쳐도 Vercel 의 4.5 MB 요청 한도 아래다 — 그래서 초안에 한 장씩 올리는 길은 두지 않았다.
 * 혹시 3.5 MB 를 넘으면(그럴 수 없지만) 품질을 낮춰 다시 만든다.
 * JS 가 없으면 이 파일은 아무것도 하지 않고, 세 단계가 한 화면에 보이는 보통 폼으로 동작한다. */
(() => {
  const root = document.querySelector('[data-compose-root]');
  const form = document.querySelector('form[data-compose]');
  if (!root || !form || !window.HTMLCanvasElement) return;
  const P = window.haedokPosts || { toast: () => {}, reduced: false };
  const $ = (s, el = document) => el.querySelector(s);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  const MAX = Number(form.dataset.maxPhotos) || 5;
  const LEFT = Number(form.dataset.photosLeft || MAX);
  const TARGET_BYTES = 350_000, HARD_BYTES = Number(form.dataset.maxBytes) || 400_000, TOTAL_BYTES = 3_500_000;
  const WIDTH = 1080;
  const fileInput = $('[data-files]', form);
  fileInput.removeAttribute('name'); // 사진은 캔버스로 다시 만든 것만 보낸다
  const drop = $('[data-drop]', form), crop = $('[data-crop]', form), thumbs = $('[data-thumbs]', form);
  const frame = $('[data-frame]', form), canvas = $('[data-crop-canvas]', form), zoom = $('[data-zoom]', form);
  const errBox = document.querySelector('[data-form-err]');
  const photos = []; // {src, w, h, zoom, cx, cy, url}
  let cur = 0, noPhoto = false;

  const aspect = () => { const v = (form.querySelector('input[name="aspect"]:checked') || {}).value || '4:5'; return v === '1:1' ? 1 : 4 / 5; };
  const showErr = msg => { errBox.textContent = msg; errBox.hidden = !msg; if (msg) errBox.scrollIntoView({ block: 'nearest' }); };

  // ---------- 단계 넘기기(위 막대: 뒤로 · 단계 이름 · 다음/공유하기)
  const topbar = $('[data-topbar]'), stepper = $('[data-stepper]');
  const titles = { 1: '사진 고르기', 2: '책과 글', 3: '공유' };
  const steps = Array.from(form.querySelectorAll('[data-step]'));
  // 책 찾기: JS 가 없을 때 쓰는 GET 폼은 숨기고, ② 단계 안에 같은 모양의 칸을 만든다(폼 안에 폼을 두지 않는다)
  const noJsSearch = document.querySelector('form[data-draft-form]');
  const slot = $('[data-search-slot]', form);
  const search = el('div', 'ps-search');
  search.setAttribute('role', 'search');
  if (noJsSearch && slot) {
    noJsSearch.hidden = true;
    const old = noJsSearch.querySelector('[name="q"]');
    const lab = el('label', 'ps-sub', '다른 책 찾기');
    lab.htmlFor = 'psQ2';
    const row = el('div', 'ps-search-row');
    const q = el('input', 'ps-input');
    Object.assign(q, { id: 'psQ2', type: 'search', maxLength: 100, placeholder: '제목이나 지은이', autocomplete: 'off', value: old ? old.value : '' });
    q.setAttribute('enterkeyhint', 'search');
    const go = noJsSearch.querySelector('button').cloneNode(true);
    go.type = 'button';
    row.append(q, go);
    search.append(lab, row);
    slot.replaceWith(search);
    q.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); findBooks(); } });
    go.addEventListener('click', () => findBooks());
  }
  root.classList.add('js');
  topbar.hidden = false;
  stepper.hidden = false;
  let step = 1;
  function goto(n) {
    step = n;
    root.dataset.at = String(n);
    steps.forEach(s => { s.hidden = Number(s.dataset.step) !== n; });
    $('[data-step-title]').textContent = titles[n];
    const next = $('[data-next]');
    next.textContent = n === 3 ? '공유하기' : '다음';
    Array.from(stepper.children).forEach((li, i) => li.classList.toggle('on', i < n));
    window.scrollTo({ top: 0, behavior: 'auto' });
    if (n === 1 && photos.length) draw();
  }
  $('[data-back]').addEventListener('click', () => {
    if (step > 1) goto(step - 1);
    else if (history.length > 1) history.back();
    else location.href = '/profile';
  });
  $('[data-next]').addEventListener('click', () => {
    if (step === 1) { noPhoto = !photos.length; goto(2); }
    else if (step === 2) {
      if (!form.querySelector('input[name="book"]:checked')) { showErr('올릴 책을 골라 주세요'); return; }
      showErr(''); goto(3);
    } else submit();
  });
  $('[data-no-photo]', form).addEventListener('click', () => {
    photos.splice(0); renderThumbs(); noPhoto = true; preview(); goto(2);
  });

  // ---------- 사진 받기
  async function decode(file) {
    if (window.createImageBitmap) {
      try { return await createImageBitmap(file, { imageOrientation: 'from-image' }); } catch (_) { /* 아래 길로 */ }
    }
    return new Promise((ok, bad) => {
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = () => { ok(img); URL.revokeObjectURL(url); };
      img.onerror = () => { bad(new Error('decode')); URL.revokeObjectURL(url); };
      img.src = url;
    });
  }
  async function add(files) {
    const list = Array.from(files).filter(f => f.type.startsWith('image/') || /\.(heic|heif|jpe?g|png|webp)$/i.test(f.name));
    const room = Math.min(MAX, LEFT) - photos.length;
    if (room <= 0) { P.toast(LEFT < MAX ? `오늘은 ${LEFT}장까지 더 올릴 수 있어요` : `사진은 ${MAX}장까지예요`); return; }
    if (list.length > room) P.toast(`사진은 ${Math.min(MAX, LEFT)}장까지 — 앞의 ${room}장만 넣었어요`);
    for (const f of list.slice(0, room)) {
      try {
        const src = await decode(f);
        photos.push({ src, w: src.width || src.naturalWidth, h: src.height || src.naturalHeight, zoom: 1, cx: 0.5, cy: 0.5 });
      } catch (_) {
        P.toast(`‘${f.name}’은 열 수 없어요. 다른 사진으로 해 주세요`);
      }
    }
    if (photos.length) { cur = photos.length - 1; noPhoto = false; }
    renderThumbs(); draw(); preview();
  }
  fileInput.addEventListener('change', () => { add(fileInput.files); fileInput.value = ''; });
  // 끌어다 놓기(넓은 화면)
  ['dragenter', 'dragover'].forEach(t => root.addEventListener(t, e => { if (e.dataTransfer && Array.from(e.dataTransfer.types).includes('Files')) { e.preventDefault(); drop.classList.add('over'); } }));
  ['dragleave', 'drop'].forEach(t => root.addEventListener(t, e => { if (t === 'dragleave' && root.contains(e.relatedTarget)) return; drop.classList.remove('over'); }));
  root.addEventListener('drop', e => {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    if (step !== 1) goto(1);
    add(e.dataTransfer.files);
  });

  // ---------- 자르기: 사진 안에서 (cx, cy)를 가운데로, 확대 z 만큼 좁힌 칸을 고른 비율로 자른다
  function rect(p, a = aspect()) {
    const base = Math.min(p.w, p.h * a); // 확대 1에서 비율을 맞춘 가장 큰 폭
    const cw = base / p.zoom, ch = cw / a;
    const x = Math.min(Math.max(p.cx * p.w - cw / 2, 0), p.w - cw);
    const y = Math.min(Math.max(p.cy * p.h - ch / 2, 0), p.h - ch);
    p.cx = (x + cw / 2) / p.w; p.cy = (y + ch / 2) / p.h; // 가장자리에 닿으면 가운데도 따라온다
    return { x, y, cw, ch };
  }
  function draw() {
    const p = photos[cur];
    crop.hidden = !p;
    drop.classList.toggle('slim', !!p);
    if (!p) return;
    frame.style.aspectRatio = aspect() === 1 ? '1 / 1' : '4 / 5';
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = Math.round(frame.clientWidth * dpr) || 400, H = Math.round(W / aspect());
    if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
    const r = rect(p);
    const g = canvas.getContext('2d');
    g.imageSmoothingQuality = 'high';
    g.drawImage(p.src, r.x, r.y, r.cw, r.ch, 0, 0, W, H);
    zoom.value = String(p.zoom);
    frame.setAttribute('aria-label', `사진 ${cur + 1} / ${photos.length} — 끌어서 위치를 옮겨요`);
  }
  let drag = null;
  frame.addEventListener('pointerdown', e => {
    const p = photos[cur];
    if (!p) return;
    e.preventDefault(); // 끄는 동안 글자가 선택되지 않게
    frame.setPointerCapture(e.pointerId);
    drag = { x: e.clientX, y: e.clientY, cx: p.cx, cy: p.cy };
    frame.classList.add('dragging');
  });
  frame.addEventListener('pointermove', e => {
    const p = photos[cur];
    if (!drag || !p) return;
    const r = rect(p);
    const k = r.cw / frame.clientWidth; // 화면 1px = 사진 k px
    p.cx = drag.cx - ((e.clientX - drag.x) * k) / p.w;
    p.cy = drag.cy - ((e.clientY - drag.y) * k) / p.h;
    draw();
  });
  const endDrag = () => { if (drag) { drag = null; frame.classList.remove('dragging'); preview(); } };
  frame.addEventListener('pointerup', endDrag);
  frame.addEventListener('pointercancel', endDrag);
  frame.addEventListener('wheel', e => {
    const p = photos[cur];
    if (!p) return;
    e.preventDefault();
    p.zoom = Math.min(3, Math.max(1, p.zoom - e.deltaY * 0.002));
    draw();
  }, { passive: false });
  zoom.addEventListener('input', () => { const p = photos[cur]; if (p) { p.zoom = Number(zoom.value); draw(); } });
  zoom.addEventListener('change', preview);
  form.addEventListener('change', e => { if (e.target.name === 'aspect') { draw(); renderThumbs(); preview(); } });
  window.addEventListener('resize', () => { if (step === 1) draw(); });

  // ---------- 고른 사진 줄: 번호 · 고르기 · ‹ › 순서 · ✕ 빼기, 마지막에 '+ 더하기'
  function thumbCanvas(p) {
    const c = el('canvas');
    const a = aspect();
    c.width = 120; c.height = Math.round(120 / a);
    const r = rect(p, a);
    c.getContext('2d').drawImage(p.src, r.x, r.y, r.cw, r.ch, 0, 0, c.width, c.height);
    return c;
  }
  function move(d) {
    const i = cur, j = i + d;
    if (j < 0 || j >= photos.length) return;
    [photos[i], photos[j]] = [photos[j], photos[i]];
    cur = j;
    renderThumbs(); draw(); preview();
  }
  // 선택한 사진 도구: ‹ 앞으로 · 뒤로 › · ✕ 빼기 (44px 버튼 — 작은 칸 위의 작은 버튼 대신)
  const tool = $('[data-photo-tools]', form);
  tool.querySelector('[data-left]').addEventListener('click', () => move(-1));
  tool.querySelector('[data-right]').addEventListener('click', () => move(1));
  tool.querySelector('[data-remove]').addEventListener('click', () => {
    photos.splice(cur, 1);
    cur = Math.max(0, Math.min(cur, photos.length - 1));
    renderThumbs(); draw(); preview();
    (thumbs.querySelector('.ps-th.on .ps-th-pick') || $('.ps-drop-in', form)).focus();
  });
  function renderThumbs() {
    thumbs.replaceChildren();
    photos.forEach((p, i) => {
      const li = el('li', `ps-th${i === cur ? ' on' : ''}`);
      li.draggable = true;
      const pick = el('button', 'ps-th-pick');
      pick.type = 'button';
      pick.setAttribute('aria-label', `사진 ${i + 1}${i === cur ? ' · 자르는 중' : ' 자르기'}`);
      pick.setAttribute('aria-pressed', i === cur ? 'true' : 'false');
      pick.append(thumbCanvas(p), el('span', 'ps-th-n mono', String(i + 1)));
      pick.addEventListener('click', () => { cur = i; renderThumbs(); draw(); thumbs.children[i].querySelector('.ps-th-pick').focus(); });
      li.append(pick);
      // 끌어서 순서 바꾸기(넓은 화면)
      li.addEventListener('dragstart', e => { e.dataTransfer.setData('text/x-ps-photo', String(i)); e.dataTransfer.effectAllowed = 'move'; });
      li.addEventListener('dragover', e => { if (Array.from(e.dataTransfer.types).includes('text/x-ps-photo')) { e.preventDefault(); e.stopPropagation(); } });
      li.addEventListener('drop', e => {
        const from = Number(e.dataTransfer.getData('text/x-ps-photo'));
        if (Number.isNaN(from) || e.dataTransfer.files.length) return;
        e.preventDefault(); e.stopPropagation();
        const [p2] = photos.splice(from, 1);
        photos.splice(i, 0, p2);
        cur = i;
        renderThumbs(); draw(); preview();
      });
      thumbs.append(li);
    });
    if (photos.length && photos.length < Math.min(MAX, LEFT)) {
      const li = el('li', 'ps-th ps-th-add');
      const lab = el('label', 'ps-th-pick');
      lab.htmlFor = fileInput.id;
      lab.style.aspectRatio = aspect() === 1 ? '1 / 1' : '4 / 5';
      lab.append(el('span', '', '＋'), el('span', 'sr', '사진 더하기'));
      li.append(lab);
      thumbs.append(li);
    }
    tool.querySelector('[data-left]').disabled = cur <= 0;
    tool.querySelector('[data-right]').disabled = cur >= photos.length - 1;
    tool.querySelector('[data-tool-n]').textContent = photos.length ? `${cur + 1} / ${photos.length}` : '';
  }

  // ---------- 1080px JPEG 로 만들기(품질을 낮춰 가며 350 KB 아래로)
  function toBlob(c, q) { return new Promise(ok => c.toBlob(b => ok(b), 'image/jpeg', q)); }
  async function encode(p, budget = TARGET_BYTES) {
    const a = aspect();
    let w = WIDTH;
    for (let tries = 0; tries < 8; tries += 1) {
      const c = el('canvas');
      c.width = w; c.height = Math.round(w / a);
      const g = c.getContext('2d');
      g.imageSmoothingQuality = 'high';
      const r = rect(p, a);
      g.fillStyle = '#fff';
      g.fillRect(0, 0, c.width, c.height);
      g.drawImage(p.src, r.x, r.y, r.cw, r.ch, 0, 0, c.width, c.height);
      for (const q of [0.82, 0.74, 0.66, 0.58]) {
        const b = await toBlob(c, q);
        if (b && b.size <= budget) return b;
      }
      w = Math.round(w * 0.85);
    }
    throw new Error('too-big');
  }

  // ---------- 미리 보기(넓은 화면은 오른쪽, 휴대폰은 ③ 단계)
  const card = $('[data-preview]');
  const pv = s => card.querySelector(s);
  let pvUrl = null;
  async function preview() {
    const box = pv('[data-pv-art]');
    const p = photos[0];
    if (p) {
      const c = el('canvas');
      const a = aspect();
      c.width = 480; c.height = Math.round(480 / a);
      const r = rect(p, a);
      c.getContext('2d').drawImage(p.src, r.x, r.y, r.cw, r.ch, 0, 0, c.width, c.height);
      const b = await toBlob(c, 0.8);
      if (pvUrl) URL.revokeObjectURL(pvUrl);
      pvUrl = URL.createObjectURL(b);
      const wrap = el('div', 'ps-pic ps-pv-photo');
      wrap.style.setProperty('--ratio', a === 1 ? '1 / 1' : '4 / 5');
      const img = el('img');
      img.src = pvUrl; img.alt = '첫 번째 사진 미리 보기';
      wrap.append(img);
      if (photos.length > 1) wrap.append(el('span', 'ps-car-chip mono', `1/${photos.length}`));
      box.replaceChildren(wrap);
    } else {
      book();
    }
  }

  // ---------- ② 책 고르기 · 글 · 별점 → 미리 보기
  function art(pick) {
    const boxEl = el('div', `ps-art p${pick.dataset.palette || 0}`);
    const title = pick.dataset.title, coverUrl = pick.dataset.cover;
    if (coverUrl) {
      const bg = el('img', 'ps-bg'); bg.src = coverUrl; bg.alt = ''; bg.setAttribute('aria-hidden', 'true');
      const cv = el('img', 'ps-cv'); cv.src = coverUrl; cv.alt = `${title} 표지`;
      boxEl.append(bg, cv);
    } else {
      const cv = el('div', `ps-cv cover p${pick.dataset.palette || 0} m${pick.dataset.pattern || 0}`);
      cv.setAttribute('role', 'img'); cv.setAttribute('aria-label', `${title} 표지`);
      cv.append(el('span', 'cv-art'), el('span', 'cv-t', title), el('span', 'cv-a', pick.dataset.author || ''));
      boxEl.append(cv);
    }
    return boxEl;
  }
  function book() {
    const pick = document.querySelector('input[name="book"]:checked');
    if (pick) pv('[data-pv-title]').textContent = pick.dataset.title + (pick.dataset.author ? ` · ${pick.dataset.author}` : '');
    if (photos.length) return;
    if (pick) pv('[data-pv-art]').replaceChildren(art(pick));
  }
  const SPOILER = /\|\|([\s\S]+?)\|\|/g;
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
  const value = name => { const c = document.querySelector(`input[name="${name}"]:checked`); return c ? c.value : ''; };
  const rateRow = form.querySelector('.ps-rate-row');
  function stars() {
    const n = Number(value('rating')) || 0;
    rateRow.dataset.n = String(n);
    const s = pv('[data-pv-stars]');
    s.hidden = !n;
    s.setAttribute('aria-label', `별점 ${n}점`);
    s.querySelectorAll('i').forEach((i, k) => i.classList.toggle('on', k < n));
  }
  const caption = $('#psCaption'), quote = $('#psQuote');
  function text() {
    const c = caption.value.trim(), q = quote.value.trim();
    pv('[data-pv-caption-wrap]').hidden = !c; rich(pv('[data-pv-caption]'), c);
    const qp = pv('[data-pv-quote]'); qp.hidden = !q; rich(qp, q);
  }
  function meta() {
    const st = form.querySelector('input[name="status"]:checked');
    if (st) pv('[data-pv-status]').textContent = st.nextElementSibling.textContent;
    pv('[data-pv-vis]').hidden = value('visibility') !== 'friends';
  }
  document.addEventListener('change', e => {
    const n = e.target.name;
    if (n === 'book') { book(); showErr(''); }
    else if (n === 'rating') stars();
    else if (n === 'status' || n === 'visibility') meta();
  });
  caption.addEventListener('input', text);
  quote.addEventListener('input', text);
  stars();

  // 책 찾기: 화면을 다시 그리지 않고(고른 사진이 남는다) JSON 으로 받아 '찾은 책' 줄에 그린다
  const found = $('[data-found]', form), note = $('[data-found-note]', form);
  async function findBooks() {
    const q = search.querySelector('input').value.trim();
    if (!q) return;
    note.hidden = false; note.textContent = '찾는 중…';
    try {
      const r = await fetch(`/posts/books?q=${encodeURIComponent(q)}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(String(r.status));
      const j = await r.json();
      const keep = found.querySelector('input:checked');
      found.replaceChildren();
      if (keep) found.append(keep.closest('.ps-pick'));
      j.items.forEach(it => {
        if (keep && keep.value === it.value) return;
        const lab = el('label', 'ps-pick');
        const inp = el('input', 'sr');
        Object.assign(inp, { type: 'radio', name: 'book', value: it.value, required: true });
        inp.setAttribute('form', 'psForm');
        Object.assign(inp.dataset, { title: it.title, author: it.author, cover: it.cover, palette: it.palette, pattern: it.pattern });
        const fb = el('div', `flatbook p${it.palette}`);
        fb.setAttribute('aria-hidden', 'true');
        if (it.cover) { const img = el('img'); img.src = it.cover.replace('/cover500/', '/cover200/'); img.alt = ''; img.loading = 'lazy'; fb.append(img); }
        else { const cv = el('div', `cover p${it.palette} m${it.pattern}`); cv.append(el('span', 'cv-art'), el('span', 'cv-t', it.title), el('span', 'cv-a', it.author)); fb.append(cv); }
        const sub = el('span');
        sub.append(el('em', 'ps-kind', it.kind), document.createTextNode(` ${it.author}`));
        lab.append(inp, fb, el('b', '', it.title), sub);
        found.append(lab);
      });
      found.hidden = !found.children.length;
      note.textContent = j.items.length ? `${j.items.length}권을 찾았어요${j.partial ? ' · 일부 도서관이 응답하지 않았어요' : ''}` : `‘${q}’(으)로 찾은 책이 없어요. 다른 말로 찾아 보세요.`;
    } catch (_) {
      note.textContent = '찾지 못했어요. 잠시 뒤에 다시 해 주세요';
    }
  }

  // ---------- ③ 공유하기: 사진을 만들고 → 한 번에 보낸다(올라가는 정도 표시)
  const bar = $('[data-progress-bar]'), prog = $('[data-progress]'), progT = $('[data-progress-t]');
  const submitBtn = $('[data-submit]', form);
  let sending = false;
  function progress(t, pct) { prog.hidden = false; progT.textContent = t; bar.style.width = `${pct}%`; }
  async function submit() {
    if (sending) return;
    if (!form.querySelector('input[name="book"]:checked')) { goto(2); showErr('올릴 책을 골라 주세요'); return; }
    sending = true; submitBtn.disabled = true; $('[data-next]').disabled = true;
    showErr('');
    try {
      const body = new FormData(form);
      body.delete('photos');
      const blobs = [];
      let budget = TARGET_BYTES;
      for (;;) {
        blobs.splice(0);
        for (let i = 0; i < photos.length; i += 1) {
          progress(`사진 줄이는 중 ${i + 1}/${photos.length}`, Math.round((i / photos.length) * 30));
          blobs.push(await encode(photos[i], Math.min(budget, HARD_BYTES)));
        }
        const total = blobs.reduce((s, b) => s + b.size, 0);
        if (total <= TOTAL_BYTES || budget < 120_000) break;
        budget = Math.round(budget * 0.7);
      }
      blobs.forEach((b, i) => body.append('photos', b, `photo-${i + 1}.jpg`));
      const res = await new Promise((ok, bad) => {
        const x = new XMLHttpRequest();
        x.open('POST', form.action);
        x.setRequestHeader('Accept', 'application/json');
        x.upload.addEventListener('progress', e => { if (e.lengthComputable) progress(`올리는 중 ${Math.round((e.loaded / e.total) * 100)}%`, 30 + Math.round((e.loaded / e.total) * 68)); });
        x.onload = () => { let j = {}; try { j = JSON.parse(x.responseText); } catch (_) { j = {}; } ok({ status: x.status, j }); };
        x.onerror = () => bad(new Error('network'));
        x.send(body);
      });
      if (res.status === 200 && res.j.url) { progress('다 올렸어요', 100); location.href = res.j.url; return; }
      throw Object.assign(new Error('rejected'), { detail: res.j.detail });
    } catch (err) {
      prog.hidden = true;
      const msg = err.detail || (err.message === 'too-big' ? '사진이 너무 커서 줄이지 못했어요. 다른 사진으로 해 주세요' : '올리지 못했어요. 연결을 확인하고 다시 해 주세요');
      if (/사진|JPEG|장까지/.test(msg)) goto(1); else if (/책/.test(msg)) goto(2);
      showErr(msg);
    } finally {
      sending = false; submitBtn.disabled = false; $('[data-next]').disabled = false;
    }
  }
  form.addEventListener('submit', e => { e.preventDefault(); submit(); });

  goto((p => (p.get('q') || p.get('step') === '2') ? 2 : 1)(new URLSearchParams(location.search)));
  if (form.querySelector('input[name="book"]:checked')) book();
})();
