"""아래 막대 · 왼쪽 레일 — 4칸(홈 · 탐색 · 커뮤니티 · 서재), 켜진 칸, 서재(내 책 + 대결) 합친 화면,
커뮤니티 칸의 배지(안 읽은 메시지 99+) · 점(친구 새 글), 기능 끄기."""

import re

import pytest

from app import chat as chat_pages
from app.core.config import get_settings
from app.nav import _seen, badge_text
from app.services import posts as svc
from tests.conftest import signup
from tests.test_api import upstream
from tests.test_posts import done, pid_of, post, three


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    s = get_settings()
    for name in ("feature_posts", "feature_chat", "feature_board"):
        monkeypatch.setattr(s, name, True)


def nav_of(html: str) -> str:
    return html.split('<nav class="tabbar"', 1)[1].split("</nav>", 1)[0]


def keys(html: str) -> list[str]:
    return re.findall(r'data-nav="([a-z]+)"', nav_of(html))


def on_keys(html: str) -> list[str]:
    return re.findall(r'class="on" data-nav="([a-z]+)"', nav_of(html))


def pick(i: int = 1) -> str:
    return svc.sign_pick(svc.BookPick("store", str(i), f"책{i}"))


async def test_exactly_four_tabs_on_the_phone(api):
    async with api(upstream) as c:
        await signup(c)
        html = (await c.get("/library")).text
        assert keys(html) == ["home", "discover", "community", "library", "me"]
        nav = nav_of(html)
        labels = re.findall(r"<span>([^<]+)</span></a>|<span>([^<]+)</span><i", nav)
        assert [a or b for a, b in labels] == ["홈", "탐색", "커뮤니티", "서재", "나"]
        assert "--tabs:4" in nav  # 막대는 4칸(나는 레일에만)
        assert 'href="/community"' in nav and 'href="/library"' in nav
        assert "피드" not in nav and "메시지" not in nav and 'href="/chat"' not in nav  # 메시지는 커뮤니티 안
        assert 'href="/posts/new"' not in nav and "만들기" not in nav  # 만들기는 메뉴에 없다
        assert 'href="/races"' not in nav  # 대결 칸은 서재로 합쳤다
        assert 'class="top-me' in html and 'href="/profile"' in html.split('class="top-me', 1)[1][:80]


async def test_active_tab_follows_the_page(api, session_factory):
    async with api(upstream) as c:
        await signup(c)
        pid = pid_of(await post(c, pick()))
        for path, key in [
            ("/", "home"),
            ("/discover", "discover"),
            ("/library", "library"),
            ("/races", "library"),
            ("/library?tab=races", "library"),
            ("/community", "community"),
            ("/community/messages", "community"),
            ("/community/together", "community"),
            ("/together/new", "community"),
            ("/friends", "community"),
            (f"/p/{pid}", "community"),
            ("/posts/new", "community"),
            ("/profile", "me"),
        ]:
            html = (await c.get(path)).text
            assert on_keys(html) == [key], path
            assert 'aria-current="page"' in nav_of(html), path


async def test_library_and_races_render_the_merged_shelf(api):
    async with api(upstream) as c:
        await signup(c)
        books = (await c.get("/library")).text
        races = (await c.get("/races")).text
        races_q = (await c.get("/library?tab=races")).text
        for html in (books, races, races_q):
            assert '<h1 class="top-title">서재</h1>' in html
            assert 'id="shelf-books"' in html and 'id="shelf-races"' in html  # 두 갈래가 한 화면에
            # 예전 두 화면의 내용 그대로
            assert 'data-panel="reading"' in html and 'data-panel="newRace"' in html
        assert 'data-seg="books" aria-controls="shelf-books" aria-selected="true"' in books
        assert 'id="shelf-races" role="tabpanel" aria-labelledby="shelf-tab-races" hidden' in books
        for html in (races, races_q):
            assert 'data-seg="races" aria-controls="shelf-races" aria-selected="true"' in html
            assert 'id="shelf-books" role="tabpanel" aria-labelledby="shelf-tab-books" hidden' in html
        assert "<title>서재 — " in books and "<title>대결 · 서재 — " in races
        rank_hidden = re.compile(r'class="rl-rank"[^>]*hidden')
        assert rank_hidden.search(books) and not rank_hidden.search(races)
        err = (await c.get("/races", params={"error": "코드를 찾을 수 없어요"})).text
        assert "코드를 찾을 수 없어요" in err and 'aria-selected="true">' in err


def test_badge_text_caps_at_99():
    assert [badge_text(n) for n in (1, 99, 100, 250)] == ["1", "99", "99+", "99+"]


async def test_message_badge_shows_99_plus(api, monkeypatch):
    async with api(upstream) as c:
        await signup(c)
        monkeypatch.setattr(chat_pages, "chat_unread", lambda request, user: 250)
        nav = nav_of((await c.get("/library")).text)
        assert 'aria-label="커뮤니티, 안 읽은 메시지 250개"' in nav
        assert re.search(r'<i class="nv-badge" data-chat-unread aria-hidden="true">99\+</i>', nav)


async def test_flags_off_hide_feed_and_messages(api, monkeypatch):
    async with api(upstream) as c:
        await signup(c)
        monkeypatch.setattr(get_settings(), "feature_posts", False)
        html = (await c.get("/library")).text
        assert keys(html) == ["home", "discover", "community", "library", "me"]
        assert 'href="/me"' in nav_of(html) and 'href="/community/messages"' in nav_of(html)  # 첫 갈래로
        monkeypatch.setattr(get_settings(), "feature_chat", False)
        html = (await c.get("/library")).text
        assert 'href="/community/together"' in nav_of(html)
        monkeypatch.setattr(get_settings(), "feature_board", False)
        html = (await c.get("/library")).text
        assert keys(html) == ["home", "discover", "library", "me"] and "--tabs:3" in nav_of(html)
        assert "커뮤니티" not in nav_of(html)


async def test_feed_dot_when_a_friend_posts_after_my_last_visit(api, session_factory):
    (a, f, x), _ = await three(api, session_factory)  # 하린(a) · 민준(f)은 친구, 서연(x)은 모르는 사람
    async with done(a, f, x):
        assert "data-feed-dot" not in nav_of((await a.get("/library")).text)
        await post(x, pick(1))  # 친구가 아닌 사람의 글은 점을 켜지 않는다
        assert "data-feed-dot" not in nav_of((await a.get("/library")).text)
        await post(f, pick(2), visibility="friends")
        nav = nav_of((await a.get("/library")).text)
        assert "data-feed-dot" in nav and "친구의 새 게시물이 있어요" in nav
        assert "data-feed-dot" not in nav_of((await f.get("/library")).text)  # 내 글은 내게 점이 아니다
        r = await a.get("/community")
        assert "feed_seen=" in r.headers.get("set-cookie", "")
        assert "data-feed-dot" not in nav_of(r.text)
        assert "data-feed-dot" not in nav_of((await a.get("/library")).text)  # 피드를 봤으니 꺼진다
        await post(f, pick(3))
        assert "data-feed-dot" in nav_of((await a.get("/")).text)


def test_seen_cookie_belongs_to_one_person():
    assert _seen("7.42", 7) == 42
    assert _seen("8.42", 7) == 0  # 같은 브라우저의 다른 사람 쿠키
    assert _seen("x", 7) == 0 and _seen(None, 7) == 0 and _seen("7.", 7) == 0
