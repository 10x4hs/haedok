// 로그인·가입·비밀번호 재설정: 칸마다 바로 검사하고 오류를 칸 아래에 보여 준다. JS가 없어도 서버 검사로 똑같이 동작한다.
(() => {
  const form = document.querySelector('form.auth-form');
  if (!form) return;
  const kind = form.dataset.auth;
  const fields = [...form.querySelectorAll('[data-field]')];
  const input = name => form.querySelector(`#${name}`);
  const bytes = v => new TextEncoder().encode(v).length;
  const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  // 칸별 규칙: 문구는 서버(app/auth.py)와 같게
  const rules = {
    nickname: v => !v.trim() ? '이름을 입력해 주세요' : v.trim().length > 40 ? '이름은 1~40자로 입력해 주세요' : '',
    email: v => !v.trim() ? '이메일을 입력해 주세요' : !EMAIL.test(v.trim()) ? '이메일 형식을 확인해 주세요' : '',
    password: v => {
      if (!v) return '비밀번호를 입력해 주세요';
      if (kind === 'login') return '';
      if (v.length < 8) return '비밀번호는 8자 이상이어야 해요';
      if (bytes(v) > 72) return '비밀번호가 너무 길어요 (최대 72바이트)';
      return '';
    },
    password_confirm: v => !v ? '비밀번호를 한 번 더 입력해 주세요' : v !== input('password').value ? '비밀번호가 서로 달라요' : '',
    code: v => /^\d{6}$/.test(v.trim()) ? '' : '6자리 숫자를 입력해 주세요',
  };

  const setError = (field, text) => {
    const el = input(field.dataset.field), err = field.querySelector('.af-err');
    if (err.textContent !== text) err.textContent = text;
    field.classList.toggle('has-err', !!text);
    if (text) el.setAttribute('aria-invalid', 'true'); else el.removeAttribute('aria-invalid');
  };
  const check = field => {
    const rule = rules[field.dataset.field];
    const text = rule ? rule(input(field.dataset.field).value) : '';
    setError(field, text);
    return !text;
  };

  // 처음엔 조용히, 한 번 손댄 칸(blur)부터 입력할 때마다 다시 검사한다. 서버 오류가 있던 칸은 처음부터 다시 검사
  fields.forEach(field => {
    const el = input(field.dataset.field);
    let touched = field.classList.contains('has-err');
    el.addEventListener('blur', () => { if (el.value || touched) { touched = true; check(field); } });
    el.addEventListener('input', () => {
      if (touched) check(field);
      if (field.dataset.field === 'password') {
        const confirm = fields.find(f => f.dataset.field === 'password_confirm');
        if (confirm && input('password_confirm').value) check(confirm);
      }
    });
  });

  // 비밀번호 보기/숨기기
  form.querySelectorAll('[data-toggle-pw]').forEach(btn => btn.addEventListener('click', () => {
    const el = document.getElementById(btn.getAttribute('aria-controls'));
    const show = el.type === 'password';
    el.type = show ? 'text' : 'password';
    btn.setAttribute('aria-pressed', String(show));
    btn.setAttribute('aria-label', show ? '비밀번호 숨기기' : '비밀번호 보기');
    el.focus({ preventScroll: true });
    const end = el.value.length; try { el.setSelectionRange(end, end); } catch { /* 일부 타입은 지원하지 않는다 */ }
  }));

  // Caps Lock 경고: 비밀번호 칸에서 키를 누를 때만 알 수 있다
  form.querySelectorAll('input[type="password"]').forEach(el => {
    const warn = el.closest('.af').querySelector('[data-caps]');
    if (!warn) return;
    const sync = e => { if (e.getModifierState) warn.hidden = !e.getModifierState('CapsLock'); };
    el.addEventListener('keydown', sync); el.addEventListener('keyup', sync);
    el.addEventListener('blur', () => { warn.hidden = true; });
  });

  // 비밀번호 강도: 길이와 섞은 문자 종류로 네 칸
  const meter = form.querySelector('[data-strength]');
  if (meter) {
    const pw = input('password'), hint = form.querySelector('#password-hint'), base = hint?.textContent || '';
    const names = ['', '약함', '보통', '강함', '아주 강함'];
    pw.addEventListener('input', () => {
      const v = pw.value;
      const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter(r => r.test(v)).length;
      let level = v ? 1 : 0;
      if (v.length >= 8 && kinds >= 2) level = 2;
      if (v.length >= 10 && kinds >= 3) level = 3;
      if (v.length >= 14 && kinds >= 3) level = 4;
      if (v.length >= 16 && kinds >= 2) level = Math.max(level, 3);
      meter.dataset.level = level;
      if (hint) hint.textContent = level ? `비밀번호 강도: ${names[level]}${v.length < 8 ? ' · 8자 이상 필요' : ''}` : base;
    });
  }

  // 재설정 코드 칸: 숫자만 6자리
  const code = input('code');
  code?.addEventListener('input', () => { const d = code.value.replace(/\D/g, '').slice(0, 6); if (d !== code.value) code.value = d; });

  // 제출: 모든 칸을 검사하고, 통과하면 버튼에 진행 상태를 보이고 두 번 보내지 않는다
  const submit = form.querySelector('[data-submit]');
  const label = submit?.querySelector('.auth-submit-label'), idle = label?.textContent;
  form.addEventListener('submit', e => {
    if (form.classList.contains('sending')) { e.preventDefault(); return; }
    const bad = fields.filter(f => !check(f));
    if (bad.length) {
      e.preventDefault();
      input(bad[0].dataset.field).focus();
      return;
    }
    form.classList.add('sending'); form.setAttribute('aria-busy', 'true');
    if (submit) { submit.setAttribute('aria-busy', 'true'); label.textContent = submit.dataset.busyLabel; setTimeout(() => { submit.disabled = true; }, 0); }
  });
  addEventListener('pageshow', () => {
    form.classList.remove('sending'); form.removeAttribute('aria-busy');
    if (submit) { submit.disabled = false; submit.removeAttribute('aria-busy'); label.textContent = idle; }
  });

  // 서버가 돌려준 오류가 있으면 그 칸으로
  const first = fields.find(f => f.classList.contains('has-err'));
  if (first) { const el = input(first.dataset.field); el.focus(); if (el.type !== 'password') el.select?.(); }
})();

// 이메일 인증: 6자리 코드 칸(붙여넣기·자동완성 지원, 6자리가 차면 바로 제출), 만료·재발송 카운트다운
(() => {
  const form = document.querySelector('form.verify');
  if (!form) return;
  const input = form.querySelector('#code'), boxes = [...form.querySelectorAll('.code-boxes i')];
  const msg = form.querySelector('[data-msg]'), expiry = form.querySelector('[data-expiry]');
  const resend = document.querySelector('[data-resend]');
  const locked = form.hasAttribute('data-locked');
  let submitted = false;

  // 보이는 6칸은 실제 입력칸의 값을 비춘다. 실제 입력칸은 칸 위에 투명하게 놓여 키보드·붙여넣기·OTP 자동완성을 그대로 받는다
  const paint = () => {
    const v = input.value;
    boxes.forEach((b, i) => {
      b.textContent = v[i] || '';
      b.classList.toggle('filled', i < v.length);
      b.classList.toggle('at', i === Math.min(v.length, 5) && document.activeElement === input);
    });
  };
  const clean = () => {
    const digits = input.value.replace(/\D/g, '').slice(0, 6);
    if (digits !== input.value) input.value = digits;
    paint();
    if (digits.length === 6 && !submitted && !locked) { submitted = true; form.classList.add('sending'); form.requestSubmit(); }
  };
  input.addEventListener('input', clean);
  input.addEventListener('paste', () => setTimeout(clean, 0));
  input.addEventListener('focus', paint);
  input.addEventListener('blur', paint);
  form.querySelector('.code-boxes').addEventListener('click', () => input.focus());
  form.addEventListener('submit', e => {
    if (!/^\d{6}$/.test(input.value)) { e.preventDefault(); submitted = false; form.classList.remove('sending'); form.removeAttribute('aria-busy'); msg.textContent = '6자리 숫자를 입력해 주세요'; msg.classList.add('bad'); input.setAttribute('aria-invalid', 'true'); return; }
    if (form.dataset.sent) { e.preventDefault(); return; }   // 자동 제출 뒤 Enter를 또 눌러도 한 번만
    form.dataset.sent = '1'; form.classList.add('sending'); form.setAttribute('aria-busy', 'true');
  });
  if (form.dataset.kind === 'wrong') { input.value = ''; input.select?.(); }
  paint();

  // 만료 카운트다운: 분 단위로만 바꾸고, 0이 되면 새 코드를 받도록 안내한다
  let left = +form.dataset.expiresIn || 0;
  const tickExpiry = () => {
    if (!expiry) return;
    if (left <= 0) {
      expiry.textContent = '코드가 만료됐어요 · 새 코드를 받아 주세요';
      input.disabled = true; form.querySelector('.go').disabled = true; resend?.classList.add('urge');
      return;
    }
    expiry.textContent = left >= 60 ? `${Math.ceil(left / 60)}분 안에 입력` : `${left}초 안에 입력`;
  };
  tickExpiry();
  if (left > 0) { const t = setInterval(() => { left -= 1; tickExpiry(); if (left <= 0) clearInterval(t); }, 1000); }
})();

// 재발송 대기(가입 인증·비밀번호 재설정 공통): 서버가 막는 시간을 그대로 보여 준다
(() => {
  const resend = document.querySelector('[data-resend]');
  if (!resend) return;
  let wait = +resend.dataset.wait || 0;
  const tick = () => {
    resend.disabled = wait > 0;
    const label = resend.querySelector('[data-countdown]');
    if (wait > 0) { if (label) label.textContent = `(${wait}초)`; else resend.insertAdjacentHTML('beforeend', ` <span data-countdown>(${wait}초)</span>`); }
    else label?.remove();
  };
  tick();
  if (wait > 0) { const t = setInterval(() => { wait -= 1; tick(); if (wait <= 0) clearInterval(t); }, 1000); }
  resend.closest('form').addEventListener('submit', e => { if (resend.disabled) { e.preventDefault(); return; } resend.setAttribute('aria-busy', 'true'); resend.textContent = '보내는 중…'; setTimeout(() => { resend.disabled = true; }, 0); });
})();
