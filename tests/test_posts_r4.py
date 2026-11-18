"""라운드 4 — '나' 탭이 내 프로필(/profile)을 연다. 설정은 톱니 뒤의 /me 그대로. 그림 두 번 탭 = 좋아요."""

import re

import pytest

from app.core.config import get_settings
from app.services import posts as svc
from tests.conftest import signup
from tests.test_api import upstream
from tests.test_posts import done, pid_of, post, relate, three, token, uid


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    s = get_settings()
    for name in ("feature_posts", "feature_goals", "feature_mileage", "feature_chat"):
        monkeypatch.setattr(s, name, True)


def tab_me(html: str) -> str:
    """하단 메뉴의 '나' 칸 하나."""
    nav = html.split('class="tabbar"', 1)[1].split("</nav>", 1)[0]
    return re.search(r'<a href="([^"]+)" class="([^"]*)"[^>]*>(?:(?!</a>).)*<span>나</span>', nav, re.S).group(0)


async def test_profile_requires_login(api):
    async with api(upstream) as c:
        r = await c.get("/profile")
        assert r.status_code == 303 and r.headers["location"].startswith("/login")


async def test_profile_shows_my_grid_and_self_buttons(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        empty = (await c.get("/profile")).text
        assert "아직 올린 게시물이 없어요" in empty and 'class="ps-tile"' not in empty
        ids = [pid_of(await post(c, svc.sign_pick(svc.BookPick("store", str(i), f"책{i}")))) for i in range(3)]
        html = (await c.get("/profile")).text
        assert [int(x) for x in re.findall(r'class="ps-tile" href="/p/(\d+)"', html)] == sorted(ids, reverse=True)
        assert 'data-count="posts">3<' in html
        assert 'href="/posts/new"' in html and "게시물 올리기" in html
        assert re.search(r'<a class="btn ghost" href="/me">.*?설정', html, re.S)  # 톱니 → /me
        assert 'aria-label="프로필 사진 바꾸기"' in html  # 사진을 누르면 /me 로
        assert 'href="/friends"' in html  # 친구 수 → 친구 화면(채팅 기능이 켜져 있을 때)
        me = await uid(session_factory, "harin@example.com")
        assert (await c.get(f"/u/{me}")).text.count('class="ps-tile"') == 3  # /u/{내 id} 도 같은 내 프로필


async def test_nav_me_points_to_profile_and_stays_highlighted(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        me = await uid(session_factory, "harin@example.com")
        for path in ("/profile", "/me", f"/u/{me}", "/profile?tab=feed"):
            a = tab_me((await c.get(path)).text)
            assert 'href="/profile"' in a and 'class="on"' in a, path
        a = tab_me((await c.get("/feed")).text)
        assert 'href="/profile"' in a and 'class="on"' not in a


async def test_nav_me_is_me_when_posts_off(api, monkeypatch):
    async with api(upstream) as c:
        await signup(c)
        monkeypatch.setattr(get_settings(), "feature_posts", False)
        html = (await c.get("/me")).text
        a = tab_me(html)
        assert 'href="/me"' in a and 'class="on"' in a
        assert 'href="/profile"' not in html
        assert (await c.get("/profile")).status_code == 404


async def test_me_still_renders_settings(api):
    async with api(upstream) as c:
        await signup(c)
        html = (await c.get("/me")).text
        for marker in ('id="profileForm"', 'action="/me/profile"', 'class="settings"', 'action="/logout"', 'id="account"'):
            assert marker in html, marker


async def test_double_tap_like_is_idempotent(api, session_factory):
    (a, f, _x), _ = await three(api, session_factory)
    async with done(a, f):
        pid = pid_of(await post(a, svc.sign_pick(svc.BookPick("store", "1", "책"))))
        h = {"Accept": "application/json"}
        t = await token(f)
        for _ in range(3):  # 몇 번을 두 번 탭해도 한 번
            r = await f.post(f"/p/{pid}/like", data={"csrf": t, "want": "1"}, headers=h)
            assert r.json() == {"liked": True, "count": 1}
        r = await f.post(f"/p/{pid}/like", data={"csrf": t}, headers=h)  # 하트 버튼은 그대로 끄고 켠다
        assert r.json() == {"liked": False, "count": 0}
        r = await f.post(f"/p/{pid}/like", data={"csrf": t, "want": "1"}, headers=h)
        assert r.json() == {"liked": True, "count": 1}
        html = (await f.get("/feed")).text
        assert "data-dbl-like" in html and "좋아요 <b data-like-count>1</b>개" in html


@pytest.mark.parametrize(
    ("off", "gone", "kept"),
    [
        ("feature_goals", ("t-goal", "t-grass"), ("t-mileage",)),
        ("feature_mileage", ("t-mileage",), ("t-goal", "t-grass")),
    ],
)
async def test_tiles_follow_their_flags(api, monkeypatch, off, gone, kept):
    async with api(upstream) as c:
        await signup(c)
        on = (await c.get("/profile")).text
        assert all(k in on for k in ("t-goal", "t-grass", "t-mileage"))
        monkeypatch.setattr(get_settings(), off, False)
        html = (await c.get("/profile")).text
        assert not any(g in html for g in gone) and all(k in html for k in kept)


async def test_no_tiles_row_when_all_off_and_none_on_others(api, session_factory, monkeypatch):
    (a, f, _x), (ia, _if, _ix) = await three(api, session_factory)
    async with done(a, f):
        assert "ps-tiles" not in (await f.get(f"/u/{ia}")).text  # 남의 프로필엔 작은 칸 없음
        monkeypatch.setattr(get_settings(), "feature_goals", False)
        monkeypatch.setattr(get_settings(), "feature_mileage", False)
        assert "ps-tiles" not in (await a.get("/profile")).text


async def test_profile_feed_tab_inline_and_only_for_me(api, session_factory):
    (a, f, _x), (ia, i_f, _ix) = await three(api, session_factory)
    async with done(a, f):
        pid = pid_of(await post(f, svc.sign_pick(svc.BookPick("store", "1", "친구의 책"))))
        html = (await a.get("/profile?tab=feed")).text
        assert f'id="post-{pid}"' in html and 'class="ps-tile"' not in html
        other = (await a.get(f"/u/{i_f}?tab=feed")).text  # 남의 프로필은 늘 격자
        assert f'href="/p/{pid}"' in other and f'id="post-{pid}"' not in other and "?tab=feed" not in other
    assert ia


async def test_friend_and_stranger_buttons(api, session_factory):
    (a, f, x), (ia, _if, ix) = await three(api, session_factory)
    async with done(a, f, x):
        friend = (await f.get(f"/u/{ia}")).text
        assert f'href="/chat/dm/{ia}"' in friend and "게시물 올리기" not in friend
        stranger = (await x.get(f"/u/{ia}")).text
        assert f'action="/friends/{ia}/request"' in stranger and "설정" not in stranger.split("ps-ig-btns")[1][:400]
        await relate(session_factory, ia, ix, status="blocked", blocker=ia)
        assert (await x.get(f"/u/{ia}")).status_code == 404
