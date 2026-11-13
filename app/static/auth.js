// 로그인·가입: 입력칸을 하나씩 보여 주고 → 로 넘어간다. JS가 없으면 모든 칸이 한꺼번에 보인다.
(() => {
  const form = document.querySelector('.steps');
  if (!form) return;
  const steps = [...form.querySelectorAll('[data-step]')];
  const msg = form.querySelector('[data-msg]'), back = form.querySelector('[data-back]');
  const dots = [...form.querySelectorAll('.step-dots i')];
  form.classList.add('stepped');

  const check = input => {
    const v = input.value.trim();
    if (!v) return `${input.placeholder.replace(/ \(.+\)/, '')}을(를) 입력해 주세요`;
    if (input.type === 'email' && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) return '이메일 형식을 확인해 주세요';
    if (input.minLength > 1 && input.value.length < input.minLength) return `${input.minLength}자 이상 입력해 주세요`;
    if (input.name === 'code' && !/^\d{6}$/.test(v)) return '6자리 숫자를 입력해 주세요';
    if (input.name === 'password_confirm' && input.value !== form.password.value) return '비밀번호가 서로 달라요';
    return '';
  };

  let at = 0;
  const show = (i, note = '') => {
    at = i;
    steps.forEach((s, k) => { s.classList.toggle('on', k === i); s.classList.toggle('past', k < i); });
    dots.forEach((d, k) => d.classList.toggle('on', k <= i));
    if (back) back.hidden = i === 0;
    msg.textContent = note;
    const input = steps[i].querySelector('input');
    setTimeout(() => input.focus(), 60);
  };

  const advance = () => {
    const input = steps[at].querySelector('input');
    const err = check(input);
    if (err) { msg.textContent = err; steps[at].classList.remove('shake'); void steps[at].offsetWidth; steps[at].classList.add('shake'); return false; }
    if (at < steps.length - 1) { show(at + 1); return false; }
    return true;
  };

  form.querySelectorAll('[data-next]').forEach(b => b.addEventListener('click', advance));
  form.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.tagName === 'INPUT') { e.preventDefault(); if (advance()) form.requestSubmit(); } });
  form.addEventListener('submit', e => { if (at < steps.length - 1 || check(steps[at].querySelector('input'))) { e.preventDefault(); advance(); return; } form.classList.add('sending'); });
  back?.addEventListener('click', () => show(Math.max(0, at - 1)));

  // 서버가 돌려준 오류: 가입은 그 칸에서, 로그인은 비밀번호 칸에서 다시 시작
  const firstErr = form.dataset.firstError;
  const serverMsg = msg.textContent.trim();
  if (firstErr) { const k = steps.findIndex(s => s.dataset.name === firstErr); show(Math.max(0, k), steps[k]?.dataset.error || ''); }
  else if (serverMsg && steps.length === 2) show(1, serverMsg);
  else show(0, serverMsg);
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
    if (!/^\d{6}$/.test(input.value)) { e.preventDefault(); submitted = false; form.classList.remove('sending'); msg.textContent = '6자리 숫자를 입력해 주세요'; msg.classList.add('bad'); return; }
    form.classList.add('sending');
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

  // 재발송 60초 대기: 서버가 막는 시간을 그대로 보여 준다
  if (resend) {
    let wait = +resend.dataset.wait || 0;
    const label = resend.querySelector('[data-countdown]');
    const tickWait = () => {
      resend.disabled = wait > 0;
      if (wait > 0) { if (label) label.textContent = `(${wait}초)`; else resend.insertAdjacentHTML('beforeend', ` <span data-countdown>(${wait}초)</span>`); }
      else resend.querySelector('[data-countdown]')?.remove();
    };
    tickWait();
    if (wait > 0) { const t = setInterval(() => { wait -= 1; tickWait(); if (wait <= 0) clearInterval(t); }, 1000); }
    resend.closest('form').addEventListener('submit', () => { resend.disabled = true; resend.textContent = '보내는 중…'; });
  }
})();
