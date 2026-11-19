"""라운드 8 — 피드 → 커뮤니티. 막대 4칸(홈 · 탐색 · 커뮤니티 · 서재), 커뮤니티 안의 갈래(피드 · 메시지 · 같이 읽기),
예전 주소(/feed · /chat · /together)는 308 로 넘기거나 같은 내용을 커뮤니티 머리 아래에 그린다.
안 읽은 메시지 수는 커뮤니티 칸과 메시지 갈래에. 따로 만드는 칸 · 머리 단추는 없다(tests/test_feed.py)."""

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
    return html.split('<nav class="cm-tabs"', 1)[1].split("</nav>", 1)[0]


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
        assert 'data-cm="messages" aria-current="page"' in dm.text
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
            tabs = sub(html)
            assert [k for k in ("feed", "messages", "together") if f'data-cm="{k}"' in tabs] == [
                "feed",
                "messages",
                "together",
            ]
            assert f'data-cm="{key}" aria-current="page"' in tabs and tabs.count("aria-current") == 1, path
            assert 'class="on" data-nav="community"' in tabbar(html), path
            assert 'class="top-me' in html and 'id="menuBtn"' in html  # 앱의 나 · 메뉴는 그대로
        # 피드 안의 거르기는 한 단계 아래(작은 알약)
        html = (await a.get("/community?tab=friends")).text
        assert '<nav class="ps-filter" aria-label="피드 거르기">' in html
        assert 'href="/community?tab=friends" aria-current="page">친구<' in html
        assert "ps-ig-tabs ps-feed-tabs" not in html


async def test_unread_on_community_tab_and_messages_section(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        html = (await a.get("/community")).text
        assert "data-chat-unread" not in tabbar(html) and "cm-badge" not in sub(html)
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
        tabs = sub((await a.get("/community")).text)
        assert 'aria-label="메시지, 안 읽은 메시지 2개"' in tabs and '<i class="cm-badge" aria-hidden="true">2</i>' in tabs


async def test_flags_shape_the_sections(api, session_factory, monkeypatch):
    (a, f, x), _ = await three(api, session_factory)
    async with done(a, f, x):
        monkeypatch.setattr(get_settings(), "feature_board", False)
        tabs = sub((await a.get("/community")).text)
        assert 'data-cm="together"' not in tabs and 'data-cm="messages"' in tabs
        assert (await a.get("/community/together")).status_code == 404
        monkeypatch.setattr(get_settings(), "feature_posts", False)
        assert (await a.get("/community")).status_code == 404
        html = (await a.get("/community/messages")).text
        assert 'data-nav="community"' in tabbar(html) and 'href="/community/messages"' in tabbar(html)
        assert '<nav class="cm-tabs"' not in html  # 갈래가 하나뿐이면 줄을 그리지 않는다
        monkeypatch.setattr(get_settings(), "feature_chat", False)
        assert (await a.get("/community/messages")).status_code == 404
        assert 'data-nav="community"' not in tabbar((await a.get("/library")).text)
