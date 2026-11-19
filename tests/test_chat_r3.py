"""채팅 3차 — 커뮤니티 안의 '메시지' 갈래(안 읽은 수), 대화 화면 셸, 개인 채팅 머리의 상태 한 줄(공개 범위)."""

import datetime as dt

from app.core.config import get_settings
from app.models import Book, LibraryEntry, Shelf
from app.services import chat
from tests.test_chat import befriend, login_as, post, trio, uid  # noqa: F401


async def test_messages_live_in_community_with_unread_count(trio):  # noqa: F811
    h, m = trio["h"], trio["m"]
    page = (await m.get("/me")).text
    nav = page.split('<nav class="tabbar"')[1].split("</nav>")[0]
    assert "<span>메시지</span>" not in nav and "<span>커뮤니티</span>" in nav and "--tabs:4" in nav
    menu = page.split('id="menu"')[1]
    assert "채팅" not in menu.split("</nav>")[0]  # 햄버거 메뉴의 중복 항목은 없앴다
    await post(h, f"/api/v1/chat/room/{trio['room']}/messages", text="안녕", channel="chat")
    page = (await m.get("/community/messages")).text
    nav = page.split('<nav class="tabbar"')[1].split("</nav>")[0]
    assert "data-chat-unread" in nav and "커뮤니티, 안 읽은 메시지 1개" in nav  # 커뮤니티 칸의 숫자
    sub = page.split('<nav class="cm-tabs"')[1].split("</nav>")[0]
    assert 'data-cm="messages" aria-current="page" aria-label="메시지, 안 읽은 메시지 1개"' in sub
    assert '<i class="cm-badge" aria-hidden="true">1</i>' in sub


async def test_flag_off_removes_tab(trio, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "feature_chat", False)
    nav = (await trio["h"].get("/me")).text.split('<nav class="tabbar"')[1].split("</nav>")[0]
    assert "메시지" not in nav and "--tabs:6" not in nav


async def test_thread_is_an_app_shell_without_page_title(trio):  # noqa: F811
    html = (await trio["h"].get(f"/chat/room/{trio['room']}")).text
    assert "chat-shell cm-chat chat-at-thread" in html
    assert '<h1 class="top-title">커뮤니티</h1>' in html and 'data-cm="messages" aria-current="page"' in html
    assert 'class="ch-thead"' in html and 'class="ch-compose"' in html
    assert 'class="ch-backdrop' in html  # 방은 책 표지 배경(아주 옅게)


async def test_dm_header_status_never_shows_private_upload(api, session_factory):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    a_id, b_id = await uid(session_factory, "a@example.com"), await uid(session_factory, "b@example.com")
    await befriend(a, b, b_id, a_id)
    async with session_factory() as s:
        up = Book(
            source="upload",
            external_id="u9",
            title="비밀 원고",
            owner_id=b_id,
            has_fulltext=True,
            total_chunks=5,
        )
        s.add(up)
        await s.flush()
        s.add(
            LibraryEntry(
                user_id=b_id, book_id=up.id, shelf=Shelf.READING, current_chunk=2, updated_at=chat.clock()
            )
        )
        await s.commit()
    html = (await a.get(f"/chat/dm/{b_id}")).text
    assert "비밀 원고" not in html and "지금 읽는 중" in html
    async with session_factory() as s:
        pub = Book(source="wikisource", external_id="p9", title="소나기", has_fulltext=True, total_chunks=10)
        s.add(pub)
        await s.flush()
        s.add(
            LibraryEntry(
                user_id=b_id,
                book_id=pub.id,
                shelf=Shelf.READING,
                current_chunk=6,
                updated_at=chat.clock() + dt.timedelta(seconds=5),
            )
        )
        await s.commit()
    assert "지금 읽는 중 · 소나기 7쪽" in (await a.get(f"/chat/dm/{b_id}")).text
    for c in (a, b):
        await c.aclose()
