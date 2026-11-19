/* 읽은 책 게시물 — 인스타그램처럼 동작하는 작은 것들.
 * 가리기 열기 · 좋아요/저장(새로고침 없이) · 그림 두 번 탭 = 좋아요 · 사진 넘김(밀기 · 화살표 · 점 · 1/3) ·
 * 보내기 시트(친구에게 DM) · 댓글 시트(휴대폰 피드) · 댓글 좋아요 · 답글 · 글자 수 · 긴 글 '더 보기'.
 * JS가 없어도 모든 폼은 그대로 동작한다(서버가 처리하고 돌아온다). 사용자 글로 DOM을 만들 때는 textContent 만 쓴다.
 * 댓글 시트는 서버가 이스케이프해 그린 조각(/p/{id}/comments)을 DOMParser 로 옮겨 붙인다. */
(() => {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const toast = msg => { if (window.haedokToast) window.haedokToast(msg); };
  const JSON_H = { Accept: 'application/json' };

  async function post(form, extra) {
    const body = new FormData(form);
    if (extra) Object.entries(extra).forEach(([k, v]) => body.set(k, v));
    const r = await fetch(form.action, { method: 'POST', body, headers: JSON_H, credentials: 'same-origin' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(j.detail || String(r.status)); e.status = r.status; e.detail = j.detail; throw e; }
    return j;
  }

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

  // ---------- 좋아요 줄: 'OO님 외 n명이 좋아합니다' 또는 '좋아요 n개'
  function likeLine(line, count) {
    if (!line) return;
    const friend = line.dataset.friend || '';
    const b = (t, cnt) => { const n = document.createElement('b'); if (cnt) n.dataset.likeCount = ''; n.textContent = t; return n; };
    const t = s => document.createTextNode(s);
    line.replaceChildren();
    if (count && friend) {
      if (count > 1) line.append(b(friend), t('님 외 '), b(String(count - 1), true), t('명이 좋아합니다'));
      else line.append(b(friend), t('님이 좋아합니다'));
    } else if (count) {
      line.append(t('좋아요 '), b(String(count), true), t('개'));
    }  // 0개면 비운다(인스타그램처럼 안내 문장을 두지 않는다)
  }

  // want=1 은 '켜기만'(그림 두 번 탭) — 몇 번 보내도 같다
  async function sendLike(form, want) {
    const btn = form.querySelector('button');
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const j = await post(form, want ? { want: '1' } : null);
      btn.setAttribute('aria-pressed', j.liked ? 'true' : 'false');
      btn.setAttribute('aria-label', j.liked ? '좋아요 취소' : '좋아요');
      btn.querySelector('.ps-heart').classList.toggle('on', j.liked);
      // 친구 이름이 들어간 줄은 내가 끄고 켤 때 친구 수를 바꾸지 않는다 — 서버 수에서 나를 빼고 다시 센다
      likeLine((form.closest('.ps-post') || document).querySelector('[data-like-line]'), j.count);
    } catch (err) {
      if (err.status === 429) toast(err.detail || '잠시 뒤에 다시 해 주세요');
      else if (!want) form.submit();
    } finally {
      btn.disabled = false;
    }
  }
  document.addEventListener('submit', e => {
    const f = e.target;
    if (f.matches('form[data-like]')) { e.preventDefault(); sendLike(f, false); }
    else if (f.matches('form[data-save]')) { e.preventDefault(); sendSave(f); }
    else if (f.matches('form[data-cm-like]')) { e.preventDefault(); sendCommentLike(f); }
  });

  // ---------- 저장(책갈피)
  async function sendSave(form) {
    const btn = form.querySelector('button');
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const j = await post(form);
      btn.setAttribute('aria-pressed', j.saved ? 'true' : 'false');
      btn.setAttribute('aria-label', j.saved ? '저장 취소' : '저장');
      btn.querySelector('.ps-mark').classList.toggle('on', j.saved);
      toast(j.saved ? '저장했어요 · 내 프로필의 저장됨에서 볼 수 있어요' : '저장을 취소했어요');
    } catch (err) {
      if (err.status === 429) toast(err.detail || '잠시 뒤에 다시 해 주세요');
      else form.submit();
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- 그림 두 번 탭(더블클릭) = 좋아요. 가운데 하트가 커졌다 사라진다(움직임 줄이기면 나타났다 사라지기만)
  document.addEventListener('click', e => {
    const pic = e.target.closest('[data-dbl-like]');
    if (!pic || e.target.closest('.ps-veil, button, a')) return;
    const now = Date.now();
    if (now - (pic._last || 0) < 320) {
      pic._last = 0;
      const form = (pic.closest('.ps-post') || document).querySelector('form[data-like]');
      const pop = pic.querySelector('.ps-pop');
      if (pop) { pop.classList.remove('show'); void pop.offsetWidth; pop.classList.add('show'); }
      if (form) sendLike(form, true);
    } else {
      pic._last = now;
    }
  });

  // ---------- 사진 넘김: 가로 스크롤 + scroll-snap. 화살표 · 점 · '1/3' 은 지금 장을 따라간다
  function initCar(car) {
    if (car._ready) return;
    car._ready = true;
    const track = car.querySelector('[data-car-track]');
    const slides = track.children;
    const n = slides.length;
    if (n < 2) return;
    const pic = car.closest('.ps-pic');
    const dots = pic && pic.nextElementSibling && pic.nextElementSibling.matches('[data-car-dots]') ? pic.nextElementSibling.children : [];
    const now = car.querySelector('[data-car-now]');
    const prev = car.querySelector('[data-car-prev]');
    const next = car.querySelector('[data-car-next]');
    let at = 0;
    const paint = () => {
      const i = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
      if (i === at && now.textContent === String(i + 1)) return;
      at = Math.max(0, Math.min(n - 1, i));
      now.textContent = String(at + 1);
      Array.from(dots).forEach((d, k) => d.classList.toggle('on', k === at));
      prev.hidden = at === 0;
      next.hidden = at === n - 1;
    };
    const go = i => track.scrollTo({ left: i * track.clientWidth, behavior: reduced ? 'auto' : 'smooth' });
    track.addEventListener('scroll', () => window.requestAnimationFrame(paint), { passive: true });
    prev.addEventListener('click', () => go(at - 1));
    next.addEventListener('click', () => go(at + 1));
    track.addEventListener('keydown', e => {
      if (e.key === 'ArrowRight') { e.preventDefault(); go(Math.min(n - 1, at + 1)); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); go(Math.max(0, at - 1)); }
    });
  }

  // ---------- 시트(아래에서 올라오는 창) — <dialog> 하나를 열고 닫는다. 바깥 · ✕ · Esc 로 닫힌다
  function openSheet(d, opener) {
    if (!d || typeof d.showModal !== 'function') return false;
    d._opener = opener || null;
    d.showModal();
    return true;
  }
  function closeSheet(d) {
    if (!d.open) return;
    d.close();
  }
  document.addEventListener('click', e => {
    const x = e.target.closest('dialog.ps-sheet [data-close]');
    if (x) { closeSheet(x.closest('dialog')); return; }
    if (e.target.matches('dialog.ps-sheet')) closeSheet(e.target); // 바깥(어두운 곳)을 눌렀다
  });
  document.querySelectorAll('dialog.ps-sheet').forEach(d => {
    d.addEventListener('close', () => { if (d._opener && d._opener.focus) d._opener.focus(); });
  });

  // ---------- 보내기: 친구를 고르면 그 친구와의 개인 채팅에 게시물 링크가 간다
  const sendSheet = document.getElementById('psSend');
  if (sendSheet) {
    const form = sendSheet.querySelector('[data-send-form]');
    const copy = sendSheet.querySelector('[data-send-copy]');
    const q = sendSheet.querySelector('[data-send-q]');
    document.addEventListener('click', e => {
      const b = e.target.closest('[data-send]');
      if (!b) return;
      const id = b.dataset.send;
      if (form) form.action = `/p/${id}/send`;
      copy.dataset.share = `/p/${id}`;
      if (q) { q.value = ''; q.dispatchEvent(new Event('input')); }
      openSheet(sendSheet, b);
    });
    if (q) q.addEventListener('input', () => {
      const t = q.value.trim();
      sendSheet.querySelectorAll('[data-name]').forEach(li => { li.hidden = !!t && !li.dataset.name.includes(t); });
    });
    if (form) form.addEventListener('submit', async e => {
      e.preventDefault();
      const who = e.submitter;
      if (!who) return;
      who.disabled = true;
      try {
        await post(form, { friend_id: who.value });
        const go = who.querySelector('.ps-send-go');
        if (go) go.textContent = '보냄';
        who.classList.add('sent');
        toast(`${who.querySelector('b').textContent}님에게 보냈어요`);
      } catch (err) {
        who.disabled = false;
        toast(err.detail || '보내지 못했어요. 잠시 뒤에 다시 해 주세요');
      }
    });
    copy.addEventListener('click', () => closeSheet(sendSheet));
  }

  // ---------- 공유 = 링크 복사
  document.addEventListener('click', async e => {
    const b = e.target.closest('[data-share]');
    if (!b || !b.dataset.share) return;
    const url = new URL(b.dataset.share, location.origin).href;
    let ok = false;
    try { await navigator.clipboard.writeText(url); ok = true; } catch (_) { ok = false; }
    toast(ok ? '링크를 복사했어요' : url);
    const menu = b.closest('details');
    if (menu) menu.open = false;
  });

  // ---------- 댓글 시트(휴대폰 피드): 말풍선 · '댓글 n개 모두 보기'를 누르면 아래에서 올라온다.
  // 넓은 화면은 게시물 화면(왼쪽 그림 · 오른쪽 댓글)으로 그대로 간다.
  const cmSheet = document.getElementById('psComments');
  const phone = window.matchMedia('(max-width: 767px)');
  async function loadComments(url, keepScroll) {
    const body = cmSheet.querySelector('[data-sheet-body]');
    const top = keepScroll ? body.scrollTop : 0;
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    const wrap = doc.querySelector('[data-cm-wrap]');
    body.replaceChildren(document.adoptNode(wrap));
    body.scrollTop = top;
    wire(body);
    return wrap;
  }
  if (cmSheet) {
    document.addEventListener('click', async e => {
      const a = e.target.closest('[data-comments]');
      if (!a || !phone.matches || a.closest('.ps-view')) return;
      e.preventDefault();
      cmSheet._url = a.dataset.comments;
      const body = cmSheet.querySelector('[data-sheet-body]');
      const wait = document.createElement('p');
      wait.className = 'muted small ps-sheet-wait';
      wait.textContent = '불러오는 중…';
      body.replaceChildren(wait);
      openSheet(cmSheet, a);
      try {
        await loadComments(cmSheet._url);
        if (a.matches('.ps-act')) { const t = cmSheet.querySelector('textarea'); if (t) t.focus(); }
      } catch (_) {
        location.href = a.getAttribute('href');
      }
    });
  }

  // ---------- 댓글: 좋아요(작은 하트) · 답글 달기 · 입력칸
  async function sendCommentLike(form) {
    const btn = form.querySelector('button');
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const j = await post(form);
      btn.setAttribute('aria-pressed', j.liked ? 'true' : 'false');
      btn.setAttribute('aria-label', j.liked ? '댓글 좋아요 취소' : '댓글 좋아요');
      btn.querySelector('.ps-heart').classList.toggle('on', j.liked);
      const li = form.closest('.ps-cm');
      const meta = li && li.querySelector(':scope > .ps-cm-b > .ps-cm-meta [data-cm-likes]');
      if (meta) { meta.hidden = !j.count; meta.querySelector('b').textContent = String(j.count); }
    } catch (err) {
      if (err.status === 429) toast(err.detail || '잠시 뒤에 다시 해 주세요');
      else form.submit();
    } finally {
      btn.disabled = false;
    }
  }

  function wire(root) {
    root.querySelectorAll('[data-cm-wrap]').forEach(wrap => {
      if (wrap._wired) return;
      wrap._wired = true;
      const form = wrap.querySelector('[data-cm-form]');
      const t = form.querySelector('textarea');
      const parent = form.querySelector('[data-parent]');
      const chip = form.querySelector('[data-replying]');
      const stopReply = () => { parent.value = ''; parent.disabled = true; chip.hidden = true; };
      wrap.addEventListener('click', e => {
        const r = e.target.closest('[data-reply]');
        if (r) {
          parent.disabled = false;
          parent.value = r.dataset.reply;
          chip.querySelector('[data-replying-t]').textContent = `${r.dataset.nick}님에게 답글 남기는 중`;
          chip.hidden = false;
          const tag = `@${r.dataset.nick} `;
          if (!t.value.startsWith(tag)) t.value = tag + t.value;
          t.focus();
          t.setSelectionRange(t.value.length, t.value.length);
          t.dispatchEvent(new Event('input'));
        }
        if (e.target.closest('[data-replying-x]')) { stopReply(); t.focus(); }
      });
      t.addEventListener('input', () => { t.style.height = 'auto'; t.style.height = `${Math.min(t.scrollHeight + 2, 140)}px`; });
      bindCount(t);
      // 시트 안에서는 보내고 나서 목록만 다시 불러온다(화면이 넘어가지 않게)
      if (cmSheet && cmSheet.contains(wrap)) {
        form.addEventListener('submit', async e => {
          e.preventDefault();
          const btn = form.querySelector('.ps-cm-post');
          btn.disabled = true;
          try {
            const j = await post(form);
            const fresh = await loadComments(cmSheet._url, true);
            const row = fresh.querySelector(`#c${j.id}`);
            if (row) {
              const box = row.closest('details');
              if (box) box.open = true;
              row.scrollIntoView({ block: 'nearest' });
            }
          } catch (err) {
            toast(err.detail || '댓글을 올리지 못했어요');
            btn.disabled = false;
          }
        });
      }
    });
  }

  // ---------- 긴 글은 두 줄에서 접고 '더 보기'
  function initClamp(cap) {
    const more = cap.nextElementSibling;
    if (cap._ready || !more || !more.matches('[data-more]')) return;
    cap._ready = true;
    if (cap.scrollHeight - cap.clientHeight > 2) {
      more.hidden = false;
      more.addEventListener('click', () => { cap.classList.add('open'); more.remove(); });
    }
  }
  // 처음 그린 화면과, 나중에 끼워 넣은 칸(피드의 이어 읽기 · 새 게시물)에 같은 동작을 붙인다
  function enhance(root) {
    root.querySelectorAll('[data-car]').forEach(initCar);
    root.querySelectorAll('[data-clamp]').forEach(initClamp);
    root.querySelectorAll('[data-count]').forEach(bindCount);
  }
  enhance(document);

  // ---------- ⋯ 메뉴는 바깥을 누르면 닫힌다
  document.addEventListener('click', e => {
    document.querySelectorAll('details.ps-menu[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
  });

  // ---------- 글자 수
  function bindCount(input) {
    const out = document.getElementById(input.dataset.count);
    if (!out || input._counted) return;
    input._counted = true;
    const sync = () => { out.textContent = `${input.value.length}/${input.maxLength}`; };
    input.addEventListener('input', sync);
    sync();
  }
  document.querySelectorAll('[data-count]').forEach(bindCount);
  wire(document);

  // 다른 스크립트(올리기 화면)가 같이 쓴다
  window.haedokPosts = { post, toast, reduced, enhance };
})();
