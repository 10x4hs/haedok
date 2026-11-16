// haedokLive(presence.js) — WebSocket이 안 열리면 빨리 폴링으로 내려가고, 실패하면 간격을 늘리고,
// 탭이 가려지면 멈추는지. 가짜 시계·가짜 소켓으로 돌린다. 실행: node tests/js/live_fallback.test.cjs
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const vm = require('node:vm');

// ── 가짜 시계 ──
function makeClock() {
  let now = 0, seq = 0;
  const timers = new Map();
  const add = (fn, ms, every) => { const id = ++seq; timers.set(id, { at: now + ms, fn, every }); return id; };
  const api = {
    setTimeout: (fn, ms = 0) => add(fn, ms, 0),
    setInterval: (fn, ms) => add(fn, ms, ms),
    clearTimeout: id => timers.delete(id),
    clearInterval: id => timers.delete(id),
    pending: () => [...timers.values()].filter(t => !t.every).length,
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [id, t] = due;
        now = t.at;
        if (t.every) t.at += t.every; else timers.delete(id);
        t.fn();
        for (let i = 0; i < 5; i++) await Promise.resolve();  // async poll() 이 끝나게
      }
      now = end;
      for (let i = 0; i < 5; i++) await Promise.resolve();
    },
  };
  return api;
}

function makeDoc() {
  const ls = new Set();
  return {
    hidden: false,
    getElementById: () => null,
    addEventListener: (t, f) => t === 'visibilitychange' && ls.add(f),
    removeEventListener: (t, f) => ls.delete(f),
    setHidden(h) { this.hidden = h; ls.forEach(f => f()); },
  };
}

function load(clock, doc) {
  const src = fs.readFileSync(path.join(__dirname, '../../app/static/presence.js'), 'utf8');
  const module = { exports: {} };
  const ctx = {
    module, document: doc, window: {}, console,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    setInterval: clock.setInterval, clearInterval: clock.clearInterval,
  };
  vm.runInNewContext(src, ctx);
  return module.exports.haedokLive;
}

// 열리지도 닫히지도 않고 매달리는 소켓(서버리스에서 업그레이드가 응답 없이 걸린 경우)
function hangingWS(log) {
  return class { constructor(url) { this.url = url; this.readyState = 0; log.push(this); } close() { this.readyState = 3; } send() {} };
}
// 바로 거절되는 소켓
function refusedWS(clock, log) {
  return class {
    constructor() { this.readyState = 0; log.push(this); clock.setTimeout(() => { this.readyState = 3; this.onclose && this.onclose(); }, 50); }
    close() {} send() {}
  };
}
// 잘 열리는 소켓
function goodWS(clock, log) {
  return class {
    constructor() { this.readyState = 0; this.sent = []; log.push(this); clock.setTimeout(() => { this.readyState = 1; this.onopen && this.onopen(); }, 30); }
    close() { this.readyState = 3; } send(m) { this.sent.push(m); }
  };
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('hanging socket falls back to polling within the timeout', async () => {
  const clock = makeClock(), doc = makeDoc(), socks = [];
  const live = load(clock, doc)({ url: 'ws://x', WS: hangingWS(socks), doc, onMessage() {}, poll: async () => { polls.push(1); return true; } });
  const polls = [];
  assert.equal(live.mode, 'connecting');
  await clock.advance(3999);
  assert.equal(live.mode, 'connecting');
  await clock.advance(1);
  assert.equal(live.mode, 'poll', 'gives up on the socket after 4s');
  assert.equal(socks[0].readyState, 3, 'the hanging socket is closed');
  await clock.advance(20000);
  assert.equal(polls.length, 1, 'first poll one interval later');
  await clock.advance(20000);
  assert.equal(polls.length, 2);
  live.stop();
});

test('poll failures back off exponentially up to the cap and reset on success', async () => {
  const clock = makeClock(), doc = makeDoc();
  let ok = false, calls = 0;
  const live = load(clock, doc)({ url: 'ws://x', WS: null, doc, onMessage() {}, poll: async () => { calls++; if (!ok) throw new Error('down'); return true; } });
  assert.equal(live.mode, 'poll', 'no WebSocket at all → polling immediately');
  await clock.advance(20000); assert.equal(calls, 1); assert.equal(live.pollDelay, 40000);
  await clock.advance(40000); assert.equal(calls, 2); assert.equal(live.pollDelay, 80000);
  await clock.advance(80000); assert.equal(calls, 3); assert.equal(live.pollDelay, 120000);
  await clock.advance(120000); assert.equal(calls, 4); assert.equal(live.pollDelay, 120000, 'capped');
  ok = true;
  await clock.advance(120000); assert.equal(calls, 5); assert.equal(live.pollDelay, 20000, 'reset after success');
  live.stop();
});

test('poll returning false counts as a failure', async () => {
  const clock = makeClock(), doc = makeDoc();
  const live = load(clock, doc)({ url: 'ws://x', WS: null, doc, onMessage() {}, poll: async () => false });
  await clock.advance(20000);
  assert.equal(live.pollDelay, 40000);
  live.stop();
});

test('hidden tab pauses polling (no timers) and polls at once when visible again', async () => {
  const clock = makeClock(), doc = makeDoc();
  let calls = 0;
  const live = load(clock, doc)({ url: 'ws://x', WS: null, doc, onMessage() {}, poll: async () => { calls++; return true; } });
  await clock.advance(20000); assert.equal(calls, 1);
  doc.setHidden(true);
  assert.equal(clock.pending(), 0, 'no poll timer while hidden');
  await clock.advance(10 * 60000); assert.equal(calls, 1, 'nothing while hidden');
  doc.setHidden(false);
  await clock.advance(0); assert.equal(calls, 2, 'immediate poll on return');
  await clock.advance(20000); assert.equal(calls, 3, 'then back to the normal interval');
  live.stop();
});

test('refused socket retries with growing gaps and gives up after 3 failures', async () => {
  const clock = makeClock(), doc = makeDoc(), socks = [];
  const live = load(clock, doc)({ url: 'ws://x', WS: refusedWS(clock, socks), doc, onMessage() {}, poll: async () => true });
  await clock.advance(50); assert.equal(live.mode, 'poll'); assert.equal(socks.length, 1);
  await clock.advance(30000); assert.equal(socks.length, 2, 'retry after 30s');
  await clock.advance(50 + 59999); assert.equal(socks.length, 2);
  await clock.advance(1); assert.equal(socks.length, 3, 'second retry after 60s');
  await clock.advance(60 * 60000); assert.equal(socks.length, 3, 'gave up after 3 failures');
  assert.equal(live.wsFails, 3);
  assert.equal(live.mode, 'poll');
  live.stop();
});

test('ws:false never opens a socket and polls at the given base interval', async () => {
  const clock = makeClock(), doc = makeDoc(), socks = [];
  let calls = 0;
  const live = load(clock, doc)({ ws: false, pollBase: 3000, url: 'ws://x', WS: goodWS(clock, socks), doc, onMessage() {}, poll: async () => { calls++; return true; } });
  assert.equal(live.mode, 'poll', 'straight to polling');
  await clock.advance(3000); assert.equal(calls, 1);
  await clock.advance(3000); assert.equal(calls, 2);
  await clock.advance(60 * 60000);
  assert.equal(socks.length, 0, 'no socket attempts at all');
  live.stop();
});

test('socket that opens slows polling to the safety net, pings, and delivers messages', async () => {
  const clock = makeClock(), doc = makeDoc(), socks = [], got = [];
  let calls = 0;
  const live = load(clock, doc)({ url: 'ws://x', WS: goodWS(clock, socks), doc, onMessage: m => got.push(m), poll: async () => { calls++; return true; } });
  await clock.advance(30);
  assert.equal(live.mode, 'ws');
  socks[0].onmessage({ data: '{"type":"reaction.added","chunk":2}' });
  socks[0].onmessage({ data: 'not json' });
  assert.equal(JSON.stringify(got), '[{"type":"reaction.added","chunk":2}]');  // vm 안의 객체라 문자열로 비교
  await clock.advance(25000);
  assert.equal(socks[0].sent.join(), 'ping');
  assert.equal(live.pollDelay, 60000, 'safety-net interval while the socket is open');
  assert.equal(calls, 0);
  await clock.advance(35000);
  assert.equal(calls, 1, 'one safety-net poll a minute after opening');
  await clock.advance(25000);
  assert.equal(calls, 1);
  // 끊기면 기본 간격 폴링으로 내려가고 다시 연결을 시도한다
  socks[0].readyState = 3; socks[0].onclose();
  assert.equal(live.mode, 'poll');
  assert.equal(live.pollDelay, 20000);
  await clock.advance(20000); assert.equal(calls, 2);
  await clock.advance(10000 + 30); assert.equal(socks.length, 2); assert.equal(live.mode, 'ws');
  live.stop();
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`ok - ${name}`); } catch (e) { failed++; console.log(`not ok - ${name}\n${e.stack}`); }
  }
  console.log(`${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
