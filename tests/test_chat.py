"""친구 맺기 + 채팅(개인 · 방) — 스포일러 잠금, 접근 권한, 차단, 속도 제한, 삭제 · 신고, 안 읽은 수."""

import re
from urllib.parse import unquote

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Book, LibraryEntry, Room, RoomMember, User
from app.models_chat import ChatReport
from tests.conftest import signup
from tests.test_api import upstream

SECRET = "범인은 집사였어요"


def token_of(html: str) -> str:
    return re.search(r'<meta name="csrf" content="([^"]+)"', html).group(1)


async def login_as(api, email: str, nickname: str):
    c = api(upstream)
    await signup(c, email=email, nickname=nickname)
    c.csrf = token_of((await c.get("/")).text)
    return c


async def uid(session_factory, email: str) -> int:
    async with session_factory() as s:
        return await s.scalar(select(User.id).where(User.email == email))


async def make_room(session_factory, owner: int, members: list[int], title: str = "긴 책") -> tuple[int, int]:
    async with session_factory() as s:
        book = Book(source="wikisource", external_id=title, title=title, total_chunks=20, has_fulltext=True)
        s.add(book)
        await s.flush()
        room = Room(book_id=book.id, owner_id=owner, name=title, invite_code=f"C{book.id:05d}")
        s.add(room)
        await s.flush()
        for m in {owner, *members}:
            s.add(RoomMember(room_id=room.id, user_id=m))
        await s.commit()
        return room.id, book.id


async def set_verified(session_factory, user_id: int, book_id: int, chunk: int) -> None:
    async with session_factory() as s:
        e = await s.scalar(
            select(LibraryEntry).where(LibraryEntry.user_id == user_id, LibraryEntry.book_id == book_id)
        )
        if e is None:
            e = LibraryEntry(user_id=user_id, book_id=book_id)
            s.add(e)
        e.verified_chunk = chunk
        await s.commit()


async def post(c, url: str, **body):
    return await c.post(url, json=body, headers={"X-CSRF-Token": c.csrf})


async def friend_action(c, other: int, action: str):
    return await c.post(f"/friends/{other}/{action}", data={"csrf": c.csrf})


async def befriend(a, b, b_id: int, a_id: int) -> None:
    assert (await friend_action(a, b_id, "request")).status_code == 303
    assert (await friend_action(b, a_id, "accept")).status_code == 303


@pytest.fixture
async def trio(api, session_factory):
    """같은 방에 있는 세 사람: 방장 하린(5쪽까지 읽음), 느린 민준(1쪽), 빠른 서연(10쪽)."""
    h = await login_as(api, "h@example.com", "하린")
    m = await login_as(api, "m@example.com", "민준")
    s = await login_as(api, "s@example.com", "서연")
    ids = [await uid(session_factory, e) for e in ("h@example.com", "m@example.com", "s@example.com")]
    room_id, book_id = await make_room(session_factory, ids[0], ids[1:])
    for user_id, chunk in zip(ids, (4, 0, 9), strict=True):
        await set_verified(session_factory, user_id, book_id, chunk)
    yield {"h": h, "m": m, "s": s, "ids": ids, "room": room_id, "book": book_id}
    for c in (h, m, s):
        await c.aclose()


# ---------------------------------------------------------------- 방 채팅: 스포일러 잠금


async def test_sealed_room_message_hides_body_from_slower_reader(trio, session_factory):
    h, m, s, room = trio["h"], trio["m"], trio["s"], trio["room"]
    url = f"/api/v1/chat/room/{room}/messages"
    sent = await post(h, url, text=SECRET, mode="book")
    assert sent.status_code == 201, sent.text
    assert sent.json()["page"] == 5 and sent.json()["body"] == SECRET  # 보낸 사람(5쪽까지 읽음)의 도장

    slow = await m.get(url)
    assert SECRET not in slow.text  # JSON 어디에도 본문이 없다
    item = slow.json()["items"][0]
    assert item["sealed"] is True and item["page"] == 5 and "body" not in item
    assert item["nickname"] == "하린" and item["at"]

    fast = await s.get(url)
    assert fast.json()["items"][0]["body"] == SECRET
    assert (await h.get(url)).json()["items"][0]["body"] == SECRET  # 보낸 사람은 늘 본다

    # 대화 화면 HTML에도 본문이 없다
    page = await m.get(f"/chat/room/{room}")
    assert page.status_code == 200 and SECRET not in page.text

    # 민준이 5쪽까지 검증받으면 다음 폴링이 본문을 준다
    first = slow.json()
    await set_verified(session_factory, trio["ids"][1], trio["book"], 4)
    poll = await m.get(url, params={"after": first["last_id"], "locked": str(item["id"])})
    assert poll.json()["items"] == []
    assert poll.json()["updates"][0]["body"] == SECRET


async def test_chat_mode_message_is_visible_to_everyone(trio):
    h, m, room = trio["h"], trio["m"], trio["room"]
    url = f"/api/v1/chat/room/{room}/messages"
    sent = await post(h, url, text="오늘 저녁 뭐 먹어요?", mode="chat")
    assert sent.status_code == 201 and "page" not in sent.json() and sent.json()["kind"] == "chat"
    item = (await m.get(url)).json()["items"][0]
    assert item["body"] == "오늘 저녁 뭐 먹어요?" and "sealed" not in item


async def test_stamp_comes_from_server_not_client(trio):
    h, m, room = trio["h"], trio["m"], trio["room"]
    url = f"/api/v1/chat/room/{room}/messages"
    sent = await post(h, url, text=SECRET, mode="book", page=1, chunk_index=0, book_id=999)
    assert sent.json()["page"] == 5  # 클라이언트가 보낸 1쪽이 아니라 서버의 검증 쪽
    assert SECRET not in (await m.get(url)).text


async def test_non_member_cannot_read_or_write_room_chat(trio, api):
    outsider = await login_as(api, "x@example.com", "낯선이")
    url = f"/api/v1/chat/room/{trio['room']}/messages"
    await post(trio["h"], url, text="안녕", mode="chat")
    assert (await outsider.get(url)).status_code == 404
    assert (await post(outsider, url, text="끼어들기", mode="chat")).status_code == 404
    assert (await outsider.get(f"/chat/room/{trio['room']}")).status_code == 404
    assert (await outsider.get("/api/v1/chat/room/99999/messages")).status_code == 404
    await outsider.aclose()


async def test_blocked_users_room_messages_are_hidden_from_blocker(trio):
    h, m, room = trio["h"], trio["m"], trio["room"]
    url = f"/api/v1/chat/room/{room}/messages"
    await post(m, url, text="민준의 말", mode="chat")
    assert (await friend_action(h, trio["ids"][1], "block")).status_code == 303
    seen = await h.get(url)
    assert "민준의 말" not in seen.text and seen.json()["last_id"] > 0
    # 차단당한 사람은 신청도 못 보낸다
    r = await friend_action(m, trio["ids"][0], "request")
    assert "err=" in r.headers["location"]


async def test_body_is_trimmed_and_length_checked(trio):
    url = f"/api/v1/chat/room/{trio['room']}/messages"
    assert (await post(trio["h"], url, text="   ", mode="chat")).status_code == 422
    assert (await post(trio["h"], url, text="가" * 501, mode="chat")).status_code == 422
    ok = await post(trio["h"], url, text="  " + "가" * 500 + "  ", mode="chat")
    assert ok.status_code == 201 and ok.json()["body"] == "가" * 500


async def test_send_requires_csrf(trio):
    url = f"/api/v1/chat/room/{trio['room']}/messages"
    r = await trio["h"].post(url, json={"text": "안녕", "mode": "chat"})
    assert r.status_code == 403


async def test_send_rate_limit(trio):
    url = f"/api/v1/chat/room/{trio['room']}/messages"
    codes = [(await post(trio["h"], url, text=f"{i}", mode="chat")).status_code for i in range(21)]
    assert codes[:20] == [201] * 20 and codes[20] == 429


async def test_delete_own_message_and_owner_removal(trio):
    h, m, s, room = trio["h"], trio["m"], trio["s"], trio["room"]
    url = f"/api/v1/chat/room/{room}/messages"
    mine = (await post(m, url, text="실수로 보냄", mode="chat")).json()["id"]
    theirs = (await post(s, url, text="서연의 말", mode="chat")).json()["id"]
    before = (await m.get(url)).json()

    assert (await m.delete(f"/api/v1/chat/messages/{theirs}", headers={"X-CSRF-Token": m.csrf})).status_code == 404
    assert (await m.delete(f"/api/v1/chat/messages/{mine}", headers={"X-CSRF-Token": m.csrf})).status_code == 204
    # 방장은 방 안의 남의 메시지를 지울 수 있다
    assert (await h.delete(f"/api/v1/chat/messages/{theirs}", headers={"X-CSRF-Token": h.csrf})).status_code == 204

    items = (await s.get(url)).json()["items"]
    assert all(i.get("deleted") and "body" not in i for i in items)
    assert "실수로 보냄" not in (await s.get(url)).text
    poll = await m.get(url, params={"after": before["last_id"], "deleted_since": before["now"]})
    assert sorted(poll.json()["deleted"]) == sorted([mine, theirs])


async def test_report_creates_row(trio, session_factory):
    h, m, room = trio["h"], trio["m"], trio["room"]
    url = f"/api/v1/chat/room/{room}/messages"
    mid = (await post(h, url, text="나쁜 말", mode="chat")).json()["id"]
    r = await post(m, f"/api/v1/chat/messages/{mid}/report", reason="욕설")
    assert r.status_code == 201
    again = await post(m, f"/api/v1/chat/messages/{mid}/report", reason="욕설")
    assert again.status_code == 201
    assert (await post(h, f"/api/v1/chat/messages/{mid}/report")).status_code == 422  # 내 메시지
    async with session_factory() as s:
        rows = list(await s.scalars(select(ChatReport)))
    assert len(rows) == 1 and rows[0].reason == "욕설" and rows[0].message_id == mid


async def test_admin_sees_reports(trio, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "h@example.com")
    url = f"/api/v1/chat/room/{trio['room']}/messages"
    mid = (await post(trio["s"], url, text="신고될 말", mode="chat")).json()["id"]
    await post(trio["m"], f"/api/v1/chat/messages/{mid}/report", reason="광고")
    page = await trio["h"].get("/admin/chat-reports")
    assert page.status_code == 200 and "신고될 말" in page.text and "광고" in page.text
    assert (await trio["m"].get("/admin/chat-reports")).status_code == 404
    hide = await trio["h"].post(f"/admin/chat-reports/{mid}/hide", data={"csrf": trio["h"].csrf})
    assert hide.status_code == 303
    assert (await trio["m"].get(url)).json()["items"][0]["deleted"] is True


# ---------------------------------------------------------------- 대화 목록 · 안 읽은 수


async def test_conversation_list_never_leaks_sealed_text(trio):
    h, m, room = trio["h"], trio["m"], trio["room"]
    await post(h, f"/api/v1/chat/room/{room}/messages", text=SECRET, mode="book")
    page = await m.get("/chat", params={"tab": "room"})
    assert page.status_code == 200
    assert SECRET not in page.text and "잠긴 메시지" in page.text
    assert SECRET in (await trio["s"].get("/chat", params={"tab": "room"})).text  # 다 읽은 친구에겐 미리보기


async def test_unread_counts_and_menu_dot(trio):
    h, m, room = trio["h"], trio["m"], trio["room"]
    url = f"/api/v1/chat/room/{room}/messages"
    assert 'data-chat-unread' not in (await m.get("/me")).text
    for t in ("하나", "둘"):
        await post(h, url, text=t, mode="chat")
    listing = (await m.get("/chat", params={"tab": "room"})).text
    assert re.search(r'data-unread="2"', listing)
    assert 'data-chat-unread' in (await m.get("/me")).text  # 메뉴의 점
    assert 'data-chat-unread' not in (await h.get("/me")).text  # 내가 보낸 건 안 읽은 수가 아니다
    await m.get(url)  # 대화를 열면 읽음
    assert 'data-unread="2"' not in (await m.get("/chat", params={"tab": "room"})).text
    assert 'data-chat-unread' not in (await m.get("/me")).text


# ---------------------------------------------------------------- 친구 · 개인 채팅


async def test_friend_request_lifecycle(api, session_factory):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    a_id, b_id = await uid(session_factory, "a@example.com"), await uid(session_factory, "b@example.com")

    found = await a.get("/friends", params={"q": "나"})
    assert f'action="/friends/{b_id}/request"' in found.text
    assert (await friend_action(a, b_id, "request")).headers["location"].startswith("/friends?msg=")
    dup = await friend_action(a, b_id, "request")
    assert "err=" in dup.headers["location"]  # 같은 신청을 두 번
    assert "나" in (await a.get("/friends")).text
    assert f'action="/friends/{a_id}/accept"' in (await b.get("/friends")).text

    # 취소하면 사라지고, 다시 보낸 뒤 상대가 거절할 수 있다
    await friend_action(a, b_id, "cancel")
    assert f'action="/friends/{a_id}/accept"' not in (await b.get("/friends")).text
    await friend_action(a, b_id, "request")
    await friend_action(b, a_id, "decline")
    assert f"/friends/{b_id}/cancel" not in (await a.get("/friends")).text

    # 반대 방향 신청 = 수락
    await friend_action(a, b_id, "request")
    r = await friend_action(b, a_id, "request")
    assert r.headers["location"].startswith("/friends?msg=")
    assert f'href="/chat/dm/{a_id}"' in (await b.get("/friends")).text

    # 수락한 사람이 아니면 accept 는 404
    assert (await friend_action(a, b_id, "accept")).status_code == 404
    await friend_action(a, b_id, "remove")
    assert f'href="/chat/dm/{b_id}"' not in (await a.get("/friends")).text
    for c in (a, b):
        await c.aclose()


async def test_roommates_are_suggested(trio):
    page = await trio["m"].get("/friends")
    assert f'action="/friends/{trio["ids"][2]}/request"' in page.text and "서연" in page.text


async def test_dm_only_between_accepted_friends(api, session_factory):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    a_id, b_id = await uid(session_factory, "a@example.com"), await uid(session_factory, "b@example.com")
    url = f"/api/v1/chat/dm/{b_id}/messages"
    assert (await post(a, url, text="안녕")).status_code == 404
    await friend_action(a, b_id, "request")
    assert (await post(a, url, text="안녕")).status_code == 404  # 신청만으로는 안 된다
    await friend_action(b, a_id, "accept")
    sent = await post(a, url, text="스포 조심", veiled=True, page=3, mode="book")
    assert sent.status_code == 201 and "page" not in sent.json()  # 개인 채팅에는 도장이 없다
    got = (await b.get(f"/api/v1/chat/dm/{a_id}/messages")).json()["items"][0]
    assert got["veiled"] is True and got["body"] == "스포 조심"  # 가리기는 예의일 뿐 본문은 온다
    listing = await b.get("/chat")
    assert "스포 조심" not in listing.text and "잠긴 메시지" in listing.text

    # 차단하면 대화도 끝난다(양쪽 모두)
    await friend_action(b, a_id, "block")
    assert (await post(a, f"/api/v1/chat/dm/{b_id}/messages", text="왜?")).status_code == 404
    assert (await b.get(f"/api/v1/chat/dm/{a_id}/messages")).status_code == 404
    assert "err=" in (await friend_action(a, b_id, "request")).headers["location"]
    # 차단당한 사람의 검색에는 차단한 사람이 나오지 않는다
    assert f"/friends/{b_id}/request" not in (await a.get("/friends", params={"q": "나"})).text
    await friend_action(b, a_id, "unblock")
    assert "err=" not in (await friend_action(a, b_id, "request")).headers["location"]
    for c in (a, b):
        await c.aclose()


async def test_friend_request_rate_limit(api, session_factory):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    b_id = await uid(session_factory, "b@example.com")
    for _ in range(20):  # 신청 · 취소를 되풀이해도 신청 횟수는 쌓인다
        assert "err=" not in (await friend_action(a, b_id, "request")).headers["location"]
        await friend_action(a, b_id, "cancel")
    r = await friend_action(a, b_id, "request")
    assert "너무" in unquote(r.headers["location"])
    for c in (a, b):
        await c.aclose()


# ---------------------------------------------------------------- 기능 끄기


async def test_flag_off_hides_everything(trio, monkeypatch):
    monkeypatch.setattr(get_settings(), "feature_chat", False)
    h, room = trio["h"], trio["room"]
    assert (await h.get("/chat")).status_code == 404
    assert (await h.get("/friends")).status_code == 404
    assert (await h.get(f"/api/v1/chat/room/{room}/messages")).status_code == 404
    assert (await post(h, f"/api/v1/chat/room/{room}/messages", text="x", mode="chat")).status_code == 404
    me = (await h.get("/me")).text
    assert 'href="/chat"' not in me and 'href="/friends"' not in me
    assert f'href="/chat/room/{room}"' not in (await h.get(f"/races/{room}")).text


async def test_entry_points_when_on(trio):
    h, room = trio["h"], trio["room"]
    me = (await h.get("/me")).text
    assert me.count('href="/chat"') == 1 and me.count('href="/friends"') == 1
    assert f'href="/chat/room/{room}"' in (await h.get(f"/races/{room}")).text
