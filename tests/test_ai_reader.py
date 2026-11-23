"""읽는 중의 AI — 문단에서 묻기 · AI 노트 · 지난 이야기 · 등장인물 · 생각해 볼 질문. 가짜 AI 만 쓴다."""

from __future__ import annotations

import datetime as dt
import json
import re

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import Book, BookChunk, LibraryEntry, Room, RoomMember, Shelf, User
from app.models_ai import AIBookCache, AINote
from app.services import ai, reader_ai
from tests.conftest import csrf_of, signup


def upstream(_: httpx.Request) -> httpx.Response:
    return httpx.Response(404)


@pytest.fixture
def fake_ai(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ai_provider", "fake")
    monkeypatch.setattr(s, "feature_ai_rag", True)
    monkeypatch.setattr(s, "feature_ai_reader", True)
    monkeypatch.setattr(s, "ai_daily_requests", 40)
    return s


@pytest.fixture
def sent(monkeypatch):
    """ai.chat 에 보낸 프롬프트를 모은다. 답은 테스트가 정할 수 있다(sent.reply)."""

    class Box(list):
        reply = None

    box = Box()

    async def chat(messages, *, system=None, max_tokens=700, json_mode=False, http=None):
        prompt = messages[-1]["content"]
        box.append({"system": system, "prompt": prompt, "json": json_mode})
        if box.reply is not None:
            return ai.ChatResult(box.reply, "fake")
        pages = re.findall(r"\[(\d+)쪽\]", prompt)
        return ai.ChatResult(f"쉽게 말하면 이런 뜻이에요 ({pages[-1] if pages else 1}쪽). 둘째 문장이에요.", "fake")

    monkeypatch.setattr(ai, "chat", chat)
    return box


def page(i: int) -> str:
    """쪽마다 문단 셋 — 문단마다 다른 표지 낱말(뒤 문단 · 뒤쪽이 새는지 본다)."""
    return f"영수는 {i}쪽 첫째문단표지{i} 마을에 왔어요.\n\n{i}쪽 둘째문단표지{i} 순이를 만났어요.\n\n{i}쪽 셋째문단표지{i} 끝."


async def make_book(s, n=14, owner_id=None) -> Book:
    book = Book(source="wikisource", external_id=f"reader-ai-{n}-{owner_id}", title="시험 책", author="작가",
                has_fulltext=True, total_chunks=n, owner_id=owner_id)  # fmt: skip
    s.add(book)
    await s.flush()
    for i in range(n):
        t = page(i)
        s.add(BookChunk(book_id=book.id, chunk_index=i, text=t, char_count=len(t)))
    await s.commit()
    return book


async def uid(s, email="reader@example.com") -> int:
    return await s.scalar(select(User.id).where(User.email == email))


async def set_verified(s, user_id: int, book: Book, v: int) -> None:
    e = await s.scalar(select(LibraryEntry).where(LibraryEntry.user_id == user_id, LibraryEntry.book_id == book.id))
    if e is None:
        s.add(LibraryEntry(user_id=user_id, book_id=book.id, shelf=Shelf.READING, verified_chunk=v))
    else:
        e.verified_chunk = v
    await s.commit()


async def setup(api, session_factory, verified=2, email="reader@example.com", nick="독자"):
    c = api(upstream)
    await c.__aenter__()
    await signup(c, email, nick)
    async with session_factory() as s:
        book = await make_book(s)
        await set_verified(s, await uid(s, email), book, verified)
    token = await csrf_of(c, "/me")
    return c, book, {"X-CSRF-Token": token}


def later_markers(chunk: int, anchor: int) -> list[str]:
    """chunk 쪽 anchor 문단 뒤의 표지 + 그 뒤 쪽들의 표지."""
    names = ["첫째", "둘째", "셋째"]
    out = [f"{names[a]}문단표지{chunk}" for a in range(anchor + 1, 3)]
    out += [f"문단표지{i}" for i in range(chunk + 1, 14)]
    return out


# --- 1. 문단에서 묻기 ------------------------------------------------------------------------


async def test_quick_actions_send_only_what_was_read(api, session_factory, fake_ai, sent):
    c, book, h = await setup(api, session_factory, verified=2)
    # 지금 펼친 쪽(3 = verified + 1)의 둘째 문단: 셋째 문단과 뒤쪽은 새면 안 된다
    for action, sel in (("explain", ""), ("word", "순이"), ("summary", "")):
        r = await c.post(f"/api/v1/books/{book.id}/ai/quick", headers=h,
                         json={"chunk_index": 3, "anchor": 1, "action": action, "selection": sel})  # fmt: skip
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["kind"] == "ok" and data["note_id"] and data["pages"] == [4]
        prompt = sent[-1]["prompt"]
        assert "둘째문단표지3" in prompt
        for m in later_markers(3, 1):
            assert m not in prompt and m not in r.text
        assert "뒤 내용" in sent[-1]["system"]
    assert "«순이»" in sent[1]["prompt"]
    assert "[2쪽]" in sent[2]["prompt"] and "[4쪽]" in sent[2]["prompt"]  # 요약은 앞 두 쪽 + 고른 곳까지
    await c.aclose()


async def test_quick_refuses_unread_pages_and_foreign_selection(api, session_factory, fake_ai, sent):
    c, book, h = await setup(api, session_factory, verified=2)
    url = f"/api/v1/books/{book.id}/ai/quick"
    r = await c.post(url, headers=h, json={"chunk_index": 4, "anchor": 0, "action": "explain"})
    assert r.status_code == 422 and "아직 읽지 않은 쪽" in r.json()["detail"]
    # 다른(뒤) 쪽의 글을 '고른 글'로 끼워 넣어도 받지 않는다
    r = await c.post(url, headers=h, json={"chunk_index": 1, "anchor": 0, "action": "explain",
                                           "selection": "13쪽 셋째문단표지13 끝."})  # fmt: skip
    assert r.status_code == 200 and "문단표지13" not in sent[-1]["prompt"]
    r = await c.post(url, headers=h, json={"chunk_index": 1, "anchor": 0, "action": "word", "selection": ""})
    assert r.status_code == 422 and "낱말" in r.json()["detail"]
    assert len(sent) == 1
    await c.aclose()


async def test_quick_cap_and_ai_down(api, session_factory, fake_ai, sent, monkeypatch):
    monkeypatch.setattr(fake_ai, "ai_daily_requests", 1)
    c, book, h = await setup(api, session_factory, verified=2)
    body = {"chunk_index": 1, "anchor": 0, "action": "explain"}
    assert (await c.post(f"/api/v1/books/{book.id}/ai/quick", headers=h, json=body)).json()["kind"] == "ok"
    r = await c.post(f"/api/v1/books/{book.id}/ai/quick", headers=h, json=body)
    assert r.status_code == 200 and r.json()["kind"] == "capped"
    assert "오늘은 여기까지" in r.json()["answer"] and len(sent) == 1
    # 캐시가 없는 지난 이야기도 같은 상한을 쓴다
    assert (await c.post(f"/api/v1/books/{book.id}/ai/recap", headers=h)).json()["kind"] == "capped"
    monkeypatch.setattr(fake_ai, "ai_daily_requests", 40)

    async def broken(*a, **k):
        raise ai.AIUnavailable("x")

    monkeypatch.setattr(ai, "chat", broken)
    r = await c.post(f"/api/v1/books/{book.id}/ai/quick", headers=h, json=body)
    assert r.status_code == 200 and r.json()["kind"] == "down"
    await c.aclose()


# --- 2. AI 노트 ---------------------------------------------------------------------------


async def _note(c, book, h, chunk=1, anchor=1) -> int:
    r = await c.post(f"/api/v1/books/{book.id}/ai/quick", headers=h,
                     json={"chunk_index": chunk, "anchor": anchor, "action": "explain"})  # fmt: skip
    return r.json()["note_id"]


async def test_notes_crud_and_privacy(api, session_factory, fake_ai, sent):
    c, book, h = await setup(api, session_factory, verified=5)
    base = f"/api/v1/books/{book.id}/ai/notes"
    nid = await _note(c, book, h)
    assert (await c.get(base)).json()["items"] == []  # 저장 전 초안은 목록에 없다
    saved = (await c.post(f"{base}/{nid}/save", headers=h)).json()
    assert saved["saved"] and not saved["shared"] and saved["chunk"] == 1 and saved["anchor"] == 1
    items = (await c.get(f"{base}?chunk=1")).json()["items"]
    assert [i["id"] for i in items] == [nid] and items[0]["label"] == "이 부분 쉽게 설명"
    assert (await c.get(f"{base}?chunk=2")).json()["items"] == []

    other = api(upstream)
    await other.__aenter__()
    await signup(other, "other@example.com", "남")
    oh = {"X-CSRF-Token": await csrf_of(other, "/me")}
    assert (await other.get(base)).json()["items"] == []  # 남의 노트는 목록에 없다
    assert (await other.get(f"{base}/{nid}")).status_code == 404
    assert (await other.post(f"{base}/{nid}/save", headers=oh)).status_code == 404
    assert (await other.post(f"{base}/{nid}/share", headers=oh)).status_code == 404
    assert (await other.delete(f"{base}/{nid}", headers=oh)).status_code == 404

    assert (await c.delete(f"{base}/{nid}", headers=h)).json() == {"deleted": nid}
    assert (await c.get(base)).json()["items"] == []
    await c.aclose()
    await other.aclose()


async def test_note_reaches_friends_only_after_share(api, session_factory, fake_ai, sent):
    c, book, h = await setup(api, session_factory, verified=5)
    friend = api(upstream)
    await friend.__aenter__()
    await signup(friend, "friend@example.com", "친구")
    async with session_factory() as s:
        me, fr = await uid(s), await uid(s, "friend@example.com")
        room = Room(book_id=book.id, owner_id=me, name="방", invite_code="AIREADER1")
        s.add(room)
        await s.flush()
        s.add_all([RoomMember(room_id=room.id, user_id=me), RoomMember(room_id=room.id, user_id=fr)])
        await s.commit()
        await set_verified(s, fr, book, 5)
    nid = await _note(c, book, h, chunk=1, anchor=1)
    await c.post(f"/api/v1/books/{book.id}/ai/notes/{nid}/save", headers=h)
    seen = (await friend.get(f"/api/v1/books/{book.id}/reactions?chunk=1")).json()
    assert seen["items"] == [] and "쉽게 말하면" not in json.dumps(seen, ensure_ascii=False)

    r = await c.post(f"/api/v1/books/{book.id}/ai/notes/{nid}/share", headers=h)
    assert r.status_code == 200 and r.json()["note"]["shared"]
    line = r.json()["reaction"]["body"]
    assert line.startswith("✦ ") and "쪽)" not in line and len(line) <= 140
    seen = (await friend.get(f"/api/v1/books/{book.id}/reactions?chunk=1")).json()
    assert [x["body"] for x in seen["items"]] == [line]
    # 친구가 아직 그 쪽을 다 읽지 않았다면: 반응의 스포일러 가드가 그대로(개수만)
    async with session_factory() as s:
        await set_verified(s, fr, book, 0)
    seen = (await friend.get(f"/api/v1/books/{book.id}/reactions?chunk=1")).json()
    assert seen["items"] == [] and seen["sealed"] == 1
    # 두 번 눌러도 반응은 하나
    await c.post(f"/api/v1/books/{book.id}/ai/notes/{nid}/share", headers=h)
    async with session_factory() as s:
        from app.models import Reaction

        assert await s.scalar(select(func.count()).select_from(Reaction)) == 1
    await c.aclose()
    await friend.aclose()


async def test_reader_has_ai_bar_only_when_on(api, session_factory, fake_ai, monkeypatch):
    c, book, h = await setup(api, session_factory, verified=2)
    html = (await c.get(f"/books/{book.id}")).text
    assert 'id="aiSel"' in html and "reader-ai.js" in html and 'data-away="0"' in html
    monkeypatch.setattr(fake_ai, "feature_ai_reader", False)
    html = (await c.get(f"/books/{book.id}")).text
    assert 'id="aiSel"' not in html and "reader-ai.js" not in html
    assert (await c.get(f"/api/v1/books/{book.id}/ai/notes")).status_code == 404
    r = await c.post(f"/api/v1/books/{book.id}/ai/quick", headers=h,
                     json={"chunk_index": 1, "anchor": 0, "action": "explain"})  # fmt: skip
    assert r.status_code == 404
    await c.aclose()


async def test_ask_page_lists_my_ai_notes(api, session_factory, fake_ai, sent):
    c, book, h = await setup(api, session_factory, verified=5)
    nid = await _note(c, book, h, chunk=2, anchor=0)
    await c.post(f"/api/v1/books/{book.id}/ai/notes/{nid}/save", headers=h)
    html = (await c.get(f"/books/{book.id}/ask")).text
    assert "내 AI 노트" in html and "쉽게 말하면 이런 뜻이에요" in html and "3쪽" in html
    await c.aclose()


# --- 3. 지난 이야기 · 등장인물 · 질문 --------------------------------------------------------


async def test_recap_cached_and_refreshed_when_reading_more(api, session_factory, fake_ai, sent):
    c, book, h = await setup(api, session_factory, verified=3)
    sent.reply = "영수가 마을에 왔어요.\n순이를 만났어요.\n이야기가 시작됐어요.\n넷째 줄은 버려요."
    url = f"/api/v1/books/{book.id}/ai/recap"
    first = (await c.post(url, headers=h)).json()
    assert first["kind"] == "ok" and first["answer"].count("\n") == 2 and not first["cached"]
    assert (await c.post(url, headers=h)).json()["cached"] and len(sent) == 1
    for m in later_markers(3, 2):
        assert m not in sent[0]["prompt"]
    async with session_factory() as s:
        await set_verified(s, await uid(s), book, 6)
    again = (await c.post(url, headers=h)).json()
    assert not again["cached"] and len(sent) == 2
    p = sent[1]["prompt"]
    assert "이전 요약(1~4쪽)" in p and "[5쪽]" in p and "[7쪽]" in p and "[4쪽]" not in p
    for m in later_markers(6, 2):
        assert m not in p
    await c.aclose()


async def test_characters_validated_cached_and_refreshed(api, session_factory, fake_ai, sent):
    c, book, h = await setup(api, session_factory, verified=4)
    sent.reply = json.dumps({"characters": [
        {"name": "영수", "line": "마을에 온 사람이에요."},
        {"name": "순이", "line": "영수가 만난 사람이에요."},
        {"name": "철수", "line": "글에 없는 지어낸 인물이에요."},
    ]}, ensure_ascii=False)  # fmt: skip
    url = f"/api/v1/books/{book.id}/ai/characters"
    data = (await c.post(url, headers=h)).json()
    assert [x["name"] for x in data["items"]] == ["영수", "순이"] and sent[0]["json"]
    assert (await c.post(url, headers=h)).json()["cached"] and len(sent) == 1
    async with session_factory() as s:
        await set_verified(s, await uid(s), book, 5)
        row = await s.get(AIBookCache, (await uid(s), book.id, "chars"))
        assert row.verified_chunk == 4
    sent.reply = json.dumps({"characters": [{"name": "순이", "line": "새 설명이에요."}]}, ensure_ascii=False)
    data = (await c.post(url, headers=h)).json()
    assert not data["cached"] and "이전 목록" in sent[1]["prompt"] and "[6쪽]" in sent[1]["prompt"]
    assert {x["name"]: x["line"] for x in data["items"]} == {"영수": "마을에 온 사람이에요.", "순이": "새 설명이에요."}
    for m in later_markers(5, 2):
        assert m not in sent[1]["prompt"]
    await c.aclose()


async def test_reflect_every_ten_pages(api, session_factory, fake_ai, sent):
    c, book, h = await setup(api, session_factory, verified=7)
    url = f"/api/v1/books/{book.id}/ai/reflect"
    assert (await c.post(url, headers=h)).status_code == 422 and not sent
    async with session_factory() as s:
        await set_verified(s, await uid(s), book, 11)
    sent.reply = "영수라면 그 마을에 남았을까요?"
    data = (await c.post(url, headers=h)).json()
    assert data["answer"] == "영수라면 그 마을에 남았을까요?" and data["pages"] == [10]
    assert "[10쪽]" in sent[0]["prompt"] and "[11쪽]" not in sent[0]["prompt"]  # 10쪽 구간까지만
    assert (await c.post(url, headers=h)).json()["cached"] and len(sent) == 1
    await c.aclose()


async def test_nothing_read_is_refused_without_ai(api, session_factory, fake_ai, sent):
    c, book, h = await setup(api, session_factory, verified=-1)
    for kind in ("recap", "characters"):
        r = await c.post(f"/api/v1/books/{book.id}/ai/{kind}", headers=h)
        assert r.status_code == 422
    assert not sent
    await c.aclose()


def test_away_and_helpers():
    now = dt.datetime(2026, 11, 19, 12, tzinfo=dt.UTC)
    assert reader_ai.away(now - dt.timedelta(days=1, minutes=1), now)
    assert not reader_ai.away(now - dt.timedelta(hours=23), now)
    assert not reader_ai.away(None, now)
    assert reader_ai.sample(0, 3, 10) == [0, 1, 2, 3]
    picked = reader_ai.sample(0, 99, 10)
    assert len(picked) == 10 and picked[0] == 0 and picked[-1] == 99
    assert reader_ai.milestone(8) is None and reader_ai.milestone(9) == 9 and reader_ai.milestone(25) == 19
    assert reader_ai.share_line("짧게 말하면 이래요 (3쪽). 둘째 문장.") == "✦ 짧게 말하면 이래요."
    assert reader_ai.parse_characters("영수: 마을 사람\n철수: 없음", "영수가 왔다") == [{"name": "영수", "line": "마을 사람"}]


async def test_drafts_are_bounded(api, session_factory, fake_ai, sent):
    c, book, h = await setup(api, session_factory, verified=5)
    for _ in range(reader_ai.DRAFTS_KEPT + 3):
        await _note(c, book, h)
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(AINote)) == reader_ai.DRAFTS_KEPT
    await c.aclose()


def test_characters_with_non_list_value_does_not_crash():
    from app.services import reader_ai as _r

    assert _r.parse_characters('{"characters": 5}', "영수가 왔다") == []
    assert _r.parse_characters('{"characters": true}', "영수가 왔다") == []
