"""README 스크린샷 — 실제 UI를 Playwright로 조작하거나 시드 스크립트로 채운 DB에서 390x844(DPR 2)로 찍는다.

한 번에 다 하려면 scripts/readme_shots.sh (docs/README-assets.md 참고). 단계별로는:

    python scripts/readme_shots.py --seed "$DATABASE_URL"   # 계정 세 개 심기
    python scripts/readme_shots.py <png 폴더>               # 이야기: 가입·리더·반응·관리자 (BASE=앱 주소)
    python scripts/readme_shots.py --world <png 폴더>       # 시드 세계: 하린 시점 화면들 (BASE=앱 주소)
    python scripts/readme_shots.py --jpeg <png 폴더>        # docs/images/ 아래 JPG로(Pillow)

앱은 빈 SQLite + DWELL_SCALE=0.01 + ADMIN_EMAILS=admin@haedok.app 으로 떠 있어야 한다.
이야기 쪽은 책 본문을 위키문헌에서 실제로 가져오므로 네트워크가 필요하다.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

BASE = os.environ.get("BASE", "http://127.0.0.1:8077")
DESKTOP = dict(viewport={"width": 1440, "height": 900}, locale="ko-KR", color_scheme="dark")
PHONE = dict(
    viewport={"width": 390, "height": 844},
    device_scale_factor=2,
    is_mobile=True,
    has_touch=True,
    locale="ko-KR",
    color_scheme="dark",
)
ACCOUNTS = [("지우", "admin@haedok.app"), ("하린", "harin@example.com"), ("민준", "minjun@example.com")]
PASSWORD = "password123"
# 따로 받은 크로미움(예: chrome-headless-shell)을 쓰려면 CHROME=/path/to/chrome
CHROME = os.environ.get("CHROME") or None
# readme_photos.py 가 그린 사진 폴더(올리기 화면 촬영에 쓴다). 없으면 그 화면은 건너뛴다
PHOTOS = Path(os.environ["PHOTOS"]) if os.environ.get("PHOTOS") else None


async def seed_users(db_url: str) -> None:
    """스크린샷에 나올 계정 세 개를 바로 DB에 넣는다 (가입 메일 없이)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app import auth
    from app.models import User

    engine = create_async_engine(db_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        for nick, email in ACCOUNTS:
            s.add(User(nickname=nick, email=email, password_hash=auth.hash_password(PASSWORD)))
        await s.commit()
    await engine.dispose()
    print("seeded", [e for _, e in ACCOUNTS])


async def login(ctx, email):
    """단계형 로그인 폼: 이메일 → Enter → 비밀번호 → Enter."""
    p = await ctx.new_page()
    await p.goto(f"{BASE}/login")
    await p.fill('input[name="email"]', email)
    await p.keyboard.press("Enter")
    await p.wait_for_selector('input[name="password"]', state="visible")
    await p.fill('input[name="password"]', PASSWORD)
    await p.keyboard.press("Enter")
    await p.wait_for_url(f"{BASE}/")
    return p


async def csrf(p):
    await p.goto(f"{BASE}/races")
    return await p.get_attribute('meta[name="csrf"]', "content")


def _json(token):
    return {"X-CSRF-Token": token, "Content-Type": "application/json"}


async def read(p, book, upto, token):
    """0쪽부터 upto쪽까지 펼치고(verified=False) 잠깐 뒤 다 읽음(verified=True)을 보낸다."""
    for i in range(upto + 1):
        for v in (False, True):
            r = await p.request.put(
                f"{BASE}/api/v1/books/{book}/progress",
                data={"chunk_index": i, "verified": v},
                headers=_json(token),
            )
            assert r.ok, (i, v, r.status, await r.text())
            if not v:
                await asyncio.sleep(0.35)


async def react(p, book, chunk, token, emoji=None, comment=None, anchor=0):
    r = await p.request.post(
        f"{BASE}/api/v1/books/{book}/reactions",
        data={"chunk_index": chunk, "anchor": anchor, "emoji": emoji, "comment": comment},
        headers=_json(token),
    )
    assert r.ok, (chunk, r.status, await r.text())


async def shot(p, out, url, name, wait=1800):
    await p.goto(f"{BASE}{url}", wait_until="domcontentloaded")
    await p.wait_for_timeout(wait)
    await p.screenshot(path=str(out / f"{name}.png"))
    print("shot", name)


async def main(out: Path) -> None:
    from playwright.async_api import async_playwright

    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        br = await pw.chromium.launch(executable_path=CHROME)

        # --- 시드: 지우가 책을 열고 대결을 연다
        A = await br.new_context(**PHONE)
        a = await login(A, ACCOUNTS[0][1])
        await a.goto(f"{BASE}/open/wikisource/운수 좋은 날")
        await a.wait_for_url(re.compile(r"/books/\d+"))
        book = int(a.url.rsplit("/", 1)[1])
        ta = await csrf(a)
        await a.request.post(f"{BASE}/races", form={"book_id": book, "days": 7, "csrf": ta})
        await a.goto(f"{BASE}/races")
        href = await a.get_attribute('a[href^="/races/"]', "href")
        await a.goto(f"{BASE}{href}")
        code = re.search(r"/join/([A-Za-z0-9-]+)", await a.content()).group(1)
        print("book", book, "race", href, "code", code)

        # --- 하린·민준 참가, 각자 읽고 반응
        B = await br.new_context(**PHONE)
        b = await login(B, ACCOUNTS[1][1])
        tb = await csrf(b)
        await b.request.post(f"{BASE}/join/{code}", form={"csrf": tb})
        C = await br.new_context(**PHONE)
        c = await login(C, ACCOUNTS[2][1])
        tc = await csrf(c)
        await c.request.post(f"{BASE}/join/{code}", form={"csrf": tc})
        await read(b, book, 5, tb)
        await react(b, book, 0, tb, emoji="😢")
        await react(b, book, 1, tb, comment="김첨지 말투가 너무 생생해요")
        await react(b, book, 4, tb, emoji="😮", anchor=1)
        await read(c, book, 2, tc)
        await react(c, book, 0, tc, comment="첫 문장부터 비 냄새가 나요")
        await read(a, book, 3, ta)
        await react(a, book, 2, ta, emoji="👏")

        # --- 로그아웃 화면: 랜딩 · 가입 · 6자리 인증
        G = await br.new_context(**PHONE)
        g = await G.new_page()
        await shot(g, out, "/", "landing", wait=2500)
        await shot(g, out, "/signup", "signup")
        for name, val in [
            ("nickname", "서연"),
            ("email", "seoyeon@example.com"),
            ("password", PASSWORD),
            ("password_confirm", PASSWORD),
        ]:
            await g.wait_for_selector(f'input[name="{name}"]', state="visible")
            await g.fill(f'input[name="{name}"]', val)
            await g.keyboard.press("Enter")
        await g.wait_for_url(f"{BASE}/signup/verify")
        await g.wait_for_timeout(600)
        await g.click('input[name="code"]')
        await g.keyboard.type("4821", delay=140)  # 6칸 중 4칸만 채운 모습
        await g.wait_for_timeout(500)
        await g.screenshot(path=str(out / "verify.png"))
        print("shot verify")

        # --- 로그인 폼 1단계(이메일만 채운 모습)
        await g.goto(f"{BASE}/login", wait_until="domcontentloaded")
        await g.fill('input[name="email"]', "seoyeon@example.com")
        await g.wait_for_timeout(1500)
        await g.screenshot(path=str(out / "login.png"))
        print("shot login")

        # --- 로그인 화면 (지우 시점) — 홈·대결·탐색·내 정보는 world()가 더 풍성한 하린 계정으로 찍는다
        await shot(a, out, f"/books/{book}", "reader", wait=3500)
        await a.click("p[data-a]")  # 문단을 누르면 반응 시트
        await a.wait_for_selector("#react:not([hidden])")
        await a.wait_for_timeout(800)
        await a.screenshot(path=str(out / "react.png"))
        print("shot react")
        await shot(a, out, "/store/20628514", "store", wait=2500)  # 서점 상세(데미안)

        # --- 관리자
        await shot(a, out, "/admin", "admin-dashboard", wait=1200)
        await shot(a, out, "/admin/users", "admin-users", wait=1200)
        await shot(a, out, "/admin/rooms", "admin-rooms", wait=1200)
        await shot(a, out, "/admin/mail", "admin-mail", wait=1200)

        # --- PC(1440×900): 펼친 두 쪽 리더
        D = await br.new_context(**DESKTOP)
        d = await login(D, ACCOUNTS[0][1])
        await shot(d, out, f"/books/{book}", "pc-reader", wait=3500)
        await br.close()


async def world(out: Path) -> None:
    """시드 스크립트(seed_board → seed_races → seed_chat → seed_goals → seed_mileage → seed_posts)로
    채운 DB에서 하린 시점의 화면을 찍는다. 네트워크 없이 심지만, 표지 이미지는 바깥에서 불러온다."""
    from playwright.async_api import async_playwright

    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        br = await pw.chromium.launch(executable_path=CHROME)

        # --- 로그인 전: 공개 페이지
        G = await br.new_context(**PHONE)
        g = await G.new_page()
        for url, name in (("/guide", "guide"), ("/docs", "docs"), ("/developers", "developers")):
            await shot(g, out, url, name, wait=1500)

        # --- 하린(친구 · 대결 · 모집 · 채팅 · 목표 · 마일리지가 다 있는 계정)
        H = await br.new_context(**PHONE)
        h = await login(H, ACCOUNTS[1][1])
        await shot(h, out, "/", "w-home", wait=3000)
        await shot(h, out, "/me", "w-me", wait=1500)
        await shot(h, out, "/races", "w-races", wait=1500)
        race = await h.get_attribute('a[href^="/races/"]', "href")
        await shot(h, out, race, "w-race", wait=2500)
        await shot(h, out, "/together", "w-together", wait=1500)
        await shot(h, out, "/friends", "w-friends", wait=1500)
        await shot(h, out, "/chat", "w-chat", wait=1500)
        room = race.replace("/races/", "/chat/room/")  # 맨 위 대결방 = seed_chat이 대화를 채운 방
        dm = await h.get_attribute('a[href^="/chat/dm/"]', "href")
        await shot(h, out, room, "w-chat-room", wait=2500)
        await shot(h, out, dm, "w-chat-dm", wait=2500)
        await shot(h, out, "/mileage", "w-mileage", wait=1500)
        await shot(h, out, "/discover", "w-discover", wait=2500)
        await shot(h, out, "/leaderboard", "w-leaderboard", wait=1500)

        # --- 게시물(seed_posts → seed_posts_ig): 친구 소식 · 프로필 격자 · 게시물과 댓글 · 올리기 1단계
        await shot(h, out, "/feed", "w-feed", wait=2000)
        # 하린의 「데미안」 — 사진 세 장, 댓글 · 답글
        card = await h.evaluate(
            "[...document.querySelectorAll('article[id^=post-]')]"
            ".find(a => a.textContent.includes('데미안')).id"
        )
        post = "/p/" + card.removeprefix("post-")
        # 손가락으로 한 장 넘긴 모습(2/3) — 휴대폰에서는 화살표 대신 밀어서 넘긴다
        await h.eval_on_selector(f"#{card} [data-car-track]", "t => t.scrollBy({left: t.clientWidth})")
        await h.eval_on_selector(f"#{card}", "a => a.scrollIntoView({block: 'start'})")
        await h.wait_for_timeout(1600)
        await h.screenshot(path=str(out / "w-feed.png"))
        print("shot w-feed (2/3)")
        # 댓글 시트(답글 펼침)
        await h.click(f"#{card} [data-comments].ps-all-cm")
        await h.wait_for_selector("#psComments [data-cm-wrap]")
        await h.click("#psComments .ps-replies summary")
        await h.wait_for_timeout(900)
        await h.screenshot(path=str(out / "w-comments.png"))
        print("shot w-comments")
        await shot(h, out, "/profile", "w-profile", wait=2000)
        await shot(h, out, post, "w-post", wait=2000)
        await shot(h, out, "/feed?tab=all", "w-explore-posts", wait=2000)
        if PHOTOS:
            await h.goto(f"{BASE}/posts/new", wait_until="domcontentloaded")
            await h.wait_for_timeout(800)
            await h.set_input_files("#psFiles", [str(PHOTOS / f"{n}.jpg") for n in ("desk", "page", "cafe")])
            await h.wait_for_selector(".ps-th")
            await h.wait_for_timeout(1200)
            await h.screenshot(path=str(out / "w-create.png"))
            print("shot w-create")

        # --- 관리자(지우): 리워드 교환 관리
        A = await br.new_context(**PHONE)
        a = await login(A, ACCOUNTS[0][1])
        await shot(a, out, "/admin/rewards", "admin-rewards", wait=1200)

        # --- PC(1440×900): 홈 · 방 채팅 · 대결
        D = await br.new_context(**DESKTOP)
        d = await login(D, ACCOUNTS[1][1])
        await shot(d, out, "/", "pc-home", wait=3000)
        await shot(d, out, room, "pc-chat", wait=2500)
        await shot(d, out, race, "pc-race", wait=2500)
        await shot(d, out, post, "pc-post", wait=2500)
        await br.close()


# PNG 이름 → docs/images/ 아래 JPG 이름. 숫자는 README에 나오는 순서다.
JPEG_NAMES = {
    "landing": "app/0-landing",
    "login": "app/1-login",
    "signup": "app/1-signup",
    "verify": "app/1b-verify",
    "w-home": "app/2-home",
    "w-discover": "app/3-explore",
    "store": "app/4-store",
    "reader": "app/5-reader",
    "react": "app/6-react",
    "w-race": "app/7-race",
    "w-me": "app/8-me",
    "pc-home": "app/9-pc-home",
    "pc-reader": "app/10-pc-reader",
    "pc-chat": "app/11-pc-chat",
    "w-races": "app/12-races",
    "w-together": "app/13-together",
    "w-friends": "app/14-friends",
    "w-chat": "app/15-chat",
    "w-chat-room": "app/16-chat-room",
    "w-chat-dm": "app/17-chat-dm",
    "w-mileage": "app/18-mileage",
    "w-leaderboard": "app/19-leaderboard",
    "guide": "app/20-guide",
    "docs": "app/21-docs",
    "developers": "app/22-developers",
    "pc-race": "app/23-pc-race",
    "w-feed": "app/24-feed",
    "w-profile": "app/25-profile",
    "w-post": "app/26-post",
    "w-comments": "app/27-comments",
    "w-create": "app/28-create",
    "w-explore-posts": "app/29-explore-posts",
    "pc-post": "app/30-pc-post",
    "admin-dashboard": "admin/1-dashboard",
    "admin-users": "admin/2-users",
    "admin-rooms": "admin/3-rooms",
    "admin-mail": "admin/4-mail",
    "admin-rewards": "admin/5-rewards",
}


def to_jpeg(src: Path, dst: Path = Path("docs/images"), limit: int = 250_000) -> None:
    """PNG를 JPEG로 — 품질 88에서 시작해 limit 바이트 이하가 될 때까지 4씩 낮춘다(Pillow 필요)."""
    import io

    from PIL import Image

    for a, b in JPEG_NAMES.items():
        if not (src / f"{a}.png").exists():
            print("skip", a)
            continue
        im = Image.open(src / f"{a}.png").convert("RGB")
        for q in range(88, 40, -4):
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
            if buf.tell() <= limit:
                break
        (dst / f"{b}.jpg").parent.mkdir(parents=True, exist_ok=True)
        (dst / f"{b}.jpg").write_bytes(buf.getvalue())
        print(b, "q", q, buf.tell() // 1024, "KB")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--seed":
        asyncio.run(seed_users(sys.argv[2]))
    elif len(sys.argv) > 2 and sys.argv[1] == "--jpeg":
        to_jpeg(Path(sys.argv[2]))
    elif len(sys.argv) > 2 and sys.argv[1] == "--world":
        asyncio.run(world(Path(sys.argv[2])))
    else:
        asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "readme-shots")))
