"""대결 화면(/races) — 제목 · 할 일 두 개 · 내 방(상태 한 줄 · 안 읽음 점) · 끝난 방 접기."""

import datetime as dt
import re

from sqlalchemy import update

from app.core.config import get_settings
from app.models import Room
from tests.test_chat import login_as, make_room, post, set_verified, uid


def rows(html: str) -> list[tuple[str, str]]:
    """내 방 줄: (제목, 상태)."""
    return re.findall(r'<div class="rl-main"><b>([^<]+)</b><span>([^<]+)</span></div>', html)


async def test_title_actions_and_code_field(api):
    c = await login_as(api, "a@example.com", "가")
    page = (await c.get("/races")).text
    assert '<h1 class="top-title">대결</h1>' in page and 'class="rl-lede"' in page
    assert 'href="/leaderboard"' in page
    assert re.search(r'data-panel="newRace"[^>]*>방 만들기<', page)
    assert re.search(r'data-panel="joinCode"[^>]*>코드로 참가<', page)
    # 코드 칸은 누를 때만 열린다 — 코드가 틀려서 돌아오면 열린 채로
    assert re.search(r'<form class="rl-panel rl-code" id="joinCode"[^>]*hidden>', page)
    err = (await c.get("/races", params={"error": "코드를 찾을 수 없어요"})).text
    assert re.search(r'<form class="rl-panel rl-code" id="joinCode" method="post" action="/join">', err)
    assert "코드를 찾을 수 없어요" in err
    # 새 방 열기(기간 + 책 고르기)는 그대로 있다
    assert 'action="/races/start/' in page and 'id="days"' in page
    # 방이 없으면 처음 할 일을 알려 준다
    assert "아직 들어간 방이 없어요" in page and "‘방 만들기’에서 책을 고르면" in page
    await c.aclose()


async def test_room_rows_answer_where_am_i(api, session_factory):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    ia, ib = await uid(session_factory, "a@example.com"), await uid(session_factory, "b@example.com")
    group, book = await make_room(session_factory, ia, [ib], title="같이 책")
    solo, _ = await make_room(session_factory, ia, [], title="혼자 책")
    old, _ = await make_room(session_factory, ia, [ib], title="끝난 책")
    await set_verified(session_factory, ia, book, 11)  # 12쪽
    await set_verified(session_factory, ib, book, 2)
    async with session_factory() as s:
        await s.execute(
            update(Room).where(Room.id == old).values(ends_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
        )
        await s.commit()
    page = (await a.get("/races")).text
    active, done = page.split('class="rl-done"')
    assert ("같이 책", "2명 중 1위 · 12/20쪽") in rows(active)
    assert ("혼자 책", "혼자 읽는 중 · 친구를 불러 보세요") in rows(active)
    assert "끝난 책" not in active  # 끝난 방은 접어 둔다
    assert "끝난 방" in done and ("끝난 책", "끝남 · 2명 중 1위 · 0/20쪽") in rows(done)
    assert f'href="/races/{group}"' in active and f'href="/races/{solo}"' in active
    assert ("같이 책", "2명 중 2위 · 3/20쪽") in rows((await b.get("/races")).text)
    for c in (a, b):
        await c.aclose()


async def test_unread_dot_only_when_chat_is_on(api, session_factory, monkeypatch):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    ia, ib = await uid(session_factory, "a@example.com"), await uid(session_factory, "b@example.com")
    room, _ = await make_room(session_factory, ia, [ib])
    assert 'class="rl-dot"' not in (await b.get("/races")).text
    await post(a, f"/api/v1/chat/room/{room}/messages", text="안녕", mode="chat")
    assert 'class="rl-dot"' in (await b.get("/races")).text
    assert 'class="rl-dot"' not in (await a.get("/races")).text  # 내가 보낸 건 안 읽은 게 아니다
    monkeypatch.setattr(get_settings(), "feature_chat", False)
    assert 'class="rl-dot"' not in (await b.get("/races")).text
    for c in (a, b):
        await c.aclose()
