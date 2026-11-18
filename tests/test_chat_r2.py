"""채팅 2차 — 채널이 정하는 도장, 반응(한 사람 하나), 답장 인용 잠금, 방장만 공지, 읽음, 친구 활동 공개 범위."""

import datetime as dt

from app.core.config import get_settings
from app.models import Book, LibraryEntry, Shelf, User
from app.models_chat import ChatMessage
from app.services import chat
from tests.test_chat import (
    SECRET,
    befriend,
    login_as,
    make_room,
    post,
    set_verified,
    trio,  # noqa: F401 — 픽스처
    uid,
)


def url_of(t) -> str:
    return f"/api/v1/chat/room/{t['room']}/messages"


async def react(c, mid: int, emoji: str | None):
    url = f"/api/v1/chat/messages/{mid}/reaction"
    if emoji is None:
        return await c.delete(url, headers={"X-CSRF-Token": c.csrf})
    return await c.put(url, json={"emoji": emoji}, headers={"X-CSRF-Token": c.csrf})


# ---------------------------------------------------------------- 채널이 도장을 정한다


async def test_channel_decides_stamp_and_filters(trio):  # noqa: F811
    h, m = trio["h"], trio["m"]
    url = url_of(trio)
    chat_msg = await post(h, url, text="수다예요", channel="chat", page=99, mode="book")
    assert chat_msg.status_code == 201 and "page" not in chat_msg.json()  # channel 이 mode 보다 먼저
    book_msg = await post(h, url, text=SECRET, channel="book", page=0)
    assert book_msg.json()["page"] == 5  # 클라이언트의 0이 아니라 서버의 검증 쪽

    book_view = await m.get(url, params={"channel": "book"})
    chat_view = await m.get(url, params={"channel": "chat"})
    assert [i["kind"] for i in book_view.json()["items"]] == ["book"]
    assert SECRET not in book_view.text
    assert [i["body"] for i in chat_view.json()["items"]] == ["수다예요"]
    assert SECRET not in chat_view.text


async def test_unread_is_counted_per_channel(trio):  # noqa: F811
    h, m = trio["h"], trio["m"]
    url = url_of(trio)
    await post(h, url, text="책 이야기", channel="book")
    await post(h, url, text="수다", channel="chat")
    seen = (await m.get(url, params={"channel": "chat"})).json()
    assert seen["other_unread"] == 1  # 수다를 봐도 책 이야기는 아직 안 읽음
    listing = (await m.get("/chat", params={"tab": "room"})).text
    assert 'data-unread="1"' in listing


# ---------------------------------------------------------------- 반응


async def test_reaction_one_per_user_per_message(trio):  # noqa: F811
    h, m, s = trio["h"], trio["m"], trio["s"]
    mid = (await post(h, url_of(trio), text="반응해 주세요", channel="chat")).json()["id"]
    assert (await react(m, mid, "❤️")).status_code == 200
    r = await react(m, mid, "😂")  # 바꾸면 덮어쓴다
    assert r.json()["reactions"] == [{"emoji": "😂", "count": 1, "mine": True}]
    await react(s, mid, "😂")
    got = (await h.get(url_of(trio), params={"channel": "chat"})).json()["items"][0]
    assert got["reactions"] == [{"emoji": "😂", "count": 2, "mine": False}]
    assert (await react(m, mid, None)).json()["reactions"] == [{"emoji": "😂", "count": 1, "mine": False}]
    assert (await react(m, mid, "🍕")).status_code == 422  # 앱의 반응 이모지만


async def test_cannot_react_to_sealed_message_and_sealed_shows_no_reactions(trio, api):  # noqa: F811
    h, m, s = trio["h"], trio["m"], trio["s"]
    mid = (await post(h, url_of(trio), text=SECRET, channel="book")).json()["id"]
    assert (await react(m, mid, "😮")).status_code == 422  # 민준은 아직 못 읽었다
    await react(s, mid, "😮")
    item = (await m.get(url_of(trio), params={"channel": "book"})).json()["items"][0]
    assert item["sealed"] is True and "reactions" not in item and "body" not in item
    outsider = await login_as(api, "x@example.com", "낯선이")
    assert (await react(outsider, mid, "😮")).status_code == 404
    await outsider.aclose()


async def test_reaction_change_reaches_other_readers_by_poll(trio):  # noqa: F811
    h, m = trio["h"], trio["m"]
    mid = (await post(h, url_of(trio), text="안녕", channel="chat")).json()["id"]
    first = (await m.get(url_of(trio), params={"channel": "chat"})).json()
    await react(h, mid, "👏")
    poll = await m.get(
        url_of(trio),
        params={"channel": "chat", "after": first["last_id"], "deleted_since": first["now"]},
    )
    assert poll.json()["updates"][0]["reactions"][0]["emoji"] == "👏"


# ---------------------------------------------------------------- 답장


async def test_reply_quotes_and_never_leaks_sealed_text(trio, session_factory):  # noqa: F811
    h, m, s = trio["h"], trio["m"], trio["s"]
    url = url_of(trio)
    secret = (await post(h, url, text=SECRET, channel="book")).json()["id"]
    # 덜 읽은 민준은 잠긴 메시지에 답장할 수 없다
    assert (await post(m, url, text="뭐라고요?", channel="book", reply_to=secret)).status_code == 422
    # 다른 채널의 메시지에도 답장할 수 없다
    assert (await post(s, url, text="수다로 답장", channel="chat", reply_to=secret)).status_code == 422

    reply = await post(s, url, text="맞아요 충격이었어요", channel="book", reply_to=secret)
    assert reply.status_code == 201 and reply.json()["reply"]["text"] == SECRET
    # 서연이 처음부터 다시 읽게 되어 검증 쪽이 내려가면, 내 답장은 보여도 인용한 원문은 잠긴다
    await set_verified(session_factory, trio["ids"][2], trio["book"], 0)
    mine = (await s.get(url, params={"channel": "book"})).json()["items"]
    quoted = [i for i in mine if i.get("reply")][0]["reply"]
    assert quoted["text"] == "잠긴 메시지" and quoted["locked"] is True
    assert SECRET not in (await s.get(url, params={"channel": "book"})).text


def test_quote_helper_hides_sealed_and_masks_spoilers():
    author = User(id=2, nickname="하린")
    sealed = ChatMessage(id=1, user_id=2, page=8, body=SECRET)
    q = chat._quote(sealed, author, me=3, reader_page=2)
    assert q["text"] == "잠긴 메시지" and SECRET not in str(q)
    spoiler = ChatMessage(id=2, user_id=2, page=None, body="결말은 ||범인이 집사||예요")
    assert chat._quote(spoiler, author, me=3, reader_page=None)["text"] == "결말은 [스포일러]예요"
    gone = ChatMessage(id=3, user_id=2, page=None, body=SECRET, deleted_at=dt.datetime.now(dt.UTC))
    assert chat._quote(gone, author, me=3, reader_page=None)["text"] == "삭제된 메시지"


# ---------------------------------------------------------------- 공지


async def test_notice_only_by_owner_and_shown_in_both_channels(trio, api):  # noqa: F811
    h, m, room = trio["h"], trio["m"], trio["room"]
    url = f"/api/v1/chat/room/{room}/notice"
    r = await m.put(url, json={"text": "내 공지"}, headers={"X-CSRF-Token": m.csrf})
    assert r.status_code == 403
    assert (await h.put(url, json={"text": "가" * 141}, headers={"X-CSRF-Token": h.csrf})).status_code == 422
    ok = await h.put(url, json={"text": "이번 주는 30쪽까지"}, headers={"X-CSRF-Token": h.csrf})
    assert ok.json()["notice"] == "이번 주는 30쪽까지"
    for ch in ("book", "chat"):
        assert (await m.get(url_of(trio), params={"channel": ch})).json()["notice"] == "이번 주는 30쪽까지"
        assert "이번 주는 30쪽까지" in (await m.get(f"/chat/room/{room}", params={"ch": ch})).text
    outsider = await login_as(api, "x@example.com", "낯선이")
    r = await outsider.put(url, json={"text": "x"}, headers={"X-CSRF-Token": outsider.csrf})
    assert r.status_code == 404
    await outsider.aclose()
    cleared = await h.put(url, json={"text": "  "}, headers={"X-CSRF-Token": h.csrf})
    assert cleared.json()["notice"] is None


# ---------------------------------------------------------------- 개인 채팅: 읽음


async def test_dm_seen_marker(api, session_factory):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    a_id, b_id = await uid(session_factory, "a@example.com"), await uid(session_factory, "b@example.com")
    await befriend(a, b, b_id, a_id)
    sent = (await post(a, f"/api/v1/chat/dm/{b_id}/messages", text="읽었어요?")).json()
    assert (await a.get(f"/api/v1/chat/dm/{b_id}/messages")).json()["seen_upto"] == 0
    await b.get(f"/api/v1/chat/dm/{a_id}/messages")
    assert (await a.get(f"/api/v1/chat/dm/{b_id}/messages")).json()["seen_upto"] == sent["id"]
    for c in (a, b):
        await c.aclose()


# ---------------------------------------------------------------- 친구가 읽는 책: 공개 범위


async def _shelve(session_factory, user_id: int, book: Book, minutes_ago: float, chunk: int = 3) -> None:
    async with session_factory() as s:
        s.add(book)
        await s.flush()
        s.add(
            LibraryEntry(
                user_id=user_id,
                book_id=book.id,
                shelf=Shelf.READING,
                current_chunk=chunk,
                updated_at=chat.clock() - dt.timedelta(minutes=minutes_ago),
            )
        )
        await s.commit()


async def test_friend_activity_hides_private_uploads_and_non_friends(api, session_factory):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    c = await login_as(api, "c@example.com", "다")
    a_id, b_id, c_id = [await uid(session_factory, f"{x}@example.com") for x in "abc"]
    await befriend(a, b, b_id, a_id)
    await make_room(session_factory, a_id, [c_id], title="함께 방")  # 다는 같은 방일 뿐 친구가 아니다

    await _shelve(
        session_factory,
        b_id,
        Book(
            source="upload",
            external_id="u1",
            title="나의 비밀 일기",
            owner_id=b_id,
            has_fulltext=True,
            total_chunks=9,
        ),
        2,
    )
    await _shelve(
        session_factory,
        c_id,
        Book(
            source="wikisource", external_id="pub-c", title="공개된 고전", has_fulltext=True, total_chunks=30
        ),
        1,
    )
    for page in ("/friends", "/chat"):
        html = (await a.get(page)).text
        assert "나의 비밀 일기" not in html  # 친구여도 개인 파일 제목은 절대 나오지 않는다
        assert "공개된 고전" not in html  # 친구가 아닌 사람의 책은 나오지 않는다
        assert "지금 읽는 중" in html  # 링은 보인다(무엇인지는 몰라도)

    await _shelve(
        session_factory,
        b_id,
        Book(
            source="wikisource", external_id="pub-b", title="운수 좋은 날", has_fulltext=True, total_chunks=24
        ),
        0.5,
        chunk=11,
    )
    html = (await a.get("/friends")).text
    assert "운수 좋은 날" in html and "12 / 24쪽" in html
    assert "운수 좋은 날" not in (await c.get("/friends")).text  # 친구가 아니면 안 보인다
    for x in (a, b, c):
        await x.aclose()


async def test_week_dots_follow_goals_flag(api, session_factory, monkeypatch):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    a_id, b_id = await uid(session_factory, "a@example.com"), await uid(session_factory, "b@example.com")
    await befriend(a, b, b_id, a_id)
    assert 'class="ch-week"' in (await a.get("/friends")).text
    monkeypatch.setattr(get_settings(), "feature_goals", False)
    assert 'class="ch-week"' not in (await a.get("/friends")).text
    for x in (a, b):
        await x.aclose()


async def test_suggestions_and_requests_name_the_shared_room(api, session_factory):
    a = await login_as(api, "a@example.com", "가")
    b = await login_as(api, "b@example.com", "나")
    a_id, b_id = await uid(session_factory, "a@example.com"), await uid(session_factory, "b@example.com")
    await make_room(session_factory, a_id, [b_id], title="데미안 함께")
    assert "‘데미안 함께’에 함께 있어요" in (await b.get("/friends")).text
    await b.post(f"/friends/{a_id}/request", data={"csrf": b.csrf})
    assert "함께 있는 방: 데미안 함께" in (await a.get("/friends")).text
    for x in (a, b):
        await x.aclose()


async def test_search_explains_not_found(trio):  # noqa: F811
    page = (await trio["h"].get("/friends", params={"q": "없는사람"})).text
    assert "‘없는사람’라는 닉네임은 없어요" in page


async def test_list_preview_masks_spoilers_in_room(trio):  # noqa: F811
    await post(trio["h"], url_of(trio), text="||범인은 집사|| 맞죠?", channel="chat")
    html = (await trio["m"].get("/chat", params={"tab": "room"})).text
    assert "범인은 집사" not in html and "[스포일러] 맞죠?" in html


async def test_conversation_pages_render_both_channels(trio):  # noqa: F811
    for ch in ("book", "chat"):
        r = await trio["m"].get(f"/chat/room/{trio['room']}", params={"ch": ch})
        assert r.status_code == 200 and f'data-channel="{ch}"' in r.text
    assert 'id="members"' in r.text and "검증된 쪽" in r.text
