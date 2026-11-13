"""초대 링크: 로그인 전 공개 미리보기(OG), 가입 → 인증 → 초대방으로 돌아오기, 첫 방문 안내 카드."""

import re

from tests.conftest import SENT_CODES, csrf_of, signup
from tests.test_api import upstream


def csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


async def make_room(host) -> tuple[str, str]:
    """호스트가 대결방을 만들고 (초대 코드, 대결 주소)를 돌려준다."""
    await signup(host, email="host@example.com", nickname="호스트")
    page = await host.get("/races")
    r = await host.post("/races/start/wikisource/긴글", data={"csrf": csrf(page.text), "days": "3"})
    race = await host.get(r.headers["location"])
    code = re.search(r'id="code">([A-Z0-9]{6})<', race.text).group(1)
    return code, r.headers["location"]


async def test_logged_out_preview_is_public_with_og_tags(api):
    async with api(upstream) as host, api(upstream) as visitor:
        code, _ = await make_room(host)
        r = await visitor.get(f"/join/{code}")
    assert r.status_code == 200
    html = r.text
    assert "호스트님이 「긴글」 같이 읽기에 초대했어요" in html
    assert re.search(r'property="og:title" content="호스트님이 「긴글」 같이 읽기에 초대했어요"', html)
    assert re.search(r'property="og:description" content="1명이 함께 읽는 중', html)
    assert f'property="og:url" content="http://test/join/{code}"' in html
    assert 'property="og:image" content="http://test/og/default.png"' in html
    assert '<meta name="robots" content="noindex, nofollow">' in html  # 공개지만 색인은 안 한다
    assert f'href="/signup?next=/join/{code}"' in html and f'href="/login?next=/join/{code}"' in html
    assert "같이 읽기" in html


async def test_unknown_code_preview_is_404_but_renders(api):
    async with api(upstream) as c:
        r = await c.get("/join/NOPE00")
    assert r.status_code == 404 and "초대를 찾을 수 없어요" in r.text


async def test_logged_in_member_still_redirects_to_room(api):
    async with api(upstream) as host:
        code, race_url = await make_room(host)
        r = await host.get(f"/join/{code}")
    assert r.status_code == 303 and r.headers["location"] == race_url


async def test_next_survives_signup_verify_and_first_run_card_shows(api):
    async with api(upstream) as host, api(upstream) as friend:
        code, _ = await make_room(host)
        target = f"/join/{code}"
        page = await friend.get("/signup", params={"next": target})
        token = csrf(page.text)
        r = await friend.post(
            "/signup",
            data={
                "nickname": "친구",
                "email": "friend@example.com",
                "password": "password123",
                "password_confirm": "password123",
                "csrf": token,
            },
        )
        assert r.status_code == 303 and r.headers["location"] == "/signup/verify"
        r = await friend.post("/signup/verify", data={"code": SENT_CODES["friend@example.com"], "csrf": token})
        assert r.status_code == 303 and r.headers["location"] == target  # 초대방으로 돌아온다
        preview = await friend.get(target)
        assert preview.status_code == 200 and "호스트님의 초대" in preview.text  # 로그인 상태의 참가 화면

        home = await friend.get("/")
        assert 'id="welcome"' in home.text and "친구님, 반가워요" in home.text
        assert 'href="/discover"' in home.text and 'href="/races"' in home.text and "같이 읽기" in home.text
        r = await friend.post("/welcome/dismiss", data={"csrf": csrf(home.text)})
        assert r.status_code == 303
        assert 'id="welcome"' not in (await friend.get("/")).text


async def test_next_accepts_only_relative_paths(api):
    async with api(upstream) as c:
        for bad in ("https://evil.com/", "//evil.com", "/\\evil.com"):
            page = await c.get("/signup", params={"next": bad})
            token = await csrf_of(c)
            await c.post(
                "/signup",
                data={
                    "nickname": "독자",
                    "email": "reader@example.com",
                    "password": "password123",
                    "password_confirm": "password123",
                    "csrf": token,
                },
            )
            r = await c.post("/signup/verify", data={"code": SENT_CODES["reader@example.com"], "csrf": token})
            assert r.status_code == 303 and r.headers["location"] == "/", (bad, page.status_code)
            await c.post("/logout", data={"csrf": csrf((await c.get("/")).text)})
            break  # 한 이메일은 한 번만 가입할 수 있다 — 나머지는 _safe_next 단위 검사로

    from app.web import _safe_next

    assert _safe_next("https://evil.com/") == "/" and _safe_next("//evil.com") == "/"
    assert _safe_next("/\\evil.com") == "/" and _safe_next("/join/ABC123") == "/join/ABC123"


async def test_login_page_keeps_next_from_preview(api):
    async with api(upstream) as host, api(upstream) as friend:
        code, race_url = await make_room(host)
        await signup(friend, email="friend@example.com", nickname="친구")
        await friend.post("/logout", data={"csrf": csrf((await friend.get("/")).text)})
        page = await friend.get("/login", params={"next": f"/join/{code}"})
        r = await friend.post(
            "/login",
            data={"email": "friend@example.com", "password": "password123", "next": f"/join/{code}", "csrf": csrf(page.text)},
        )
    assert r.status_code == 303 and r.headers["location"] == f"/join/{code}"
