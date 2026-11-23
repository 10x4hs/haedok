"""책에게 물어보기(RAG · 스포일러 가드) · 나를 위한 다음 책 · 벡터 저장소. 가짜 AI 공급자만 쓴다(네트워크 없음)."""

from __future__ import annotations

import re

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app import web
from app.core.config import get_settings
from app.models import Book, BookChunk, LibraryEntry, Shelf, User
from app.models_ai import AskLog, BookVector, ChunkVector, Vector
from app.models_posts import ReadingPost
from app.services import ai, rag, recs, vectors
from tests.conftest import csrf_of, signup

DIM = get_settings().ai_embed_dim


def upstream(_: httpx.Request) -> httpx.Response:
    return httpx.Response(404)


@pytest.fixture
def fake_ai(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ai_provider", "fake")
    monkeypatch.setattr(s, "feature_ai_rag", True)
    monkeypatch.setattr(s, "ai_daily_requests", 40)
    return s


@pytest.fixture
def captured(monkeypatch):
    """ai.chat 에 실제로 보낸 프롬프트를 모은다(가짜 답을 돌려준다)."""
    sent: list[dict] = []

    async def chat(messages, *, system=None, max_tokens=700, json_mode=False, http=None):
        sent.append({"system": system, "messages": messages})
        pages = re.findall(r"\[(\d+)쪽\]", messages[-1]["content"])
        cite = f"({pages[0]}쪽)" if pages else ""
        # 모델이 보내지 않은 쪽(99쪽)을 지어내도 지워져야 한다
        return ai.ChatResult(f"김첨지는 그날 운이 좋았어요 {cite}. 끝에는 99쪽에서 울어요 (99쪽).", "fake")

    monkeypatch.setattr(ai, "chat", chat)
    return sent


# 쪽마다 다른 낱말 — 뒤쪽 낱말이 어디에도 나가지 않는지 본다
PAGES = [
    "김첨지는 아침부터 비가 오는 날 인력거를 끌었어요. 앞집 마나님을 전찻길까지 모셔다 드렸어요. 첫째쪽표지",
    "김첨지는 운수 좋은 날이라며 기뻐했어요. 아내가 아파 누워 있었어요. 둘째쪽표지",
    "김첨지는 설렁탕을 사 가겠다고 생각했어요. 친구 치삼이를 만나 술을 마셨어요. 셋째쪽표지",
    "김첨지가 집에 돌아오니 아내는 이미 숨을 거두었어요. 비밀결말표지 설렁탕을 사왔는데 왜 먹지를 못하니.",
    "넷째 뒤쪽 비밀결말표지 김첨지는 울었어요.",
]


async def make_book(session, texts=PAGES, owner_id=None, source="wikisource", eid="운수 좋은 날") -> Book:
    book = Book(
        source=source if owner_id is None else "upload",
        external_id=eid if owner_id is None else f"up-{owner_id}-{eid}",
        title="운수 좋은 날",
        author="현진건",
        has_fulltext=True,
        total_chunks=len(texts),
        owner_id=owner_id,
    )
    session.add(book)
    await session.flush()
    for i, t in enumerate(texts):
        session.add(BookChunk(book_id=book.id, chunk_index=i, text=t, char_count=len(t)))
    await session.commit()
    return book


async def user_id(session, email="reader@example.com") -> int:
    return await session.scalar(select(User.id).where(User.email == email))


async def shelve(session, uid: int, book: Book, verified: int, shelf=Shelf.READING) -> None:
    session.add(LibraryEntry(user_id=uid, book_id=book.id, shelf=shelf, verified_chunk=verified))
    await session.commit()


# --- 벡터 저장소 ------------------------------------------------------------------------------


def test_split_parts_is_stable_and_bounded():
    text = ("가나다라마바사. " * 200).strip() + "\n\n짧은 문단."
    a, b = vectors.split_parts(text), vectors.split_parts(text)
    assert a == b and len(a) > 2
    assert all(len(p) <= vectors.PART_CHARS * 2 for p in a)
    assert vectors.split_parts("") == []


async def test_sqlite_cosine_orders_nearest_first(session_factory):
    def unit(i: int) -> list[float]:
        v = [0.0] * DIM
        v[i] = 1.0
        return v

    q = [0.0] * DIM
    q[0], q[1] = 0.8, 0.6
    async with session_factory() as s:
        items = [vectors.Item("wikisource", f"b{i}", f"책{i}", f"글{i}") for i in range(3)]
        await vectors.upsert_books(s, [(items[0], unit(2)), (items[1], unit(1)), (items[2], unit(0))])
        hits = await vectors.nearest_books(s, q, 3)
    assert [h.external_id for h in hits] == ["b2", "b1", "b0"]
    assert hits[0].score == pytest.approx(0.8, abs=1e-5) and hits[2].score == pytest.approx(0.0, abs=1e-6)


def test_postgres_sql_uses_pgvector_cosine_operator():
    pg = postgresql.dialect()
    knn = str(vectors.PG_KNN_CHUNKS.compile(dialect=pg))
    assert "vec <=> CAST(%(q)s AS vector)" in knn
    assert "book_id = %(book)s AND chunk_index <= %(upto)s" in knn  # 뒤쪽은 SQL 에서부터 빠진다
    books = str(vectors.PG_KNN_BOOKS.compile(dialect=pg))
    assert "ORDER BY vec <=> CAST(%(q)s AS vector) LIMIT %(k)s" in books
    assert vectors.ef_search_sql(12) == "SET LOCAL hnsw.ef_search = 40"
    assert vectors.ef_search_sql(300) == "SET LOCAL hnsw.ef_search = 600"
    ddl = str(CreateTable(BookVector.__table__).compile(dialect=pg))
    assert f"vec vector({DIM}) NOT NULL" in ddl
    (ix,) = [i for i in BookVector.__table__.indexes if i.name == "ix_book_vectors_vec_hnsw"]
    assert "USING hnsw (vec vector_cosine_ops)" in str(CreateIndex(ix).compile(dialect=pg))
    # 값은 pgvector 글자 표기로 넘긴다
    assert Vector(3).process_bind_param([0.5, 0.25, 1.0], pg) == "[0.5,0.25,1]"
    assert vectors.pg_literal([0.5, -1.0]) == "[0.5,-1]"


async def test_fill_books_is_bounded_and_idempotent(session_factory, fake_ai):
    items = vectors.catalogue_items()
    assert len(items) > vectors.BATCH
    async with session_factory() as s:
        p = await vectors.fill_books(s, vectors.BATCH)
        assert p.added == vectors.BATCH and p.done == vectors.BATCH
        sub = items[:5]
        again = await vectors.fill_books(s, 10, sub)
        assert again.added == 0 and again.done == 5  # 해시가 같으면 다시 임베딩하지 않는다
        sub[0] = vectors.Item(sub[0].source, sub[0].external_id, sub[0].title, sub[0].text + " 개정")
        assert (await vectors.fill_books(s, 10, sub)).added == 1


async def test_chunk_vectors_are_bounded_and_resume(session_factory, fake_ai):
    async with session_factory() as s:
        book = await make_book(s)
        first = await vectors.ensure_chunks(s, book, upto=3, budget=2)
        assert first.added == 2 and first.pending
        assert set(await s.scalars(select(ChunkVector.chunk_index))) == {0, 1}
        second = await vectors.ensure_chunks(s, book, upto=3, budget=2)
        assert second.added == 2 and not second.pending
        # 읽은 쪽(upto) 너머는 만들지 않는다
        assert max(await s.scalars(select(ChunkVector.chunk_index))) == 3


# --- 책에게 물어보기 --------------------------------------------------------------------------


async def test_later_pages_never_reach_prompt_or_answer(api, session_factory, fake_ai, captured):
    async with api(upstream) as c:
        await signup(c)
        async with session_factory() as s:
            book = await make_book(s)
            await shelve(s, await user_id(s), book, verified=2)
            # 다른 사람이 끝까지 읽어 뒤쪽 조각 벡터가 이미 있어도 새지 않는다
            await vectors.ensure_chunks(s, book, upto=4)
        token = await csrf_of(c, f"/books/{book.id}/ask")
        r = await c.post(
            f"/api/v1/books/{book.id}/ask",
            json={"question": "설렁탕을 사왔는데 아내는 어떻게 되나요?"},
            headers={"X-CSRF-Token": token},
        )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["kind"] == "ok"
    prompt = captured[0]["messages"][-1]["content"]
    assert "[1쪽]" in prompt or "[2쪽]" in prompt or "[3쪽]" in prompt
    for later in ("비밀결말표지", "[4쪽]", "[5쪽]", "숨을 거두었어요"):
        assert later not in prompt and later not in r.text
    assert "뒤 내용" in captured[0]["system"]  # 뒤 내용을 말하지 말라고 분명히 적는다
    assert all(p <= 3 for p in data["pages"]) and data["pages"]
    assert "99쪽" not in data["answer"]  # 보내지 않은 쪽 번호는 지운다
    assert re.search(r"\([123]쪽\)", data["answer"])


async def test_nothing_relevant_says_so_without_calling_chat(session_factory, fake_ai, captured):
    async with session_factory() as s:
        s.add(User(email="a@example.com", nickname="a", password_hash="x"))
        await s.commit()
        uid = await user_id(s, "a@example.com")
        book = await make_book(s)
        await shelve(s, uid, book, verified=1)
        user = await s.get(User, uid)
        a = await rag.ask(s, user, book, "qqqq wwww zzzz xxxx")
    assert a.kind == "not_found" and a.text == rag.NOT_FOUND == "아직 읽은 부분에는 그 내용이 없어요."
    assert captured == []


async def test_unread_book_is_refused(api, session_factory, fake_ai, captured):
    async with api(upstream) as c:
        await signup(c)
        async with session_factory() as s:
            book = await make_book(s)
            await shelve(s, await user_id(s), book, verified=-1)
        token = await csrf_of(c, f"/books/{book.id}/ask")
        r = await c.post(f"/api/v1/books/{book.id}/ask", json={"question": "결말?"}, headers={"X-CSRF-Token": token})
        page = await c.get(f"/books/{book.id}/ask")
    assert r.status_code == 200 and r.json()["kind"] == "unread"
    assert captured == []
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(ChunkVector)) == 0
    assert "아직 다 읽은 쪽이 없어요" in page.text


async def test_private_upload_only_for_its_owner(api, session_factory, fake_ai, captured):
    async with api(upstream) as owner, api(upstream) as other:
        await signup(owner, "owner@example.com", "주인")
        await signup(other, "other@example.com", "남")
        async with session_factory() as s:
            book = await make_book(s, owner_id=await user_id(s, "owner@example.com"))
            await shelve(s, await user_id(s, "owner@example.com"), book, verified=1)
            await shelve(s, await user_id(s, "other@example.com"), book, verified=1)
        page = await owner.get(f"/books/{book.id}/ask")
        assert page.status_code == 200 and "내 파일 내용 일부가 AI 서비스로 보내져요" in page.text
        token = await csrf_of(other, "/me")
        assert (await other.get(f"/books/{book.id}/ask")).status_code == 404
        assert (await other.get(f"/api/v1/books/{book.id}/ask")).status_code == 404
        r = await other.post(f"/api/v1/books/{book.id}/ask", json={"question": "김첨지"}, headers={"X-CSRF-Token": token})
        assert r.status_code == 404
    assert captured == []


async def test_daily_cap(api, session_factory, fake_ai, captured, monkeypatch):
    monkeypatch.setattr(fake_ai, "ai_daily_requests", 1)
    async with api(upstream) as c:
        await signup(c)
        async with session_factory() as s:
            book = await make_book(s)
            await shelve(s, await user_id(s), book, verified=2)
        token = await csrf_of(c, f"/books/{book.id}/ask")
        h = {"X-CSRF-Token": token}
        first = await c.post(f"/api/v1/books/{book.id}/ask", json={"question": "김첨지 인력거"}, headers=h)
        second = await c.post(f"/api/v1/books/{book.id}/ask", json={"question": "김첨지 인력거"}, headers=h)
        form = await c.post(f"/books/{book.id}/ask", data={"question": "또", "csrf": token})
        page = await c.get(form.headers["location"])
    assert first.json()["kind"] == "ok"
    assert second.status_code == 200 and second.json()["kind"] == "capped"
    assert "오늘은 여기까지 물어볼 수 있어요" in second.json()["answer"]
    assert "오늘은 여기까지 물어볼 수 있어요" in page.text
    assert len(captured) == 1


async def test_ai_down_is_calm(api, session_factory, fake_ai, monkeypatch):
    async def broken(texts, *, http=None):
        raise ai.AIUnavailable("timeout")

    monkeypatch.setattr(ai, "embed", broken)
    async with api(upstream) as c:
        await signup(c)
        async with session_factory() as s:
            book = await make_book(s)
            await shelve(s, await user_id(s), book, verified=2)
        token = await csrf_of(c, f"/books/{book.id}/ask")
        r = await c.post(f"/api/v1/books/{book.id}/ask", json={"question": "김첨지"}, headers={"X-CSRF-Token": token})
        form = await c.post(f"/books/{book.id}/ask", data={"question": "김첨지", "csrf": token})
        page = await c.get(form.headers["location"])
    assert r.status_code == 200 and r.json()["kind"] == "down"
    assert r.json()["answer"] == rag.DOWN
    assert page.status_code == 200 and rag.DOWN in page.text


async def test_history_keeps_five_and_can_be_deleted(api, session_factory, fake_ai, captured):
    async with api(upstream) as c:
        await signup(c)
        async with session_factory() as s:
            book = await make_book(s)
            await shelve(s, await user_id(s), book, verified=2)
        token = await csrf_of(c, f"/books/{book.id}/ask")
        h = {"X-CSRF-Token": token}
        for n in range(7):
            await c.post(f"/api/v1/books/{book.id}/ask", json={"question": f"김첨지 인력거 {n}"}, headers=h)
        hist = (await c.get(f"/api/v1/books/{book.id}/ask")).json()
        assert [it["question"] for it in hist["items"]] == [f"김첨지 인력거 {n}" for n in (6, 5, 4, 3, 2)]
        assert hist["read_upto"] == 3
        page = await c.get(f"/books/{book.id}/ask")
        assert "AI 답변 · 틀릴 수 있어요" in page.text and "김첨지 인력거 6" in page.text
        assert (await c.delete(f"/api/v1/books/{book.id}/ask", headers=h)).json() == {"deleted": 5}
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(AskLog)) == 0


async def test_hidden_and_404_when_ai_or_flag_off(api, session_factory, monkeypatch):
    s0 = get_settings()
    monkeypatch.setattr(s0, "ai_provider", "")
    async with api(upstream) as c:
        await signup(c)
        async with session_factory() as s:
            book = await make_book(s)
            await shelve(s, await user_id(s), book, verified=2)
        reader = await c.get(f"/books/{book.id}")
        assert reader.status_code == 200 and "askBtn" not in reader.text
        assert (await c.get(f"/books/{book.id}/ask")).status_code == 404
        assert "나를 위한 다음 책" not in (await c.get("/discover")).text
        monkeypatch.setattr(s0, "ai_provider", "fake")
        monkeypatch.setattr(s0, "feature_ai_rag", False)
        assert (await c.get(f"/api/v1/books/{book.id}/ask")).status_code == 404
        monkeypatch.setattr(s0, "feature_ai_rag", True)
        reader = await c.get(f"/books/{book.id}")
        assert 'id="askBtn"' in reader.text and 'id="askWrap"' in reader.text


# --- 나를 위한 다음 책 -----------------------------------------------------------------------


def _cat(title: str) -> dict:
    return next(b for b in web.CATALOG if b["title"] == title)


async def _library_book(s, meta: dict) -> Book:
    book = Book(source=meta["source"], external_id=str(meta["id"]), title=meta["title"], author=meta.get("author"))
    s.add(book)
    await s.flush()
    return book


async def test_taste_weights(session_factory):
    async with session_factory() as s:
        s.add(User(email="w@example.com", nickname="w", password_hash="x"))
        await s.commit()
        uid = await user_id(s, "w@example.com")
        done, reading, liked, disliked = [await _library_book(s, web.CATALOG[i]) for i in range(4)]
        s.add_all(
            [
                LibraryEntry(user_id=uid, book_id=done.id, shelf=Shelf.DONE),
                LibraryEntry(user_id=uid, book_id=reading.id, shelf=Shelf.READING),
                LibraryEntry(user_id=uid, book_id=liked.id, shelf=Shelf.READING),
                LibraryEntry(user_id=uid, book_id=disliked.id, shelf=Shelf.DONE),
            ]
        )
        for b, stars in ((liked, 5), (disliked, 1)):
            s.add(
                ReadingPost(author_id=uid, book_source=b.source, external_id=b.external_id, book_title=b.title,
                            status="done", rating=stars, post_day="2026-11-19")
            )
        await s.commit()
        h = await recs.history(s, uid)
    key = lambda b: (b.source, b.external_id)  # noqa: E731
    assert h.weights[key(done)] == pytest.approx(1.0)
    assert h.weights[key(reading)] == pytest.approx(0.6)
    assert h.weights[key(liked)] == pytest.approx(1.5)  # 게시물 '다 읽음' 1.0 + 별점 4 이상 0.5
    assert h.weights[key(disliked)] == pytest.approx(0.5)  # 다 읽음 1.0 - 별점 2 이하 0.5
    assert recs.weighted_mean({("a", "1"): 1.0, ("a", "2"): -0.5}, {("a", "1"): [1.0, 0.0], ("a", "2"): [0.0, 1.0]}) == pytest.approx(
        [0.8944272, -0.4472136], abs=1e-6
    )
    assert recs.weighted_mean({("a", "1"): -0.5}, {("a", "1"): [1.0, 0.0]}) is None


async def test_for_user_excludes_shelved_and_explains(session_factory, fake_ai):
    demian = next(b for b in web.STORE if b["title"].startswith("데미안"))
    picks = [_cat("운수 좋은 날"), _cat("메밀꽃 필 무렵"), _cat("동백꽃"), _cat("봄봄")]
    async with session_factory() as s:
        items = [vectors.Item(b["source"], str(b["id"]), b["title"], vectors.describe(b)) for b in picks]
        items.append(vectors.Item("store", demian["id"], demian["title"], vectors.describe(demian)))
        await vectors.fill_books(s, 10, items)
        s.add(User(email="r@example.com", nickname="r", password_hash="x"))
        await s.commit()
        uid = await user_id(s, "r@example.com")
        s.add(
            ReadingPost(author_id=uid, book_source="store", external_id=demian["id"], book_title=demian["title"],
                        status="done", rating=5, post_day="2026-11-19")
        )
        unsu = await _library_book(s, picks[0])
        s.add(LibraryEntry(user_id=uid, book_id=unsu.id, shelf=Shelf.READING))
        await s.commit()
        refs = await recs.for_user(s, uid, k=12)
        assert refs and all(isinstance(r, recs.BookRef) for r in refs)
        got = {(r.source, r.external_id) for r in refs}
        assert ("store", demian["id"]) not in got and (picks[0]["source"], picks[0]["id"]) not in got
        assert all(r.reason for r in refs)
        assert {r.reason for r in refs} <= {"「데미안」을 좋아했다면", "「운수 좋은 날」을 읽고 있다면"}
        assert [r.score for r in refs] == sorted((r.score for r in refs), reverse=True)
        # 캐시: 두 번째는 같은 답
        assert [r.external_id for r in await recs.for_user(s, uid, k=12)] == [r.external_id for r in refs]
        # 서재가 바뀌면 다시 계산하고, 새로 꽂은 책은 빠진다
        dong = await _library_book(s, picks[2])
        s.add(LibraryEntry(user_id=uid, book_id=dong.id, shelf=Shelf.DONE))
        await s.commit()
        assert (picks[2]["source"], picks[2]["id"]) not in {(r.source, r.external_id) for r in await recs.for_user(s, uid)}
        # similar_books 도 같은 목록에서, exclude_user 의 책은 뺀다
        sim = await recs.similar_books(s, "봄봄 김유정", k=3, exclude_user=uid)
        assert sim and sim[0].title == "봄봄"
        assert all((r.source, r.external_id) != (picks[2]["source"], picks[2]["id"]) for r in sim)


def test_free_first_when_scores_tie():
    a = recs.BookRef("store", "1", "가", None, None, False, "/store/1", 0.5)
    b = recs.BookRef("wikisource", "x", "나", None, None, True, "/open/wikisource/x", 0.5)
    assert [r.free for r in recs._rank([a, b], 2)] == [True, False]


def test_reason_particle():
    assert recs.reason_for("데미안 (한글판)", "done") == "「데미안」을 좋아했다면"
    assert recs.reason_for("메밀꽃 필 무렵", "done") == "「메밀꽃 필 무렵」을 좋아했다면"
    assert recs.reason_for("봄봄", "reading") == "「봄봄」을 읽고 있다면"
    assert recs.reason_for("동백꽃", "done") == "「동백꽃」을 좋아했다면"
    assert recs.reason_for("어린 왕자 이야기", "done").endswith("를 좋아했다면")


async def test_cold_start_uses_famous_pool(session_factory, fake_ai):
    async with session_factory() as s:
        s.add(User(email="c@example.com", nickname="c", password_hash="x"))
        await s.commit()
        refs = await recs.for_user(s, await user_id(s, "c@example.com"), k=12)
    free, store = web._famous_pool()
    assert len(refs) == 12 and all(r.reason is None for r in refs)
    assert refs[0].free and refs[0].external_id == str(free[0]["id"])


async def test_links_are_valid(api, session_factory, fake_ai):
    async with session_factory() as s:
        s.add(User(email="l@example.com", nickname="l", password_hash="x"))
        await s.commit()
        refs = await recs.for_user(s, await user_id(s, "l@example.com"), k=40)
    assert any(r.free for r in refs) and any(not r.free for r in refs)
    for r in refs:
        if r.free:
            assert r.href.startswith(f"/open/{r.source}/") and (r.source, r.external_id) in web.CATALOG_INDEX
        else:
            assert r.href == f"/store/{r.external_id}"
    store_ref = next(r for r in refs if not r.free)
    async with api(upstream) as c:
        await signup(c)
        page = await c.get(store_ref.href)
    assert page.status_code == 200 and store_ref.title.split(" (")[0] in page.text


async def test_rows_on_discover_and_profile(api, session_factory, fake_ai):
    async with api(upstream) as c:
        await signup(c)
        discover = await c.get("/discover")
        assert discover.status_code == 200
        assert "나를 위한 다음 책" in discover.text
        assert discover.text.index("나를 위한 다음 책") < discover.text.index("인기 순위")
        async with session_factory() as s:
            uid = await user_id(s)
        me = await c.get(f"/u/{uid}")
        assert me.status_code == 200 and "나를 위한 다음 책" in me.text


async def test_admin_ai_fills_one_batch(api, session_factory, fake_ai, monkeypatch):
    monkeypatch.setattr(fake_ai, "admin_emails", "admin@example.com")
    async with api(upstream) as c:
        await signup(c, "reader2@example.com", "독자2")
        assert (await c.get("/admin/ai")).status_code == 404
    async with api(upstream) as c:
        await signup(c, "admin@example.com", "관리자")
        page = await c.get("/admin/ai")
        assert page.status_code == 200 and "책 벡터" in page.text and "100권 채우기" in page.text
        token = await csrf_of(c, "/admin/ai")
        r = await c.post("/admin/ai/fill", data={"csrf": token})
        assert r.status_code == 303 and "100" in httpx.URL(r.headers["location"]).params["msg"]
        after = await c.get("/admin/ai")
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(BookVector)) == vectors.BATCH
    assert f"{vectors.BATCH} / " in after.text


async def test_build_script_fills_in_batches_and_is_idempotent(tmp_path, fake_ai, capsys):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.base import Base
    from scripts import build_book_vectors as script

    url = f"sqlite+aiosqlite:///{tmp_path / 'v.db'}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    assert await script.run(url, 150, check=False) == 0
    out = capsys.readouterr().out
    assert "+100권" in out and "+50권" in out
    assert await script.run(url, None, check=True) == 0
    assert f"150 / {len(vectors.catalogue_items()):,}" in capsys.readouterr().out


def test_sentence_with_an_invented_page_is_dropped_whole():
    """지어낸 쪽(99쪽)을 말한 문장은 번호만 바꾸지 않고 통째로 버린다 — 뒤 내용이 새지 않게."""
    text, cited = rag.clean_cites("김첨지는 운이 좋았어요 (1쪽). 끝에는 99쪽에서 울어요 (99쪽). 비가 왔어요 (2쪽, 3쪽).", {1, 2, 3})
    assert "울어요" not in text and "99" not in text and "어느 쪽" not in text
    assert "운이 좋았어요 (1쪽)" in text and "비가 왔어요 (2쪽, 3쪽)" in text
    assert cited == [1, 2, 3]
    assert rag.clean_cites("끝에는 99쪽에서 울어요 (99쪽).", {1, 2, 3}) == ("", [])


def test_page_tag_after_the_period_belongs_to_that_sentence():
    """‘…배신해요. (40쪽)’처럼 마침표 뒤에 붙은 쪽 표시도 그 문장의 근거다 — 다음 문장으로 넘어가 필터를 피하면 안 된다."""
    text, cited = rag.clean_cites("그는 착해요. (3쪽) 그는 결국 친구를 배신해요. (40쪽)", {3})
    assert "배신" not in text and "40" not in text
    assert text == "그는 착해요. (3쪽)" and cited == [3]
    text, _ = rag.clean_cites("그는 결국 친구를 배신해요.(40쪽) 끝.", {3})
    assert "배신" not in text and text == "끝."
