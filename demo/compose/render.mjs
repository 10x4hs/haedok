// 해독 데모 — index.html을 프레임마다 찍는다 (1080x1920, 30 fps, JPEG q95).
//
//   node demo/compose/render.mjs                 # 전체 → demo/out/frames/00001.jpg … + demo/out/frames.json
//   node demo/compose/render.mjs --at 1.2,10.3   # 몇 장만 → demo/out/preview/t01.200.jpg …
//
// 클립(webm)은 먼저 demo/out/clipframes/<이름>/NNNNN.jpg 로 푼다 — 합성 쪽이 <video> 탐색 대신 정확한 프레임을 쓰게.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DEMO = path.resolve(HERE, '..');
const OUT = path.join(DEMO, 'out');
const SHELL = process.env.CHROME_SHELL || path.join(os.homedir(),
  'Library/Caches/ms-playwright/chromium_headless_shell-1254/chrome-headless-shell-mac-arm64/chrome-headless-shell');

const args = process.argv.slice(2);
const atIdx = args.indexOf('--at');
const preview = atIdx >= 0 ? args[atIdx + 1].split(',').map(Number) : null;

// 1) 클립 풀기 (webm이 새로우면 다시)
function explodeClips() {
  const dir = path.join(DEMO, 'clips');
  const counts = {};
  for (const f of fs.readdirSync(dir).filter(f => f.endsWith('.webm'))) {
    const name = f.replace(/\.webm$/, '');
    const dst = path.join(OUT, 'clipframes', name);
    const src = path.join(dir, f);
    const stamp = path.join(dst, '.src-mtime');
    const mtime = String(fs.statSync(src).mtimeMs);
    if (!fs.existsSync(stamp) || fs.readFileSync(stamp, 'utf8') !== mtime) {
      fs.rmSync(dst, { recursive: true, force: true });
      fs.mkdirSync(dst, { recursive: true });
      execFileSync('ffmpeg', ['-v', 'error', '-i', src, '-q:v', '2', path.join(dst, '%05d.jpg')]);
      fs.writeFileSync(stamp, mtime);
    }
    counts[name] = fs.readdirSync(dst).filter(x => x.endsWith('.jpg')).length;
  }
  fs.writeFileSync(path.join(OUT, 'clipframes', 'counts.json'), JSON.stringify(counts, null, 1));
  return counts;
}

// 2) demo/ 를 정적으로 띄운다 (file:// 글꼴·fetch 제약을 피하려고)
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json',
  '.jpg': 'image/jpeg', '.png': 'image/png', '.woff2': 'font/woff2', '.css': 'text/css' };
function serve() {
  const server = http.createServer((req, res) => {
    const p = path.normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname)).replace(/^([/\\])+/, '');
    const file = path.join(DEMO, p);
    if (!file.startsWith(DEMO) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise(ok => server.listen(0, '127.0.0.1', () => ok(server)));
}

const counts = explodeClips();
console.log('clips', counts);
const server = await serve();
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: SHELL, args: ['--force-color-profile=srgb', '--font-render-hinting=none'] });
const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
page.on('pageerror', e => { console.error('pageerror', e.message); process.exitCode = 1; });
await page.goto(`${base}/compose/index.html`);
await page.waitForFunction(() => window.ready).catch(() => {});
await page.evaluate(() => window.ready);
const META = await page.evaluate(() => window.META);

if (preview) {
  const dir = path.join(OUT, 'preview');
  fs.mkdirSync(dir, { recursive: true });
  for (const t of preview) {
    const m = await page.evaluate(t => window.render(t), t);
    const file = path.join(dir, `t${t.toFixed(3).padStart(7, '0')}.jpg`);
    await page.screenshot({ path: file, type: 'jpeg', quality: 90 });
    console.log(file, m.shot, JSON.stringify(m.phones.map(p => [p.x, p.y, p.w, p.h])));
  }
} else {
  const dir = path.join(OUT, 'frames');
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  const n = Math.round(META.LENGTH * META.FPS);
  const frames = [];
  const t0 = Date.now();
  for (let f = 0; f < n; f++) {
    const t = f / META.FPS;
    const m = await page.evaluate(t => window.render(t), t);
    await page.screenshot({ path: path.join(dir, `${String(f + 1).padStart(5, '0')}.jpg`), type: 'jpeg', quality: 95 });
    frames.push({ f: f + 1, ...m });
    if (f % 150 === 0) console.log(`frame ${f + 1}/${n} t=${t.toFixed(2)} ${m.shot} ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  }
  fs.writeFileSync(path.join(OUT, 'frames.json'), JSON.stringify({ fps: META.FPS, length: META.LENGTH, shots: META.SHOTS, frames }, null, 0));
  console.log(`rendered ${n} frames in ${((Date.now() - t0) / 1000).toFixed(0)} s → ${dir}`);
}
await browser.close();
server.close();
