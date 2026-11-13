"""README 스크린샷 — 실제 UI를 Playwright로 조작해 시드 데이터를 만들고 390x844(DPR 2)로 찍는다.

사용법은 docs/README-assets.md 참고. 요약:

    python scripts/readme_shots.py --seed "$DATABASE_URL"          # 계정 세 개 심기
    BASE=http://127.0.0.1:8077 python scripts/readme_shots.py <png 출력 폴더>

앱은 빈 SQLite + DWELL_SCALE=0.01 + ADMIN_EMAILS=admin@haedok.app 으로 떠 있어야 한다.
책 본문은 위키문헌에서 실제로 가져오므로 네트워크가 필요하다.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

BASE = os.environ.get("BASE", "http://127.0.0.1:8077")
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
    await p.goto(f"{BASE}{url}", wait_until="networkidle")
    await p.wait_for_timeout(wait)
    await p.screenshot(path=str(out / f"{name}.png"))
    print("shot", name)


async def main(out: Path) -> None:
    from playwright.async_api import async_playwright

    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        br = await pw.chromium.launch()

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

        # --- 로그인 화면 (지우 시점)
        await shot(a, out, "/", "home", wait=3000)
        await shot(a, out, f"/books/{book}", "reader", wait=3500)
        await shot(a, out, href, "race", wait=2000)
        await shot(a, out, "/discover", "explore", wait=2000)
        await shot(a, out, "/me", "look", wait=1500)

        # --- 관리자
        await shot(a, out, "/admin", "admin-dashboard", wait=1200)
        await shot(a, out, "/admin/users", "admin-users", wait=1200)
        await shot(a, out, "/admin/rooms", "admin-rooms", wait=1200)
        await shot(a, out, "/admin/mail", "admin-mail", wait=1200)
        await br.close()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--seed":
        asyncio.run(seed_users(sys.argv[2]))
    else:
        asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "readme-shots")))
