"""AI 서점 도우미(/assistant) · 이번 달 독서(/me/recap) · 게시물 초안.

AI 는 가짜(AI_PROVIDER=fake) 또는 monkeypatch 한 ai.chat 만 쓴다. 벡터 검색도 monkeypatch 한다. 네트워크는 없다.
"""

import datetime as dt
import json
import re
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
from sqlalchemy import func, select

from app import web
from app.core.config import get_settings
from app.models import Book, Highlight, LibraryEntry, Reaction, ReactionKind, ReadingEvent, Shelf, User
from app.models_posts import ReadingPost
from app.services import ai, goals, recap, shop_ai
from tests.conftest import csrf_of, signup

FREE = web.CATALOG[0]  # 무료 카탈로그 첫 책(운수 좋은 날)
STORE = web.STORE[0]  # 서점 고른 책(가격 정보 없음)


def no_net(req: httpx.Request) -> httpx.Response:
    raise AssertionError(f"network call: {req.url}")


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.delenv("FEATURE_AI_SHOP", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def no_vectors(monkeypatch):
    monkeypatch.setattr(shop_ai, "vectors", None)


class Spy:
    """ai.chat 대역 — 받은 메시지를 적어 두고 정해 둔 답(또는 함수 결과)을 돌려준다."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    async def __call__(self, messages, *, system=None, max_tokens=700, json_mode=False, http=None):
        self.calls.append({"messages": messages, "system": system, "json_mode": json_mode})
        text = self.answer(messages) if callable(self.answer) else self.answer
        if isinstance(text, Exception):
            raise text
        return ai.ChatResult(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False), "spy")

    @property
    def prompt(self) -> str:
        return "\n".join(m["content"] for c in self.calls for m in c["messages"])


def ids_in(messages) -> dict[str, str]:
    """후보 목록 줄(c1 | 제목 | …)에서 id → 제목."""
    out = {}
    for line in messages[-1]["content"].splitlines():
        m = re.match(r"(c\d+) \| ([^|]+) \|", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


async def user_id(session_factory, email="reader@example.com") -> int:
    async with session_factory() as s:
        return await s.scalar(select(User.id).where(User.email == email))


async def ask(client, q: str, state: str = "", chip: str = "") -> httpx.Response:
    token = await csrf_of(client, "/assistant")
    return await client.post("/assistant", data={"csrf": token, "q": q, "chip": chip, "state": state})


def state_of(html: str) -> str:
    return re.search(r'name="state" value="([^"]*)"', html).group(1)


# --------------------------------------------------------------------------- 꺼짐 · 입구


async def test_ai_off_hides_everything(api, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    get_settings.cache_clear()
    c = api(no_net)
    await signup(c)
    for path in ("/assistant", "/me/recap"):
        assert (await c.get(path)).status_code == 404
    token = await csrf_of(c, "/me")
    assert (await c.post("/assistant", data={"csrf": token, "q": "소설"})).status_code == 404
    assert (await c.post("/me/recap/draft", data={"csrf": token, "book_id": 1})).status_code == 404
    for path in ("/discover", "/search?q=zzqqxxnotabook", f"/store/{STORE['id']}", "/profile"):
        html = (await c.get(path)).text
        assert "/assistant" not in html and "/me/recap" not in html, path


async def test_flag_off_hides_even_with_ai(api, ai_on, monkeypatch):
    monkeypatch.setenv("FEATURE_AI_SHOP", "false")
    get_settings.cache_clear()
    c = api(no_net)
    await signup(c)
    assert (await c.get("/assistant")).status_code == 404
    assert (await c.get("/me/recap")).status_code == 404
    assert "/assistant" not in (await c.get("/discover")).text


async def test_entry_points_when_on(api, ai_on):
    c = api(no_net)
    await signup(c)
    assert 'href="/assistant"' in (await c.get("/discover")).text
    assert "/assistant?q=" in (await c.get("/search?q=zzqqxxnotabook")).text
    assert "/assistant?q=" in (await c.get(f"/store/{STORE['id']}")).text
    assert 'href="/me/recap"' in (await c.get("/profile")).text
    page = (await c.get("/assistant?q=사피엔스")).text
    assert "사피엔스</textarea>" in page  # GET 은 미리 채우기만 하고 AI 를 부르지 않는다


async def test_requires_login(api, ai_on):
    c = api(no_net)
    r = await c.get("/assistant")
    assert r.status_code in (303, 307, 401) or "/login" in str(r.url)


# --------------------------------------------------------------------------- 고르기 규칙(단위)


def _cands() -> list[shop_ai.Candidate]:
    a = shop_ai._from_catalog(FREE)
    b = shop_ai._from_store({"id": "123456789", "title": "가격 있는 책", "author": "누구", "price": 15000})
    c = shop_ai._from_store({"id": "987654321", "title": "가격 모르는 책", "author": "누구"})
    for i, x in enumerate((a, b, c), 1):
        x.id = f"c{i}"
    return [a, b, c]


def test_read_answer_drops_unknown_ids_and_keeps_our_data():
    cands = _cands()
    text = json.dumps({
        "reply": "골라 봤어요",
        "picks": [
            {"id": "c2", "why": "딱 맞아요", "price": 1, "href": "https://evil.example/buy"},
            {"id": "c99", "why": "지어낸 책"},
            {"id": "c2", "why": "중복"},
            "c1",
            {"id": "c3", "why": "x" * 500},
            {"id": 7},
        ],
        "followup": "더 짧은 게 좋아요?",
    }, ensure_ascii=False)  # fmt: skip
    t = shop_ai.read_answer("q", text, cands)
    assert [p.title for p in t.picks] == ["가격 있는 책", FREE["title"], "가격 모르는 책"]
    assert t.picks[0].price == 15000 and t.picks[0].href == "/store/123456789"
    assert t.picks[2].price is None and len(t.picks[2].why) <= shop_ai.MAX_WHY
    assert t.followup == "더 짧은 게 좋아요?" and t.note == "ai"


def test_read_answer_garbage_falls_back_to_candidates():
    t = shop_ai.read_answer("q", "『없는 책』은 9,900원이에요", _cands())
    assert t.note == "fallback" and len(t.picks) == 3 and t.reply == shop_ai.FALLBACK_REPLY
    t = shop_ai.read_answer("q", json.dumps({"reply": "", "picks": [{"id": "c77"}]}), _cands())
    assert t.picks == [] and t.reply == shop_ai.NONE_MSG


def test_prices_come_only_from_store_data():
    assert shop_ai.price_of({"price": 12000}) == 12000
    for bad in (None, 0, -5, "12000", True, 1.5):
        assert shop_ai.price_of({"price": bad}) is None
    ref = SimpleNamespace(source="store", external_id=STORE["id"], title="다른 제목", author=None,
                          cover=None, free=False, href="https://evil.example", score=1.0, reason="", price=999)  # fmt: skip
    c = shop_ai._from_ref(ref)
    assert c.price == shop_ai.price_of(STORE) and c.href == f"/store/{STORE['id']}"
    # 서점 데이터에 없는 서점 책이라도 링크는 우리 /store 화면뿐이고 가격은 모름
    ref.external_id = "111222333"
    c = shop_ai._from_ref(ref)
    assert c.href == "/store/111222333" and c.price is None
    # 본문을 줄 수 없는 소스의 '무료' 책은 후보가 되지 않는다
    assert (
        shop_ai._from_ref(SimpleNamespace(source="openlibrary", external_id="x", title="t", free=True))
        is None
    )


def test_affiliate_is_noop_and_store_page_uses_it():
    assert shop_ai.affiliate("https://www.aladin.co.kr/x?ItemId=1") == "https://www.aladin.co.kr/x?ItemId=1"
    assert web.templates.env.filters["buy_link"] is shop_ai.affiliate


def test_state_is_signed_and_per_user():
    t = shop_ai.Turn("q", "r", [shop_ai.Pick.of(_cands()[0], "why")], "f")
    packed = shop_ai.pack(1, [t])
    assert shop_ai.unpack(1, packed)[0].picks[0].title == FREE["title"]
    assert shop_ai.unpack(2, packed) == []  # 다른 사람
    body, mac = packed.rsplit(".", 1)
    assert shop_ai.unpack(1, body + "." + ("0" * len(mac))) == []  # 위조
    assert shop_ai.unpack(1, "garbage") == []
    many = shop_ai.pack(1, [t] * 20)
    assert len(shop_ai.unpack(1, many)) == shop_ai.KEEP_TURNS


def test_messages_send_at_most_8_turns():
    turns = [shop_ai.Turn(f"q{i}", f"r{i}") for i in range(12)]
    msgs = shop_ai.messages_for(turns, "지금", _cands())
    assert sum(1 for m in msgs if m["role"] == "user") == shop_ai.MAX_TURNS
    assert "q4" not in [m["content"] for m in msgs] and msgs[1]["content"].startswith("r5")
    assert "요청: 지금" in msgs[-1]["content"] and "c1 |" in msgs[-1]["content"]


# --------------------------------------------------------------------------- 한 차례(화면)


async def test_turn_picks_only_from_candidates(api, ai_on, no_vectors, monkeypatch):
    def answer(messages):
        ids = list(ids_in(messages))
        assert 3 <= len(ids) <= shop_ai.MAX_CANDIDATES
        return {"reply": "따뜻한 단편을 골랐어요.",
                "picks": [{"id": ids[0], "why": "짧고 따뜻해요"}, {"id": "c999", "why": "지어냄"},
                          {"id": ids[1], "why": "분위기가 비슷해요"}, {"id": ids[2], "why": "쉽게 읽혀요"}],
                "followup": "무서운 이야기도 괜찮아요?"}  # fmt: skip

    spy = Spy(answer)
    monkeypatch.setattr(ai, "chat", spy)
    c = api(no_net)
    await signup(c)
    r = await ask(c, "요즘 지쳐서 짧고 따뜻한 소설")
    assert r.status_code == 200
    html = r.text
    assert spy.calls[0]["json_mode"] is True and spy.calls[0]["system"] == shop_ai.ASSISTANT_SYSTEM
    named = ids_in(spy.calls[0]["messages"])
    assert html.count('class="ai-card ') == 3 and "지어냄" not in html
    assert "AI 답변 · 틀릴 수 있어요" in html and "무서운 이야기도 괜찮아요?" in html
    for title in list(named.values())[:3]:
        assert title.replace("&", "&amp;") in html
    for chip in shop_ai.REFINE:
        assert f'value="{chip}"' in html
    # 이어서 묻기: 서명된 대화를 들고 오고, 앞 차례가 AI 에게 함께 간다
    r = await ask(c, "", state=state_of(html), chip="무료만")
    assert r.status_code == 200 and r.text.count('class="ai-msg me') == 2
    assert spy.calls[1]["messages"][0]["content"] == "요즘 지쳐서 짧고 따뜻한 소설"
    assert all(
        "| 무료" in line
        for line in spy.calls[1]["messages"][-1]["content"].splitlines()
        if line.startswith("c")
    )


async def test_store_card_never_invents_price(api, ai_on, no_vectors, monkeypatch):
    unknown = next(b for b in web.STORE if not shop_ai.price_of(b))

    def answer(messages):
        cid = next(k for k, v in ids_in(messages).items() if v == unknown["title"])
        return {"reply": "이 책이요. 12,000원이에요.", "picks": [{"id": cid, "why": "할인 9,900원!", "price": 9900}],
                "followup": None}  # fmt: skip

    monkeypatch.setattr(ai, "chat", Spy(answer))
    c = api(no_net)
    await signup(c)
    html = (await ask(c, unknown["title"])).text
    assert shop_ai.UNKNOWN_PRICE in html
    assert f'href="/store/{unknown["id"]}"' in html and "서점에서 보기" in html
    assert "aladin.co.kr/shop" not in html  # 바깥 구매 링크는 서점 화면(/store)에만 있다
    assert "9900" not in html.replace("9,900원!", "")  # 가격 칸에는 지어낸 숫자가 없다


async def test_fallback_keyword_search_without_vectors(api, ai_on, no_vectors):
    c = api(no_net)
    await signup(c)
    cands = None
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_net)) as http:
        from app.db.session import get_session
        from app.main import app

        gen = app.dependency_overrides[get_session]()
        session = await gen.__anext__()
        user = await session.scalar(select(User))
        cands = await shop_ai.candidates(session, http, user, FREE["title"])
        await gen.aclose()
    assert cands[0].title == FREE["title"] and cands[0].kind == "free"
    assert cands[0].href == f"/open/{FREE['source']}/{quote(FREE['id'])}"
    assert len(cands) <= shop_ai.MAX_CANDIDATES and len({c.key for c in cands}) == len(cands)
    assert [c.id for c in cands] == [f"c{i}" for i in range(1, len(cands) + 1)]


@dataclass
class BookRef:
    source: str
    external_id: str
    title: str
    author: str | None
    cover: str | None
    free: bool
    href: str
    score: float
    reason: str


async def test_vectors_come_first_and_are_deduped(api, ai_on, monkeypatch, session_factory):
    seen = {}
    second = web.CATALOG[1]

    async def similar_books(session, text, k=8, exclude_user=None):
        seen["similar"] = (text, exclude_user)
        return [
            BookRef(
                second["source"],
                second["id"],
                second["title"],
                second.get("author"),
                None,
                True,
                "/x",
                0.9,
                "비슷한 분위기",
            ),
            BookRef(second["source"], second["id"], second["title"], None, None, True, "/x", 0.8, ""),
        ]

    async def for_user(session, user_id, k=12):
        seen["for_user"] = user_id
        raise RuntimeError("임베딩 없음")  # 한쪽이 실패해도 낱말 검색으로 이어진다

    monkeypatch.setattr(shop_ai, "vectors", SimpleNamespace(similar_books=similar_books, for_user=for_user))
    c = api(no_net)
    await signup(c)
    uid = await user_id(session_factory)
    spy = Spy({"reply": "r", "picks": [], "followup": None})
    monkeypatch.setattr(ai, "chat", spy)
    await ask(c, "슬픈 사랑 이야기")
    assert seen["similar"] == ("슬픈 사랑 이야기", uid) and seen["for_user"] == uid
    lines = [x for x in spy.calls[0]["messages"][-1]["content"].splitlines() if x.startswith("c")]
    assert lines[0].startswith(f"c1 | {second['title']} |")
    assert sum(1 for x in lines if f"| {second['title']} |" in x) == 1


async def test_ai_down_is_calm_200(api, ai_on, no_vectors, monkeypatch):
    monkeypatch.setattr(ai, "chat", Spy(ai.AIUnavailable("down")))
    c = api(no_net)
    await signup(c)
    r = await ask(c, "추리 소설")
    assert r.status_code == 200
    assert shop_ai.DOWN_MSG in r.text and "검색으로 찾은 책" in r.text and 'class="ai-card ' in r.text
    monkeypatch.setattr(ai, "chat", Spy(ValueError("boom")))
    r = await ask(c, "추리 소설")
    assert r.status_code == 200 and shop_ai.DOWN_MSG in r.text


async def test_daily_cap(api, ai_on, no_vectors, monkeypatch):
    monkeypatch.setenv("AI_DAILY_REQUESTS", "2")
    get_settings.cache_clear()
    spy = Spy({"reply": "r", "picks": [], "followup": None})
    monkeypatch.setattr(ai, "chat", spy)
    c = api(no_net)
    await signup(c)
    for _ in range(2):
        assert shop_ai.CAP_MSG not in (await ask(c, "소설")).text
    r = await ask(c, "소설")
    assert r.status_code == 200 and "오늘은 여기까지 물어볼 수 있어요" in r.text
    assert len(spy.calls) == 2
    # 같은 한도를 요약 · 초안도 함께 쓴다
    token = await csrf_of(c, "/me")
    assert (await c.post("/me/recap", data={"csrf": token})).status_code == 303
    assert len(spy.calls) == 2


async def test_fake_provider_end_to_end(api, ai_on, no_vectors):
    """가짜 공급자는 JSON 이 아닌 글을 준다 — 그래도 후보 카드가 나온다(미리보기에서 쓰는 길)."""
    c = api(no_net)
    await signup(c)
    r = await ask(c, "", chip=shop_ai.EXAMPLES[0])
    assert r.status_code == 200 and 'class="ai-card ' in r.text and "새 대화" in r.text


async def test_shelve_and_send_only_known_books(api, ai_on, monkeypatch):
    c = api(no_net)
    await signup(c)
    token = await csrf_of(c, "/assistant")
    r = await c.post(
        "/assistant/shelve", data={"csrf": token, "source": "wikisource", "external_id": "없는 책"}
    )
    assert r.status_code == 404
    r = await c.post("/assistant/shelve", data={"csrf": token, "source": "store", "external_id": STORE["id"]})
    assert r.status_code == 404  # 서점 책은 본문이 없어 담지 않는다
    r = await c.post("/assistant/send", data={"csrf": token, "friend_id": 999, "source": "store",
                                               "external_id": STORE["id"]})  # fmt: skip
    assert r.status_code == 404  # 친구가 아니다
    r = await c.post(
        "/assistant/shelve", data={"csrf": "bad", "source": FREE["source"], "external_id": FREE["id"]}
    )
    assert r.status_code in (400, 403)


# --------------------------------------------------------------------------- 이번 달 독서


NOW = dt.datetime(2026, 11, 19, 3, 0, tzinfo=dt.UTC)  # 한국 정오


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(goals, "clock", lambda: NOW)
    return NOW


async def _seed_month(session_factory, me: int, other: int) -> dict:
    async with session_factory() as s:
        a = Book(source="wikisource", external_id="a", title="첫 책", author="갑", total_chunks=3)
        b = Book(source="wikisource", external_id="b", title="둘째 책", author="을", total_chunks=10)
        s.add_all([a, b])
        await s.flush()
        day1 = dt.datetime(2026, 11, 2, 3, tzinfo=dt.UTC)
        day2 = dt.datetime(2026, 11, 5, 3, tzinfo=dt.UTC)
        old = dt.datetime(2026, 10, 20, 3, tzinfo=dt.UTC)
        ev = [
            (me, a.id, 0, day1, True), (me, a.id, 0, day1, True),  # 같은 쪽 두 번 → 1
            (me, a.id, 1, day1, True), (me, a.id, 2, day2, True),  # 첫 책 다 읽음
            (me, b.id, 0, day2, True), (me, b.id, 1, day2, False),  # 검증 안 된 쪽은 빼기
            (me, b.id, 3, old, True),  # 지난달
            (other, a.id, 0, day1, True),  # 남의 기록
        ]  # fmt: skip
        s.add_all(ReadingEvent(user_id=u, book_id=bk, chunk_index=i, dwell_ms=9000, verified=v, occurred_at=t)
                  for u, bk, i, t, v in ev)  # fmt: skip
        s.add_all([
            Highlight(user_id=me, book_id=a.id, chunk_index=0, quote="내가 고른 문장이에요. 두 번째 문장은 잘려요.",
                      created_at=day1),
            Highlight(user_id=other, book_id=a.id, chunk_index=0, quote="남이 고른 비밀 문장", created_at=day1),
            Reaction(user_id=me, book_id=a.id, chunk_index=0, kind=ReactionKind.COMMENT, body="마음이 아팠어요",
                     created_at=day1),
            Reaction(user_id=me, book_id=a.id, chunk_index=1, kind=ReactionKind.EMOJI, body="😢", created_at=day1),
            Reaction(user_id=me, book_id=a.id, chunk_index=2, kind=ReactionKind.EMOJI, body="😢", created_at=day2),
            Reaction(user_id=other, book_id=a.id, chunk_index=0, kind=ReactionKind.COMMENT, body="남의 반응 글",
                     created_at=day1),
            ReadingPost(author_id=me, book_id=a.id, book_source="wikisource", external_id="a", book_title="첫 책",
                        status="done", caption="내 게시물 글이에요.", post_day="2026-11-05", created_at=day2),
            ReadingPost(author_id=other, book_id=a.id, book_source="wikisource", external_id="a",
                        book_title="첫 책", status="done", caption="남의 게시물 글", post_day="2026-11-05",
                        created_at=day2),
            LibraryEntry(user_id=me, book_id=a.id, shelf=Shelf.DONE),
        ])  # fmt: skip
        await s.commit()
        return {"a": a.id, "b": b.id}


async def test_recap_numbers_and_prompt_only_my_data(api, ai_on, frozen, monkeypatch, session_factory):
    await signup(api(no_net), "other@example.com", "남")
    c = api(no_net)
    await signup(c)
    me, other = await user_id(session_factory), await user_id(session_factory, "other@example.com")
    await _seed_month(session_factory, me, other)
    async with session_factory() as s:
        r = await recap.numbers(s, me, recap.Month(2026, 11))
    assert (r.pages, r.days, [b.title for b in r.finished], r.posts, r.reactions) == (4, 2, ["첫 책"], 1, 3)
    assert r.top_emojis == [("😢", 2)]

    page = (await c.get("/me/recap?month=2026-11")).text
    assert "2026년 11월 독서" in page and "AI 요약 만들기" in page

    spy = Spy("이번 달에는 슬픈 이야기에 마음이 갔어요.")
    monkeypatch.setattr(ai, "chat", spy)
    token = await csrf_of(c, "/me")
    res = await c.post("/me/recap", data={"csrf": token, "month": "2026-11"})
    assert res.status_code == 303 and "error" not in res.headers["location"]
    prompt = spy.prompt
    assert "내가 고른 문장이에요." in prompt and "두 번째 문장" not in prompt  # 인용은 한 문장
    assert "마음이 아팠어요" in prompt and "내 게시물 글이에요." in prompt
    for theirs in ("남이 고른 비밀 문장", "남의 반응 글", "남의 게시물 글"):
        assert theirs not in prompt
    page = (await c.get("/me/recap?month=2026-11")).text
    assert "슬픈 이야기에 마음이 갔어요" in page and "AI 요약 · 틀릴 수 있어요" in page

    # 하루에 한 번만 새로 만든다
    res = await c.post("/me/recap", data={"csrf": token, "month": "2026-11"})
    assert "error=" in res.headers["location"] and len(spy.calls) == 1
    monkeypatch.setattr(goals, "clock", lambda: NOW + dt.timedelta(days=1))
    res = await c.post("/me/recap", data={"csrf": token, "month": "2026-11"})
    assert "error" not in res.headers["location"] and len(spy.calls) == 2


async def test_recap_ai_down_is_calm(api, ai_on, frozen, monkeypatch, session_factory):
    await signup(api(no_net), "other@example.com", "남")
    c = api(no_net)
    await signup(c)
    me, other = await user_id(session_factory), await user_id(session_factory, "other@example.com")
    await _seed_month(session_factory, me, other)
    monkeypatch.setattr(ai, "chat", Spy(ai.AIUnavailable("x")))
    token = await csrf_of(c, "/me")
    res = await c.post("/me/recap", data={"csrf": token, "month": "2026-11"})
    assert res.status_code == 303 and "error=" in res.headers["location"]
    assert (await c.get(res.headers["location"])).status_code == 200


async def test_recap_empty_month_does_not_call_ai(api, ai_on, frozen, monkeypatch):
    spy = Spy("x")
    monkeypatch.setattr(ai, "chat", spy)
    c = api(no_net)
    await signup(c)
    token = await csrf_of(c, "/me")
    res = await c.post("/me/recap", data={"csrf": token, "month": "2026-11"})
    assert "error=" in res.headers["location"] and spy.calls == []


def test_parse_month(frozen):
    assert recap.parse_month("2026-03").key == "2026-03"
    for bad in ("", "2026-13", "abc", "2099-01", "1999-12"):
        assert recap.parse_month(bad).key == "2026-11"
    assert recap.Month(2026, 1).shift(-1).key == "2025-12"


def test_one_sentence():
    assert recap.one_sentence("첫 문장. 둘째 문장.") == "첫 문장."
    assert len(recap.one_sentence("가" * 300)) == recap.QUOTE_CHARS


# --------------------------------------------------------------------------- 게시물 초안


async def test_caption_draft_prefills_but_never_posts(api, ai_on, frozen, monkeypatch, session_factory):
    await signup(api(no_net), "other@example.com", "남")
    c = api(no_net)
    await signup(c)
    me, other = await user_id(session_factory), await user_id(session_factory, "other@example.com")
    ids = await _seed_month(session_factory, me, other)

    spy = Spy({"caption": "짧지만 오래 남는 이야기였어요. " * 30, "quote_id": "q1"})
    monkeypatch.setattr(ai, "chat", spy)
    page = (await c.get("/me/recap")).text
    assert "게시물 초안 만들기" in page and f'name="book_id" value="{ids["a"]}"' in page
    token = await csrf_of(c, "/me")
    res = await c.post("/me/recap/draft", data={"csrf": token, "book_id": ids["a"]})
    assert res.status_code == 303
    loc = urlparse(res.headers["location"])
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert loc.path == "/posts/new" and q["step"] == "2" and q["book"] == f"b:{ids['a']}" and q["ai"] == "1"
    assert len(q["caption"]) <= recap.MAX_CAPTION
    assert q["quote"] == "내가 고른 문장이에요."  # 내 하이라이트에서만
    assert "남이 고른 비밀 문장" not in spy.prompt and "남의 반응 글" not in spy.prompt
    async with session_factory() as s:
        assert (
            await s.scalar(select(func.count()).select_from(ReadingPost).where(ReadingPost.author_id == me))
            == 1
        )
    compose = (await c.get(res.headers["location"])).text
    assert "AI가 쓴 초안이에요" in compose and "짧지만 오래 남는 이야기였어요." in compose

    # 고를 수 없는 번호 · 남의 문장은 고르지 않는다
    monkeypatch.setattr(ai, "chat", Spy({"caption": "좋았어요", "quote_id": "q9"}))
    res = await c.post("/me/recap/draft", data={"csrf": token, "book_id": ids["a"]})
    assert "quote" not in parse_qs(urlparse(res.headers["location"]).query)
    # 다 읽지 않은 책은 초안을 만들 수 없다
    res = await c.post("/me/recap/draft", data={"csrf": token, "book_id": ids["b"]})
    assert res.status_code == 404


async def test_caption_draft_ai_down(api, ai_on, frozen, monkeypatch, session_factory):
    await signup(api(no_net), "other@example.com", "남")
    c = api(no_net)
    await signup(c)
    me, other = await user_id(session_factory), await user_id(session_factory, "other@example.com")
    ids = await _seed_month(session_factory, me, other)
    monkeypatch.setattr(ai, "chat", Spy(ai.AIUnavailable("x")))
    token = await csrf_of(c, "/me")
    res = await c.post("/me/recap/draft", data={"csrf": token, "book_id": ids["a"]})
    assert (
        res.status_code == 303
        and res.headers["location"].startswith("/me/recap?")
        and "error=" in res.headers["location"]
    )


async def test_changing_look_keeps_recap_cache(api, ai_on, frozen, monkeypatch, session_factory):
    await signup(api(no_net), "other@example.com", "남")
    c = api(no_net)
    await signup(c)
    me, other = await user_id(session_factory), await user_id(session_factory, "other@example.com")
    await _seed_month(session_factory, me, other)
    monkeypatch.setattr(ai, "chat", Spy("요약이에요."))
    token = await csrf_of(c, "/me")
    await c.post("/me/recap", data={"csrf": token, "month": "2026-11"})
    r = await c.put("/api/v1/me/settings", json={"mode": "light"}, headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    assert "요약이에요." in (await c.get("/me/recap?month=2026-11")).text
