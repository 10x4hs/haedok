"""AI 서점 도우미(/assistant) — 독자가 원하는 책을 말하면, 우리가 가진 책 목록에서만 골라 준다.

한 번 물을 때의 흐름
1. 후보 만들기: 벡터 검색(app/services/vectors.py 가 있으면) + 내 취향 추천 + 낱말 검색(카탈로그 · 서점),
   같은 책은 한 번만, 최대 30권. 후보마다 짧은 id(c1, c2 …)를 붙인다.
2. AI 한 번 부르기(json_mode): {"reply", "picks": [{"id", "why"}], "followup"} — 후보 id 안에서만 3~5권.
3. 받은 id 를 후보와 맞춰 보고 모르는 id 는 버린다. 가격 · 링크는 AI 가 아니라 후보(서점 데이터)에서만 온다.
4. 화면은 답 + 상품 카드(무료면 '무료로 읽기', 서점이면 '서점에서 보기').

대화는 서버에 저장하지 않는다. 지난 차례들을 서명한 값(state)으로 화면이 들고 있다가 다음 질문에 함께 보낸다
(서명이 틀리면 버린다). AI 에게는 최근 8차례만 보낸다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings
from app.models import User
from app.services import ai, bookstore, recs
from app.services.search import FULLTEXT_SOURCES

log = logging.getLogger(__name__)

# 검토하기 쉽게 프롬프트는 여기 한곳에 둔다
ASSISTANT_SYSTEM = """당신은 책 앱 '해독'의 서점 도우미예요. 독자의 말을 듣고 '후보' 목록에서만 책을 골라요.
규칙:
- 후보 id 가운데 3~5권만 골라요. 후보에 없는 책은 절대 말하지 않아요.
- 가격 · 링크 · 할인은 지어내지 않아요.
  가격은 후보에 적힌 것만 말하고, '가격 모름'이면 서점에서 확인하라고 해요.
- 무료 = 앱에서 바로 읽는 책, 서점 = 알라딘에서 사는 전자책이에요. 결제는 앱 밖에서 해요.
- 맞는 후보가 없으면 picks 를 비우고 솔직하게 말해요.
JSON 하나로만 답해요:
{"reply": "두 문장 이내, 쉬운 해요체",
 "picks": [{"id": "c1", "why": "이 독자에게 맞는 이유 한 줄(40자 이내)"}],
 "followup": "취향을 좁히는 짧은 질문 하나, 없으면 null"}"""

MAX_CANDIDATES = 30
MAX_TURNS = 8  # AI 에게 보내는 최근 차례 수(이번 질문 포함)
KEEP_TURNS = 8  # 화면이 들고 있는 차례 수
MAX_Q = 300
MAX_REPLY = 400
MAX_WHY = 80
MAX_FOLLOWUP = 80
PICKS_MIN, PICKS_MAX = 3, 5
FALLBACK_SHOWN = 4

REFINE = ("더 짧게", "무료만", "비슷한 거 더")
EXAMPLES = ("요즘 지쳐서 짧고 따뜻한 소설", "사피엔스 같은 책인데 더 쉬운 거", "선물할 책 2만 원 이하")
CAP_MSG = "오늘은 여기까지 물어볼 수 있어요. 내일 다시 찾아 주세요."
DOWN_MSG = "지금은 AI가 답하지 못해요. 대신 검색으로 찾은 책을 보여 드릴게요."
NONE_MSG = "맞는 책을 찾지 못했어요. 조금 다르게 말해 주실래요?"
FALLBACK_REPLY = "이런 책은 어때요?"
FALLBACK_FOLLOWUP = "어떤 분위기를 좋아하는지 알려 주면 더 골라 볼게요."
UNKNOWN_PRICE = "가격은 서점에서 확인해요"

DAY_S = 86_400

# 낱말 검색이 문장을 알아듣도록: 흔한 말은 버리고, 몇몇 낱말은 서가 이름으로 바꾼다
_STOP = {
    "요즘", "같은", "책", "책인데", "책이", "추천", "추천해", "추천해줘", "좀", "거", "것",
    "하는", "싶어요", "싶은",
    "읽고", "읽을", "만한", "있는", "없는", "더", "주세요", "해줘", "알려줘", "그런", "이런", "뭐", "없을까",
    "이하", "이상", "원", "만", "선물할", "선물", "쉬운", "짧고", "짧은", "길고", "따뜻한", "지쳐서",
}  # fmt: skip
_GENRE_HINTS = {
    "소설": ("단편소설", "장편소설", "중편소설", "고전소설"),
    "짧": ("단편소설", "시", "수필", "동화"),
    "단편": ("단편소설",),
    "장편": ("장편소설",),
    "시": ("시", "시집", "세계 · 시"),
    "시집": ("시집",),
    "수필": ("수필",),
    "에세이": ("수필",),
    "추리": ("세계 · 추리",),
    "탐정": ("세계 · 추리",),
    "sf": ("세계 · SF",),
    "과학": ("세계 · SF",),
    "판타지": ("세계 · 판타지",),
    "동화": ("동화", "세계 · 동화", "세계 · 어린이"),
    "아이": ("동화", "세계 · 어린이"),
    "모험": ("세계 · 모험",),
    "공포": ("세계 · 공포",),
    "사랑": ("세계 · 로맨스",),
    "로맨스": ("세계 · 로맨스",),
    "철학": ("세계 · 철학",),
    "역사": ("세계 · 역사",),
    "희곡": ("희곡", "세계 · 희곡"),
    "고전": ("고전소설", "세계 고전"),
    "따뜻": ("동화", "수필", "단편소설"),
}


# --------------------------------------------------------------------------- 링크 · 가격


def affiliate(url: str) -> str:
    """바깥 서점으로 나가는 주소를 만드는 유일한 곳. 제휴 값이 생기면 여기서만 붙인다(지금은 그대로)."""
    return url


def free_href(source: str, external_id: str) -> str:
    return f"/open/{source}/{quote(external_id)}"


def store_href(item_id: str) -> str:
    return f"/store/{quote(item_id, safe='')}"


def _store_item(item_id: str) -> dict | None:
    """서점 데이터에 있는 책 — 고른 책 → 베스트셀러 → 실시간 조회 캐시. 없으면 None(지어내지 않는다)."""
    from app import web

    if book := web.STORE_INDEX.get(item_id) or web.BEST_INDEX.get(item_id):
        return book
    hit, cached = bookstore.cached_lookup(item_id)
    return cached if hit and cached else None


def price_of(item: dict | None) -> int | None:
    """서점 데이터의 값만. 0 · 음수 · 숫자가 아닌 값은 '모름'.

    ISBN 으로 찾은 책(카카오)의 price 는 종이책 판매가라 전자책 서점 가격으로 보여 주지 않는다."""
    if bookstore.is_isbn(str((item or {}).get("id") or "")):
        return None
    p = (item or {}).get("price")
    return p if isinstance(p, int) and not isinstance(p, bool) and p > 0 else None


# --------------------------------------------------------------------------- 후보


@dataclass
class Candidate:
    kind: str  # "free"(앱에서 바로 읽기) | "store"(서점)
    source: str
    external_id: str
    title: str
    author: str | None = None
    cover: str | None = None
    price: int | None = None
    href: str = ""
    note: str = ""  # 서가 · 분류(AI 가 고를 때 참고)
    reason: str = ""  # 검색이 이 책을 고른 까닭(AI 가 없을 때 보여 준다)
    id: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.external_id)

    def line(self) -> str:
        where = (
            "무료" if self.kind == "free" else (f"서점 {self.price:,}원" if self.price else "서점 가격 모름")
        )
        parts = [self.id, self.title, self.author or "지은이 모름", where]
        if self.note:
            parts.append(self.note)
        return " | ".join(p.replace("|", "/").replace("\n", " ") for p in parts)


def _safe_cover(url: str | None) -> str | None:
    url = (url or "").strip()
    if url.startswith("https://") or (url.startswith("/static/") and ".." not in url):
        return url[:1000]
    return None


def _from_catalog(b: dict, reason: str = "") -> Candidate:
    return Candidate(
        "free", b["source"], str(b["id"]), b["title"], b.get("author"), _safe_cover(b.get("cover")),
        None, free_href(b["source"], str(b["id"])), b.get("genre") or "", reason,
    )  # fmt: skip


def _from_store(b: dict, reason: str = "") -> Candidate | None:
    item_id = str(b.get("id") or "")
    if not bookstore.valid_id(item_id) or not b.get("title"):
        return None
    note = b.get("category") or ""
    return Candidate(
        "store", "store", item_id, b["title"], b.get("author"), _safe_cover(b.get("cover")),
        price_of(b), store_href(item_id), note, reason,
    )  # fmt: skip


def _from_ref(ref) -> Candidate | None:
    """vectors.BookRef → 후보.

    무료 책은 본문을 줄 수 있는 소스만, 서점 책은 서점 데이터에 있는 것만(가격도 거기서)."""
    source, ext = str(getattr(ref, "source", "") or ""), str(getattr(ref, "external_id", "") or "")
    title = getattr(ref, "title", None)
    if not (source and ext and title):
        return None
    reason = str(getattr(ref, "reason", "") or "")[:MAX_WHY]
    if getattr(ref, "free", False) and source in FULLTEXT_SOURCES:
        from app import web

        meta = web.CATALOG_INDEX.get((source, ext)) or {}
        cover = _safe_cover(getattr(ref, "cover", None)) or _safe_cover(meta.get("cover"))
        return Candidate("free", source, ext, title, getattr(ref, "author", None), cover, None,
                         free_href(source, ext), meta.get("genre") or "", reason)  # fmt: skip
    if source == "store":
        item = _store_item(ext) or {}
        c = _from_store({**item, "id": ext, "title": item.get("title") or title,
                         "author": item.get("author") or getattr(ref, "author", None),
                         "cover": item.get("cover") or getattr(ref, "cover", None)}, reason)  # fmt: skip
        if c is not None:
            c.price = price_of({**item, "id": ext})  # BookRef 의 값이 아니라 서점 데이터의 값만
        return c
    return None


def tokens(text: str) -> list[str]:
    words = re.findall(r"[0-9A-Za-z가-힣]+", (text or "").lower())
    out: list[str] = []
    for w in words:
        w = (
            re.sub(
                r"(이에요|예요|인데|이랑|같은|처럼|하고|에서|으로|는|은|이|가|을|를|도|의|로|와|과)$", "", w
            )
            or w
        )
        if len(w) >= 2 and w not in _STOP and w not in out:
            out.append(w)
    return out[:8]


def _genres_for(text: str) -> Counter[str]:
    """서가 → 그 서가를 가리킨 낱말 수('짧고 따뜻한 소설'이면 단편소설이 셋)."""
    low = (text or "").lower()
    out: Counter[str] = Counter()
    for hint, genres in _GENRE_HINTS.items():
        if hint in low:
            out.update(genres)
    return out


async def keyword_books(
    session: AsyncSession, client: httpx.AsyncClient, user: User, text: str
) -> list[Candidate]:
    """벡터 검색이 없을 때(또는 함께) 쓰는 낱말 검색: /search 와 같은 곳(무료 카탈로그 · 서점)."""
    from app import web

    words = tokens(text)
    genres = _genres_for(text)
    # '소설' · '시' 같은 갈래 낱말은 서가로만 맞춘다(제목에 '소설'이 든 평론이 앞서지 않게)
    title_words = [w for w in words if w not in _GENRE_HINTS]
    famous = {k: i for i, k in enumerate(web.FAMOUS_FREE_KEYS)}

    scored: list[tuple[int, int, bool, dict]] = []
    for b in web.CATALOG:
        title, author = b["title"].lower(), (b.get("author") or "").lower()
        named = sum(3 for w in title_words if w in title or w in author)
        score = named + genres.get(b.get("genre"), 0)
        if score:
            scored.append((-score, famous.get((b["source"], b["id"]), 10_000), bool(named), b))
    scored.sort(key=lambda x: (x[0], x[1]))
    out = [
        _from_catalog(b, "찾는 말과 맞아요" if named else f"{b.get('genre')} 서가")
        for _, _, named, b in scored[:14]
    ]

    curated = list(web.STORE) + list(web.BESTSELLERS)
    local: list[dict] = []
    for w in words:
        local += bookstore.local_search(curated, w, 6)
    live: list = []
    if words:
        try:  # 카카오 · 알라딘 — 캐시 · 사용자별 한도를 /search 와 함께 쓴다. 실패하면 빈 목록
            live = await web._store_search(session, client, user, " ".join(words[:3]))
        except Exception:  # noqa: BLE001 - 서점이 죽어도 도우미는 산다
            log.warning("assistant store search failed")
            live = []
    for b in bookstore.merge(local, live, web.STORE_INDEX, 12):
        if c := _from_store(b, "서점에서 찾았어요"):
            out.append(c)
    return out


def _popular_fill() -> list[Candidate]:
    """후보가 모자랄 때 채우는 유명한 책(무료 고전 + 서점) — 날마다 같은 순서."""
    from app import web

    free, store = web._famous_pool()
    out = [_from_catalog(b, "많이 읽는 고전") for b in free[:10]]
    out += [c for b in store[:10] if (c := _from_store(b, "많이 찾는 책"))]
    return out


async def candidates(
    session: AsyncSession,
    client: httpx.AsyncClient,
    user: User,
    text: str,
    *,
    free_only: bool = False,
    shown: set[tuple[str, str]] | None = None,
) -> list[Candidate]:
    """후보 최대 30권: 벡터(비슷한 책 · 내 취향) → 낱말 검색 → 유명한 책. 같은 책은 한 번만."""
    from app import web

    found: list[Candidate] = []
    calls = [lambda: recs.for_user(session, user.id, k=8)]  # 저장된 벡터만 — AI 호출 없음
    if not await over_cap(session, user):  # 질문 임베딩은 AI 호출이라 한도가 남았을 때만
        calls.insert(0, lambda: recs.similar_books(session, text, k=10, exclude_user=user.id))
    for call in calls:
        try:
            async with session.begin_nested():  # 실패해도 바깥 트랜잭션은 살아 있게(Postgres)
                found += [c for ref in await call() if (c := _from_ref(ref))]
        except Exception:  # noqa: BLE001 - 임베딩이 안 되면 낱말 검색으로
            log.warning("assistant vector search failed", exc_info=True)
    found += await keyword_books(session, client, user, text)
    found += _popular_fill()

    out: list[Candidate] = []
    keys: set[tuple[str, str]] = set()
    titles: set[str] = set()
    later: list[Candidate] = []  # 이미 보여 준 책 — 다른 후보가 모자랄 때만
    for c in found:
        title_key = web._title_key(c.title)
        if c.key in keys or title_key in titles or (free_only and c.kind != "free"):
            continue
        keys.add(c.key)
        titles.add(title_key)
        (later if shown and c.key in shown else out).append(c)
    if len(out) < PICKS_MAX:
        out += later
    out = out[:MAX_CANDIDATES]
    for i, c in enumerate(out, 1):
        c.id = f"c{i}"
    return out


# --------------------------------------------------------------------------- AI 답 읽기


@dataclass
class Pick:
    kind: str
    source: str
    external_id: str
    title: str
    author: str | None
    cover: str | None
    price: int | None
    href: str
    why: str = ""

    @classmethod
    def of(cls, c: Candidate, why: str) -> Pick:
        return cls(c.kind, c.source, c.external_id, c.title, c.author, c.cover, c.price, c.href, why)


@dataclass
class Turn:
    q: str
    reply: str
    picks: list[Pick] = field(default_factory=list)
    followup: str | None = None
    note: str = "ai"  # ai | fallback(AI 답을 읽지 못함) | down(AI 꺼짐 · 오류) | cap(오늘 한도)


def _clip(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n] if isinstance(v, str) else ""


def _json_obj(text: str) -> dict | None:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    for chunk in (t, t[t.find("{") : t.rfind("}") + 1] if "{" in t else ""):
        if not chunk:
            continue
        try:
            data = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def read_answer(q: str, text: str, cands: list[Candidate]) -> Turn:
    """AI 답 → 한 차례. 후보에 없는 id 는 버린다. JSON 이 아니면 AI 글은 버리고 검색 후보 앞쪽을 보여 준다."""
    index = {c.id: c for c in cands}
    data = _json_obj(text)
    if data is None:  # 형식이 틀린 답은 보여 주지 않는다(지어낸 책 · 가격이 섞여 있을 수 있다)
        picks = [Pick.of(c, c.reason) for c in cands[:FALLBACK_SHOWN]]
        return Turn(q, FALLBACK_REPLY, picks, FALLBACK_FOLLOWUP if picks else None, "fallback")
    picks: list[Pick] = []
    seen: set[str] = set()
    raw_picks = data.get("picks")
    for p in raw_picks if isinstance(raw_picks, list) else []:
        cid = p.get("id") if isinstance(p, dict) else p
        if not isinstance(cid, str) or cid not in index or cid in seen:
            continue  # 모르는 id(지어낸 책)는 버린다
        seen.add(cid)
        picks.append(Pick.of(index[cid], _clip(p.get("why") if isinstance(p, dict) else "", MAX_WHY)))
        if len(picks) >= PICKS_MAX:
            break
    reply = _clip(data.get("reply"), MAX_REPLY) or (FALLBACK_REPLY if picks else NONE_MSG)
    followup = _clip(data.get("followup"), MAX_FOLLOWUP) or None
    return Turn(q, reply, picks, followup, "ai")


def messages_for(turns: list[Turn], q: str, cands: list[Candidate]) -> list[dict]:
    """AI 에게 보내는 대화: 최근 차례들(질문 + 답 + 고른 책 제목) + 이번 질문과 후보 목록."""
    out: list[dict] = []
    for t in turns[-(MAX_TURNS - 1) :]:
        out.append({"role": "user", "content": t.q})
        named = ", ".join(p.title for p in t.picks)
        out.append({"role": "assistant", "content": t.reply + (f" (보여 준 책: {named})" if named else "")})
    listing = "\n".join(c.line() for c in cands)
    out.append({"role": "user", "content": f"후보:\n{listing}\n\n요청: {q}"})
    return out


# --------------------------------------------------------------------------- 한 차례


async def over_cap(session: AsyncSession, user: User) -> bool:
    """오늘 한도를 이미 다 썼나(세지 않고 보기만)."""
    return await ratelimit.used(session, f"ai-day:{user.id}", DAY_S) >= get_settings().ai_daily_requests


async def spend(session: AsyncSession, user: User) -> None:
    """AI 대화 모델을 부르기 전에 하루 한도를 센다(넘으면 RateLimited).

    하루 = UTC 하루(한국 오전 9시에 새로)."""
    s = get_settings()
    await ratelimit.check(session, f"ai-day:{user.id}", s.ai_daily_requests, DAY_S, CAP_MSG)


def _context(turns: list[Turn], q: str) -> str:
    """검색어: 이번 말 + 바로 앞 질문(‘더 짧게’ 같은 짧은 말도 무엇을 찾던 중인지 알도록)."""
    prev = next((t.q for t in reversed(turns) if t.q not in REFINE), "")
    return f"{prev} {q}".strip() if q in REFINE or len(q) < 12 else q


async def ask(
    session: AsyncSession, client: httpx.AsyncClient, user: User, q: str, turns: list[Turn]
) -> Turn:
    q = _clip(q, MAX_Q)
    shown = {(p.source, p.external_id) for t in turns for p in t.picks}
    free_only = q == "무료만" or "무료" in q
    cands = await candidates(session, client, user, _context(turns, q), free_only=free_only, shown=shown)
    if not cands:  # 고를 책이 없으면 AI 를 부르지 않는다(한도도 쓰지 않는다)
        return Turn(q, NONE_MSG, [], None, "fallback")
    fallback = [Pick.of(c, c.reason) for c in cands[:FALLBACK_SHOWN]]
    try:
        await spend(session, user)
    except ratelimit.RateLimited:
        return Turn(q, CAP_MSG, fallback, None, "cap")
    try:
        res = await ai.chat(
            messages_for(turns, q, cands),
            system=ASSISTANT_SYSTEM,
            max_tokens=450,
            json_mode=True,
            http=client,
        )
    except ai.AIUnavailable:
        return Turn(q, DOWN_MSG, fallback, None, "down")
    except Exception:  # noqa: BLE001 - 어떤 오류든 화면은 산다
        log.exception("assistant ai call failed")
        return Turn(q, DOWN_MSG, fallback, None, "down")
    return read_answer(q, res.text, cands)


# --------------------------------------------------------------------------- 대화 상태(서명한 값)


def _mac(raw: bytes) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, b"ai-shop-state:" + raw, hashlib.sha256).hexdigest()[:32]


def pack(user_id: int, turns: list[Turn]) -> str:
    body = {"u": user_id, "t": [asdict(t) for t in turns[-KEEP_TURNS:]]}
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + _mac(raw)


def unpack(user_id: int, value: str | None) -> list[Turn]:
    """서명이 맞고 이 사람의 것일 때만. 그 밖에는 빈 대화."""
    if not value or "." not in value or len(value) > 200_000:
        return []
    body, _, mac = value.rpartition(".")
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except ValueError:
        return []
    if not hmac.compare_digest(_mac(raw), mac):
        return []
    try:
        data = json.loads(raw)
        if data.get("u") != user_id:
            return []
        return [
            Turn(
                t["q"],
                t["reply"],
                [Pick(**p) for p in t.get("picks") or []],
                t.get("followup"),
                t.get("note", "ai"),
            )
            for t in data.get("t") or []
        ][-KEEP_TURNS:]
    except (ValueError, TypeError, KeyError, AttributeError):
        return []


def chips(turn: Turn | None) -> list[str]:
    """고쳐 묻기 빠른 답(더 짧게 · 무료만 · 비슷한 거 더). 되묻기(followup)는 화면이 따로 보여 준다."""
    if turn is None or turn.note == "cap" or not turn.picks:
        return []
    return [r for r in REFINE if r != turn.q]


# --------------------------------------------------------------------------- 카드 행동


def book_of(source: str, external_id: str) -> dict | None:
    """보내기 · 담기에 쓰는 책 정보 — 우리 데이터에 있는 책만(제목을 화면에서 받지 않는다)."""
    from app import web

    if source == "store":
        item = _store_item(external_id)
        if item and bookstore.valid_id(external_id):
            return {"title": item["title"], "author": item.get("author"), "href": store_href(external_id)}
        return None
    if source in FULLTEXT_SOURCES and (meta := web.CATALOG_INDEX.get((source, external_id))):
        return {"title": meta["title"], "author": meta.get("author"), "href": free_href(source, external_id)}
    return None


def share_text(book: dict) -> str:
    by = f" · {book['author']}" if book.get("author") else ""
    return f"AI 서점 도우미가 골라 준 책이에요 — 『{book['title']}』{by} {book['href']}"
