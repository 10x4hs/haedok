"""데모 영상용 화면 캡처 — 앱을 새 SQLite로 띄우고, 시드를 심고, 스틸과 짧은 클립을 찍는다.

    .venv/bin/python demo/capture/capture.py   # 늘 새 DB로 처음부터 다시 찍는다

결과
- demo/shots/<name>.jpg — 390x844 @2x(780x1688) 스틸, 각 400 KB 이하
- demo/clips/<name>.webm — CDP 스크린캐스트를 30fps VP9로 묶은 클립, 각 3 MB 이하
- demo/clips/<name>.json — 클립 길이·주요 순간(초) 메모(합성 쪽에서 참고)

scripts/readme_shots.py 의 계정·로그인·읽기 도우미를 그대로 쓴다.
책 본문은 위키문헌에서 가져오므로 네트워크가 필요하다.
"""

import asyncio
import base64
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import readme_shots as rs  # noqa: E402

PORT = int(os.environ.get("DEMO_PORT", "8078"))
BASE = f"http://127.0.0.1:{PORT}"
rs.BASE = BASE
DEMO = ROOT / "demo"
SHOTS = DEMO / "shots"
CLIPS = DEMO / "clips"
WORK = DEMO / "out" / "capture"
DB = WORK / "capture.db"
DB_URL = f"sqlite+aiosqlite:///{DB}"
DWELL = 0.06  # 쪽마다 15초 x 0.06 = 0.9초 머물면 '읽음'
HEADLESS = Path.home() / (
    "Library/Caches/ms-playwright/chromium_headless_shell-1254/chrome-headless-shell-mac-arm64/chrome-headless-shell"
)
STORE_ID = "20628514"  # 데미안 (한글판)
RACE_BOOK = "운수 좋은 날"
SHELF_EXTRA = ["메밀꽃 필 무렵", "날개", "감자"]
# 화면 꾸미기 (mode, bg, accent). 첫 번째가 본편 — '안개'는 어두운 유리 느낌을 지키면서 화면 평균 밝기가
# 빈 화면 검사 기준(32)을 넉넉히 넘는다. 나머지는 몽타주용.
LOOKS = [
    ("dark", "fog", "pink"),
    ("dark", "prism", "violet"),
    ("dark", "sunset", "orange"),
    ("light", "plain", "blue"),
]
RACE_BOXES = {"track": "#raceTrack", "me": ".rc-lane.me", "lead": ".rc-lane.lead"}
PRESENCE_BOXES = {
    "here": ".pz-here",
    "edge": ".pz-edge",
    "friends": "#friends",
    "track": "#track",
    "foot": "#foot",
}
JPG_MAX = 400_000
WEBM_MAX = 3_000_000

# 데미안 첫머리(직접 옮긴 문장) — 업로드 시연용 PDF 본문
DEMIAN = [
    ln
    for ln in (Path(__file__).with_name("demian.txt").read_text().splitlines())
    if ln and not ln.startswith("#")
]


# ---------------------------------------------------------------- 서버


def start_server() -> subprocess.Popen:
    WORK.mkdir(parents=True, exist_ok=True)
    for p in WORK.glob("capture.db*"):
        p.unlink()
    env = os.environ | {
        "DATABASE_URL": DB_URL,
        "SECRET_KEY": "demo-capture",
        "DWELL_SCALE": str(DWELL),
        "ADMIN_EMAILS": "admin@haedok.app",
    }
    log = open(WORK / "server.log", "w")
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/uvicorn"), "app.main:app", "--port", str(PORT)],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    import urllib.request

    for _ in range(80):
        try:
            urllib.request.urlopen(f"{BASE}/health", timeout=1)
            return proc
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise SystemExit("server did not start — see demo/out/capture/server.log")


def stop_server(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


# ---------------------------------------------------------------- 저장 도우미


def save_jpg(png: bytes, name: str) -> Path:
    """PNG 바이트를 JPEG로 — 400 KB를 넘으면 품질을 낮춰 다시 굽는다."""
    tmp = WORK / f"{name}.png"
    tmp.write_bytes(png)
    out = SHOTS / f"{name}.jpg"
    for q in (3, 4, 5, 6, 8, 10):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-q:v", str(q), str(out)], check=True)
        if out.stat().st_size <= JPG_MAX:
            break
    tmp.unlink()
    print(f"shot {name:22s} {out.stat().st_size // 1024} KB", flush=True)
    return out


BOXES: dict[str, dict] = {}  # 스틸마다 주요 요소의 위치(CSS px, 390x844) — 합성에서 밀어 들어갈 초점


async def boxes_of(page, sel: dict[str, str]) -> dict:
    return await page.evaluate(
        """(sel) => Object.fromEntries(Object.entries(sel).map(([k, q]) => {
          let r = null;
          const s = getSelection();
          if (q === '::selection') { if (s.rangeCount) r = s.getRangeAt(0).getBoundingClientRect(); }
          else { const el = document.querySelector(q); if (el) r = el.getBoundingClientRect(); }
          return [k, r && r.width ? [r.x, r.y, r.width, r.height].map(v => Math.round(v * 10) / 10) : null];
        }))""",
        sel,
    )


async def snap(page, name: str, wait: int = 0, boxes: dict[str, str] | None = None) -> None:
    if wait:
        await page.wait_for_timeout(wait)
    if boxes:
        BOXES[name] = await boxes_of(page, boxes)
    save_jpg(await page.screenshot(), name)


def events_of(frames: list[tuple[float, bytes]], threshold: float = 1.5) -> list[list[float]]:
    """화면이 눈에 띄게 바뀐 순간(초, 변화량) — 합성에서 이 순간을 박에 맞춘다."""
    import io

    from PIL import Image, ImageChops, ImageStat

    out, prev = [], None
    for t, data in frames:
        im = Image.open(io.BytesIO(data)).convert("L").resize((98, 211))
        if prev is not None:
            d = ImageStat.Stat(ImageChops.difference(im, prev)).mean[0]
            if d > threshold:
                out.append([round(t, 3), round(d, 1)])
        prev = im
    return out


class Cast:
    """CDP 스크린캐스트 — 화면이 바뀔 때마다 오는 프레임을 시각과 함께 모아 30fps webm으로 묶는다."""

    def __init__(self, page, name: str, boxes: dict[str, str] | None = None):
        self.page, self.name, self.boxes = page, name, boxes
        self.frames: list[tuple[float, bytes]] = []
        self.marks: dict[str, float] = {}

    async def __aenter__(self):
        self.cdp = await self.page.context.new_cdp_session(self.page)
        self.cdp.on("Page.screencastFrame", self._on)
        self.t0 = time.monotonic()
        await self.cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 92, "maxWidth": 780, "maxHeight": 1688, "everyNthFrame": 1},
        )
        # 시작 프레임을 보장한다(정지 화면이면 프레임이 오지 않는다)
        self.frames.append((0.0, await self.page.screenshot(type="jpeg", quality=92)))
        return self

    def _on(self, params):
        self.frames.append((time.monotonic() - self.t0, base64.b64decode(params["data"])))
        asyncio.ensure_future(self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]}))

    def mark(self, key: str) -> None:
        self.marks[key] = round(time.monotonic() - self.t0, 3)

    async def __aexit__(self, *exc):
        await self.page.wait_for_timeout(150)
        end = time.monotonic() - self.t0
        boxes = await boxes_of(self.page, self.boxes) if self.boxes else {}
        await self.cdp.send("Page.stopScreencast")
        await self.cdp.detach()
        tmp = WORK / f"cast-{self.name}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        lines = []
        frames = sorted(self.frames, key=lambda f: f[0])
        for i, (t, data) in enumerate(frames):
            nxt = frames[i + 1][0] if i + 1 < len(frames) else end
            (tmp / f"{i:05d}.jpg").write_bytes(data)
            lines += [f"file '{i:05d}.jpg'", f"duration {max(nxt - t, 0.001):.4f}"]
        lines.append(f"file '{len(frames) - 1:05d}.jpg'")
        (tmp / "list.txt").write_text("\n".join(lines) + "\n")
        out = CLIPS / f"{self.name}.webm"
        for crf in (30, 34, 38, 42):
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(tmp / "list.txt"),
                    "-vf",
                    "fps=30,scale=780:1688:flags=lanczos,format=yuv420p",
                    "-c:v",
                    "libvpx-vp9",
                    "-crf",
                    str(crf),
                    "-b:v",
                    "0",
                    "-row-mt",
                    "1",
                    "-deadline",
                    "good",
                    "-an",
                    str(out),
                ],
                check=True,
            )
            if out.stat().st_size <= WEBM_MAX:
                break
        shutil.rmtree(tmp)
        meta = {
            "duration": round(end, 3),
            "frames": len(frames),
            "marks": self.marks,
            "events": events_of(frames),
            "boxes": boxes,
        }
        (CLIPS / f"{self.name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n")
        kb = out.stat().st_size // 1024
        print(f"clip {self.name:22s} {end:.2f}s {len(frames)} frames {kb} KB {self.marks}", flush=True)


# ---------------------------------------------------------------- 앱 조작 도우미


async def api(p, method: str, path: str, token: str, body: dict) -> dict:
    r = await p.request.fetch(f"{BASE}{path}", method=method, data=body, headers=rs._json(token))
    assert r.ok, (path, r.status, await r.text())
    return await r.json()


async def set_look(p, token: str, mode: str, bg: str, accent: str) -> None:
    await api(p, "PUT", "/api/v1/me/settings", token, {"mode": mode, "bg": bg, "accent": accent})


async def read_range(p, book: int, start: int, upto: int, token: str) -> None:
    for i in range(start, upto + 1):
        for v in (False, True):
            await api(p, "PUT", f"/api/v1/books/{book}/progress", token, {"chunk_index": i, "verified": v})
            if not v:
                await asyncio.sleep(15 * DWELL + 0.15)


async def make_pdf(browser) -> Path:
    """데미안 첫머리를 PDF로 굽는다(시스템 한글 글꼴, 텍스트 추출 가능)."""
    path = WORK / "데미안.pdf"
    pg = await browser.new_page()
    paras = "".join(f"<p>{t}</p>" for t in DEMIAN * 3)
    await pg.set_content(
        "<html><body style=\"font-family:'Apple SD Gothic Neo',sans-serif;font-size:13pt;line-height:1.7;"
        'margin:48px"><h1>데미안</h1><h3>1. 두 세계</h3>' + paras + "</body></html>"
    )
    await pg.pdf(path=str(path), format="A5")
    await pg.close()
    return path


async def stage_page(p) -> dict:
    """리더 화면의 현재 쪽 번호와 전체 쪽 수."""
    return await p.evaluate(
        "() => { const s = document.getElementById('stage');"
        " return {start: +s.dataset.start, total: +s.dataset.total}; }"
    )


async def select_sentence(p) -> str:
    """지금 보이는 쪽의 문단 하나에서 첫 문장을 드래그한 것처럼 고른다."""
    return await p.evaluate(
        """() => {
      let el = null;
      for (const y of [0.32, 0.4, 0.48, 0.26, 0.56]) {
        const hit = document.elementFromPoint(innerWidth / 2, innerHeight * y);
        const q = hit && hit.closest('#stage p');
        if (q && q.textContent.trim().length > 30) { el = q; break; }
      }
      if (!el) return '';
      const node = [...el.childNodes].find(n => n.nodeType === 3 && n.textContent.trim().length > 20)
        || el.firstChild;
      const txt = node.textContent;
      let end = txt.search(/[.?!]/);
      end = end < 12 ? Math.min(txt.length, 48) : end + 1;
      const start = txt.length - txt.trimStart().length;
      const r = document.createRange(); r.setStart(node, start); r.setEnd(node, end);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r);
      document.dispatchEvent(new Event('selectionchange'));
      return txt.slice(start, end);
    }"""
    )


# ---------------------------------------------------------------- 본편


async def main() -> None:
    from playwright.async_api import async_playwright

    for d in (SHOTS, CLIPS):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    proc = start_server()
    try:
        await rs.seed_users(DB_URL)
        async with async_playwright() as pw:
            br = await pw.chromium.launch(executable_path=str(HEADLESS) if HEADLESS.exists() else None)
            await run(br)
            await br.close()
    finally:
        stop_server(proc)


async def run(br) -> None:
    pdf = await make_pdf(br)
    me_email, friend_email, third_email = (e for _, e in rs.ACCOUNTS)

    # --- 로그아웃 랜딩
    G = await br.new_context(**rs.PHONE)
    g = await G.new_page()
    await g.goto(f"{BASE}/", wait_until="networkidle")
    await snap(g, "landing", wait=2500, boxes={"h1": "h1"})

    # --- 지우: 화면 꾸미기(분홍 강조) → 서점 → 올리기
    A = await br.new_context(**rs.PHONE)
    a = await rs.login(A, me_email)
    ta = await rs.csrf(a)
    await set_look(a, ta, *LOOKS[0])

    await a.goto(f"{BASE}/store/{STORE_ID}", wait_until="networkidle")
    await snap(a, "store", wait=1500, boxes={"cover": ".store-cover", "buy": "#buy", "h2": ".store-info h2"})
    await a.evaluate("window.scrollTo(0, 300)")
    await snap(a, "store-steps", wait=500)

    await a.goto(f"{BASE}/upload?store={STORE_ID}", wait_until="networkidle")
    await snap(a, "upload-empty", wait=1000, boxes={"drop": "#drop", "go": "#go"})
    await a.set_input_files("#file", str(pdf))
    await snap(a, "upload-picked", wait=400, boxes={"drop": "#drop", "go": "#go", "picked": "#picked"})

    # '읽는 중…' 상태: 폼의 submit 처리기만 돌리고(가짜 submit 이벤트는 전송하지 않는다) 찍은 뒤 되돌린다
    await a.evaluate("document.getElementById('drop').dispatchEvent(new Event('submit'))")
    await snap(a, "upload-sending", wait=300, boxes={"drop": "#drop", "go": "#go"})
    reset = "(g => { g.disabled = false; g.textContent = '올리고 읽기'; })(document.getElementById('go'))"
    await a.evaluate(reset)

    async def slow_upload(route):  # 올리는 동안(버튼 스피너)이 보이도록 요청을 잠깐 붙잡는다
        await asyncio.sleep(1.6)
        await route.continue_()

    await a.route(f"{BASE}/upload", slow_upload)
    async with Cast(a, "upload", {"stage": "#stage"}) as c:
        await a.wait_for_timeout(400)
        c.mark("press")
        await a.click("#go")
        await a.wait_for_url(re.compile(r"/books/\d+"), timeout=30000)
        c.mark("reader")
        await a.wait_for_timeout(1500)
    await a.unroute(f"{BASE}/upload")
    mine = int(a.url.rsplit("/", 1)[1])
    await snap(a, "reader-mine", wait=600, boxes={"stage": "#stage", "foot": "#foot"})

    # 쪽 넘김 클립 — 두 번 넘긴다
    async with Cast(a, "pageturn", {"stage": "#stage"}) as c:
        for k in range(2):
            await a.wait_for_timeout(700)
            c.mark(f"turn{k}")
            await a.click("#nextPg")
        await a.wait_for_timeout(1400)
    await snap(a, "reader-mine-2")

    # --- 대결 책과 서재 채우기
    await a.goto(f"{BASE}/open/wikisource/{RACE_BOOK}")
    await a.wait_for_url(re.compile(r"/books/\d+"), timeout=60000)
    book = int(a.url.rsplit("/", 1)[1])
    for t in SHELF_EXTRA:
        try:
            await a.goto(f"{BASE}/open/wikisource/{t}")
            await a.wait_for_url(re.compile(r"/books/\d+"), timeout=60000)
        except Exception as e:  # 네트워크 사정으로 한 권 못 가져와도 계속
            print("skip shelf book", t, type(e).__name__)
    await a.goto(f"{BASE}/library", wait_until="networkidle")
    lib_boxes = {"mine": f'.grid a[href="/books/{mine}"]', "grid": "#panel-reading"}
    await snap(a, "library", wait=1500, boxes=lib_boxes)
    await a.click("#tab-mine")
    await snap(a, "library-mine", wait=600)

    # --- 대결 열기 → 초대 코드
    await a.request.post(f"{BASE}/races", form={"book_id": book, "days": 7, "csrf": ta})
    await a.goto(f"{BASE}/races", wait_until="networkidle")
    href = await a.get_attribute('a[href^="/races/"]', "href")
    room = int(href.rsplit("/", 1)[1])
    await a.goto(f"{BASE}{href}", wait_until="networkidle")
    code = (await a.text_content("#code")).strip()
    print("book", book, "mine", mine, "race", room, "code", code)
    await snap(a, "race-new", wait=1200)
    await a.locator(".invite").scroll_into_view_if_needed()
    await a.evaluate("document.querySelector('.invite').scrollIntoView({block: 'center'})")
    await snap(a, "invite", wait=700, boxes={"code": "#code", "card": ".invite", "share": "#share"})

    # --- 하린: 코드 입력 → 참가 화면
    B = await br.new_context(**rs.PHONE)
    b = await rs.login(B, friend_email)
    tb = await rs.csrf(b)
    await set_look(b, tb, *LOOKS[0])
    await b.goto(f"{BASE}/races", wait_until="networkidle")
    await b.wait_for_timeout(800)
    async with Cast(b, "codetype", {"code": "#code", "card": ".code-card"}) as c:
        await b.click("#code")
        await b.wait_for_timeout(300)
        for i, ch in enumerate(code):
            c.mark(f"key{i}")
            await b.keyboard.type(ch)
            await b.wait_for_timeout(325)  # 186 BPM 한 박
        await b.wait_for_timeout(600)
    await snap(b, "codetyped", boxes={"code": "#code", "go": ".code-row .btn", "card": ".code-card"})
    await b.click(".code-row .btn")
    await b.wait_for_url(re.compile(r"/join/"))
    await snap(b, "join", wait=1200, boxes={"go": "form .btn.primary", "hero": ".race-hero"})

    # 지우의 대결 화면: 하린이 들어오는 순간(5초 폴링/웹소켓)
    await a.goto(f"{BASE}{href}", wait_until="networkidle")
    await a.wait_for_timeout(1200)
    async with Cast(a, "joined", {"track": "#raceTrack", "hero": ".race-hero"}) as c:
        await a.wait_for_timeout(500)
        c.mark("join")
        await b.click("form .btn.primary")
        await b.wait_for_url(re.compile(r"/races/\d+"))
        for _ in range(14):
            await a.wait_for_timeout(500)
            if await a.locator(".rc-lane").count() >= 2:
                c.mark("shown")
                break
        await a.wait_for_timeout(1800)
    await snap(a, "race-joined", boxes={"track": "#raceTrack", "hero": ".race-hero"})

    # 민준도 조용히 참가
    C = await br.new_context(**rs.PHONE)
    cpage = await rs.login(C, third_email)
    tc = await rs.csrf(cpage)
    await cpage.request.post(f"{BASE}/join/{code}", form={"csrf": tc})

    # --- 같은 쪽: 지우가 리더를 펴고, 하린이 같은 쪽에 들어온다
    await read_range(a, book, 0, 1, ta)
    await read_range(cpage, book, 0, 1, tc)
    await a.goto(f"{BASE}/books/{book}", wait_until="networkidle")
    await a.wait_for_timeout(2500)
    chunk = (await stage_page(a))["start"]
    await snap(a, "reader-race")

    async def friend_beat():
        while True:
            await api(b, "PUT", f"/api/v1/books/{book}/presence", tb, {"chunk_index": chunk})
            await asyncio.sleep(4)

    async with Cast(a, "friendpop", {"here": ".pz-here", "friends": "#friends", "foot": "#foot"}) as c:
        await a.wait_for_timeout(600)
        c.mark("beat")
        beat = asyncio.create_task(friend_beat())
        for _ in range(30):
            await a.wait_for_timeout(300)
            if await a.locator(".pz-here, .pz-face, #friends *").count():
                c.mark("shown")
                break
        await a.wait_for_timeout(2200)
    await snap(a, "presence", boxes=PRESENCE_BOXES)
    await snap(a, "presence-2", wait=1500)

    # --- 밑줄 친 문장 공유
    said = await select_sentence(a)
    await a.wait_for_timeout(900)
    print("selected:", said)
    await snap(a, "quote-select", boxes={"sel": "::selection", "share": ".pz-share"})
    async with Cast(a, "quote", {"toast": ".pz-toast", "toasts": "#toasts"}) as c:
        await a.wait_for_timeout(300)
        share = a.locator(".pz-share").first
        if await share.count():
            c.mark("tap")
            await share.click()
        await a.wait_for_timeout(1600)
    await snap(a, "quote-toast", boxes={"toast": ".pz-toast", "toasts": "#toasts", "foot": "#foot"})
    beat.cancel()

    # --- 레이스: 하린이 앞서 있다가 지우가 역전
    await read_range(b, book, 0, 5, tb)  # 하린 6쪽
    await read_range(a, book, 0, 1, ta)  # 지우 2쪽 → 3 · 4 · 5 · 7쪽으로 따라잡는다
    await a.goto(f"{BASE}{href}", wait_until="networkidle")
    await a.wait_for_timeout(1800)
    await a.evaluate("document.getElementById('raceTrack').scrollIntoView({block: 'center'})")
    await snap(a, "race-before", wait=800, boxes=RACE_BOXES)
    async with Cast(a, "race", RACE_BOXES) as c:
        # 한 걸음(쪽)마다 화면이 따라올 때까지 기다린다(대결 화면은 5초마다 순위를 새로 받는다)
        pct = "() => document.querySelector('.rc-lane.me')?.textContent.replace(/\\s+/g, ' ')"
        await a.wait_for_timeout(400)
        done = 1
        for step, upto in enumerate([2, 3, 4, 6]):
            before = await a.evaluate(pct)
            await read_range(a, book, done + 1, upto, ta)
            done = upto
            for _ in range(80):
                await a.wait_for_timeout(100)
                if await a.evaluate(pct) != before:
                    c.mark(f"step{step}")
                    break
        if await a.locator(".rc-lane.me.lead").count():
            c.mark("lead")
        await a.wait_for_timeout(1800)
    await snap(a, "race-after", boxes=RACE_BOXES)
    await a.evaluate("window.scrollTo(0, 0)")
    await snap(a, "race-top", wait=500, boxes={"track": "#raceTrack", "hero": ".race-hero"})

    # --- 화면 꾸미기 몽타주
    for i, look in enumerate(LOOKS):
        await set_look(a, ta, *look)
        await a.goto(f"{BASE}/", wait_until="networkidle")
        await snap(a, f"look{i}-home", wait=1800)
        await a.goto(f"{BASE}/books/{book}", wait_until="networkidle")
        await snap(a, f"look{i}-reader", wait=2200)
    await set_look(a, ta, *LOOKS[0])

    (SHOTS / "boxes.json").write_text(json.dumps(BOXES, ensure_ascii=False, indent=1) + "\n")
    for ctx in (A, B, C, G):
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
