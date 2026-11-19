"""라운드 8 · 9 — 피드 → 커뮤니티. 막대 4칸(홈 · 탐색 · 커뮤니티 · 서재), 커뮤니티 안의 갈래(피드 · 메시지 · 같이 읽기)는
위 막대(내려도 붙어 있는) 오른쪽의 아이콘 셋 — 글자 탭 줄은 없다.
예전 주소(/feed · /chat · /together)는 308 로 넘기거나 같은 내용을 커뮤니티 머리 아래에 그린다.
안 읽은 메시지 수는 커뮤니티 칸과 메시지 아이콘에. 따로 만드는 칸 · 설명 문장은 없다(tests/test_feed.py)."""

import re

import pytest

from app.core.config import get_settings
from app.models import User
from app.services import chat as chat_service
from tests.test_posts import done, three


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    s = get_settings()
    for name in ("feature_posts", "feature_chat", "feature_board"):
        monkeypatch.setattr(s, name, True)


def tabbar(html: str) -> str:
    return html.split('<nav class="tabbar"', 1)[1].split("</nav>", 1)[0]


def sub(html: str) -> str:
    """위 막대(<header class="top">) 안의 갈래 아이콘 줄."""
    top = html.split('<header class="top">', 1)[1].split("</header>", 1)[0]
    return top.split('<nav class="cm-nav"', 1)[1].split("</nav>", 1)[0]


def icons(html: str) -> dict[str, str]:
    """갈래 열쇠 → 그 아이콘 링크의 여는 태그."""
    return {m.group(2): m.group(0) for m in re.finditer(r'<a href="([^"]+)" data-cm="(\w+)"[^>]*>', sub(html))}


async def test_old_urls_redirect_keeping_the_query(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        for old, new in (
            ("/feed", "/community"),
            ("/feed?tab=friends&before=7", "/community?tab=friends&before=7"),
            ("/chat", "/community/messages"),
            ("/chat?tab=room", "/community/messages?tab=room"),
            ("/together", "/community/together"),
            ("/together?msg=hi", "/community/together?msg=hi"),
        ):
            r = await a.get(old)
            assert r.status_code == 308 and r.headers["location"] == new, old
        # 대화 · 모집 글 쓰기 주소는 그대로 동작한다(대화는 커뮤니티 머리 아래)
        dm = await a.get(f"/chat/dm/{i_f}")
        assert dm.status_code == 200 and '<h1 class="top-title">커뮤니티</h1>' in dm.text
        assert 'aria-current="page"' in icons(dm.text)["messages"]
        assert 'href="/community/messages?tab=dm" aria-label="메시지 목록으로"' in dm.text
        assert (await a.get("/together/new")).status_code == 200


async def test_sections_share_one_header_and_mark_the_active_tab(api, session_factory):
    (a, f, x), _ = await three(api, session_factory)
    async with done(a, f, x):
        for path, key in (
            ("/community", "feed"),
            ("/community/messages", "messages"),
            ("/community/together", "together"),
        ):
            html = (await a.get(path)).text
            assert '<h1 class="top-title">커뮤니티</h1>' in html, path
            got = icons(html)
            assert list(got) == ["feed", "messages", "together"], path
            assert [k for k, tag in got.items() if 'aria-current="page"' in tag] == [key], path
            for k, label in (("feed", "피드"), ("messages", "메시지"), ("together", "같이 읽기")):
                assert f'aria-label="{label}' in got[k] and f'data-tip="{label}"' in got[k] and "<svg" in sub(html)
            assert '<nav class="cm-tabs"' not in html  # 글자 탭 줄은 없앴다
            assert 'class="on" data-nav="community"' in tabbar(html), path
            assert 'class="top-me' in html and 'id="menuBtn"' in html  # 앱의 나 · 메뉴는 그대로
        # 피드 안의 거르기는 한 단계 아래(작은 알약)
        html = (await a.get("/community?tab=friends")).text
        assert '<nav class="ps-filter" aria-label="피드 거르기">' in html
        assert 'href="/community?tab=friends" aria-current="page">친구<' in html
        assert "ps-ig-tabs ps-feed-tabs" not in html
        assert "새로운 사람들의 공개 게시물" not in html and "ps-lede" not in html  # 설명 문장은 없앴다


async def test_unread_on_community_tab_and_messages_section(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        html = (await a.get("/community")).text
        assert "data-chat-unread" not in tabbar(html) and "cm-badge" not in sub(html)
        assert icons(html)["messages"].endswith('aria-label="메시지">')
        async with session_factory() as s:
            minjun = await s.get(User, i_f)
            conv = await chat_service.dm_conv(s, minjun, ia)
            await chat_service.send(s, minjun, conv, "이 책 같이 읽을래요?")
            await chat_service.send(s, minjun, conv, "답장 기다려요")
            await s.commit()
        for path in ("/community", "/community/together", "/library"):
            html = (await a.get(path)).text
            assert 'aria-label="커뮤니티, 안 읽은 메시지 2개"' in tabbar(html), path
            assert '<i class="nv-badge" data-chat-unread aria-hidden="true">2</i>' in tabbar(html), path
        html = (await a.get("/community")).text
        assert 'aria-label="메시지, 안 읽은 메시지 2개"' in icons(html)["messages"]
        assert '<i class="cm-badge" aria-hidden="true">2</i>' in sub(html)


async def test_flags_shape_the_sections(api, session_factory, monkeypatch):
    (a, f, x), _ = await three(api, session_factory)
    async with done(a, f, x):
        monkeypatch.setattr(get_settings(), "feature_board", False)
        assert list(icons((await a.get("/community")).text)) == ["feed", "messages"]
        assert (await a.get("/community/together")).status_code == 404
        monkeypatch.setattr(get_settings(), "feature_posts", False)
        assert (await a.get("/community")).status_code == 404
        html = (await a.get("/community/messages")).text
        assert 'data-nav="community"' in tabbar(html) and 'href="/community/messages"' in tabbar(html)
        assert '<nav class="cm-nav"' not in html  # 갈래가 하나뿐이면 아이콘을 그리지 않는다
        monkeypatch.setattr(get_settings(), "feature_chat", False)
        assert (await a.get("/community/messages")).status_code == 404
        assert 'data-nav="community"' not in tabbar((await a.get("/library")).text)


async def test_sticky_bar_assets_and_quiet_view_transitions(api, session_factory):
    (a, f, x), _ = await three(api, session_factory)
    async with done(a, f, x):
        for path, key in (("/community", "feed"), ("/community/together", "together")):
            html = (await a.get(path)).text
            head = html.split("</head>", 1)[0]
            assert '<link rel="stylesheet" href="/static/community.css">' in head  # 막대 스타일은 첫 화면 전에
            assert "pagereveal" in head and "pageswap" in head  # 건너뛴 화면 전환을 조용히 받는다
            assert f'data-cm="{key}"' in html.split("<body", 1)[1].split(">", 1)[0]
            assert 'src="/static/community.js"' in html and "cm-y:" in html  # 갈래마다 보던 자리
        # 커뮤니티가 아닌 화면은 그대로
        lib = (await a.get("/library")).text
        assert "community.css" not in lib and "community.js" not in lib
        # 같이 읽기: 설명 문단 대신 목록 머리의 작은 '모집 글 쓰기'
        tg = (await a.get("/community/together")).text
        assert "rl-lede" not in tg and 'class="tg-new" href="/together/new"' in tg
