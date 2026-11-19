"""나를 위한 다음 책 — 취향 벡터와 가까운, 아직 서재에 없는 책.

취향 벡터 = 내 서재 · 게시물 책 벡터의 가중 평균(다 읽음 1.0 · 읽는 중 0.6 · 별점 4 이상 +0.5 · 2 이하 -0.5).
사람마다 ai_tastes 에 캐시하고, 서재 · 게시물이 캐시보다 새로 바뀌었으면 읽을 때 다시 계산한다.
추천 이유는 LLM 없이 만든다: 내 기록 가운데 그 책과 가장 가까운 책 — "「데미안」을 좋아했다면".
서재가 비었으면(콜드 스타트) /discover 의 유명한 책 풀에서 고른다.

다른 기능(AI 서점 도우미 · 독서 요약)이 가져다 쓰는 공개 함수: similar_books, for_user, BookRef.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Book, LibraryEntry, Shelf, User
from app.models_ai import BookVector, UserTaste, pack, unpack
from app.models_posts import ReadingPost
from app.services import ai, vectors

log = logging.getLogger("haedok.recs")

W_DONE, W_READING = 1.0, 0.6
W_LIKED, W_DISLIKED = 0.5, -0.5


@dataclass
class BookRef:
    source: str  # "wikisource" · "gutendex" … (바로 읽는 고전) 또는 "store"(서점 책)
    external_id: str
    title: str
    author: str | None
    cover: str | None
    free: bool  # True = 앱에서 바로 읽는다(/open/…), False = 서점 화면(/store/…)에서 구매 링크
    href: str
    score: float
    reason: str | None = None


def ref_of(source: str, external_id: str, score: float = 0.0, reason: str | None = None) -> BookRef | None:
    """카탈로그 · 서점 목록에 실제로 있는 책만 BookRef 로 만든다.

    없는 책은 None — 지어낸 책이 화면에 나가지 않는다.
    """
    from app import web

    if source == vectors.STORE:
        b = web.STORE_INDEX.get(external_id)
        if b is None:
            return None
        return BookRef(
            source,
            external_id,
            b["title"],
            b.get("author"),
            b.get("cover"),
            False,
            f"/store/{quote(external_id, safe='')}",
            score,
            reason,
        )
    b = web.CATALOG_INDEX.get((source, external_id))
    if b is None:
        return None
    return BookRef(
        source,
        external_id,
        b["title"],
        b.get("author"),
        b.get("cover"),
        True,
        f"/open/{source}/{quote(external_id)}",
        score,
        reason,
    )


# --- 내 기록 ------------------------------------------------------------------------------


@dataclass
class History:
    weights: dict[tuple[str, str], float] = field(default_factory=dict)
    status: dict[tuple[str, str], str] = field(default_factory=dict)  # done | reading
    titles: dict[tuple[str, str], str] = field(default_factory=dict)
    title_keys: set[str] = field(default_factory=set)  # 판본만 다른 같은 책을 다시 권하지 않으려고
    basis_at: dt.datetime | None = None

    @property
    def keys(self) -> set[tuple[str, str]]:
        return set(self.titles)


def title_key(title: str) -> str:
    """'데미안 (한글판)' == '데미안' — 괄호 · 권 번호 · 띄어쓰기를 떼고 견준다."""
    t = re.sub(r"\s*[\(\[（].*?[\)\]）]\s*", " ", title or "")
    t = re.sub(r"\s+(\d{1,2}|상|하)$", "", t.strip())
    return re.sub(r"\s+", "", t).lower()


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def _later(a: dt.datetime | None, b: dt.datetime | None) -> dt.datetime | None:
    a, b = _aware(a), _aware(b)
    if a is None or b is None:
        return a or b
    return max(a, b)


async def history(session: AsyncSession, user_id: int) -> History:
    h = History()
    shelf_w: dict[tuple[str, str], float] = {}
    rating: dict[tuple[str, str], int] = {}
    rows = await session.execute(
        select(
            Book.source,
            Book.external_id,
            Book.title,
            Book.owner_id,
            LibraryEntry.shelf,
            LibraryEntry.updated_at,
        )
        .join(LibraryEntry, LibraryEntry.book_id == Book.id)
        .where(LibraryEntry.user_id == user_id)
    )
    for source, eid, title, owner, shelf, updated in rows:
        h.basis_at = _later(h.basis_at, updated)
        h.title_keys.add(title_key(title))
        if owner is not None:
            continue  # 내가 올린 파일 — 책 벡터가 없고, 밖으로 보낼 일도 없다
        key = (source, eid)
        h.titles[key] = title
        w = W_DONE if shelf == Shelf.DONE else W_READING if shelf == Shelf.READING else 0.0
        shelf_w[key] = max(shelf_w.get(key, 0.0), w)
        if w:
            h.status[key] = "done" if shelf == Shelf.DONE else "reading"
    posts = await session.execute(
        select(
            ReadingPost.book_source,
            ReadingPost.external_id,
            ReadingPost.book_title,
            ReadingPost.status,
            ReadingPost.rating,
            ReadingPost.created_at,
            ReadingPost.updated_at,
        )
        .where(ReadingPost.author_id == user_id, ReadingPost.deleted_at.is_(None))
        .order_by(ReadingPost.created_at, ReadingPost.id)
    )
    for source, eid, title, status, stars, created, updated in posts:
        h.basis_at = _later(h.basis_at, _later(created, updated))
        key = (source, eid)
        h.titles.setdefault(key, title)
        h.title_keys.add(title_key(title))
        w = W_DONE if status == "done" else W_READING if status == "reading" else 0.0
        if w > shelf_w.get(key, 0.0):
            shelf_w[key] = w
            h.status[key] = "done" if status == "done" else "reading"
        if stars is not None:
            rating[key] = stars  # 같은 책에 여러 번 올렸으면 마지막 별점
    for key in h.titles:
        w = shelf_w.get(key, 0.0)
        stars = rating.get(key)
        if stars is not None and stars >= 4:
            w += W_LIKED
        elif stars is not None and stars <= 2:
            w += W_DISLIKED
        if w:
            h.weights[key] = w
        if stars is not None and stars >= 4:
            h.status[key] = "done"
    return h


def weighted_mean(
    weights: dict[tuple[str, str], float], vecs: dict[tuple[str, str], list[float]]
) -> list[float] | None:
    """Σ w·v 를 단위 벡터로. 좋아한 쪽(양의 무게)이 하나도 없으면 None."""
    used = [(w, vecs[k]) for k, w in weights.items() if k in vecs]
    if not any(w > 0 for w, _ in used):
        return None
    dim = len(used[0][1])
    acc = [0.0] * dim
    for w, v in used:
        if len(v) != dim:
            continue
        for i, x in enumerate(v):
            acc[i] += w * x
    n = math.sqrt(sum(x * x for x in acc))
    return [x / n for x in acc] if n > 1e-9 else None


async def taste_of(session: AsyncSession, user_id: int, h: History) -> list[float] | None:
    """캐시가 기록보다 새로우면 캐시, 아니면 다시 계산해 넣는다."""
    cached = await session.get(UserTaste, user_id)
    # 기록이 바뀌었거나 관리자가 책 벡터를 더 채웠으면(updated_at) 다시 계산한다
    newest_vec = await session.scalar(select(func.max(BookVector.updated_at)))
    if (
        cached is not None
        and _aware(cached.basis_at) is not None
        and _aware(cached.basis_at) >= _aware(h.basis_at)
        and (newest_vec is None or _aware(cached.computed_at) >= _aware(newest_vec))
    ):
        return unpack(cached.vec) if cached.vec else None
    vecs = await vectors.book_vectors_for(session, h.weights)
    taste = weighted_mean(h.weights, vecs)
    if not vecs:  # 책 벡터를 아직 안 채웠다 — 채운 뒤 다시 계산되도록 캐시하지 않는다
        return None
    if cached is None:
        cached = UserTaste(user_id=user_id)
        session.add(cached)
    cached.vec = pack(taste) if taste else None
    cached.basis_at = h.basis_at
    cached.computed_at = dt.datetime.now(dt.UTC)
    await session.commit()
    return taste


# --- 이유 한 줄(LLM 없이) -------------------------------------------------------------------


def _particle(word: str) -> str:
    """을/를 — 받침이 있으면 '을'. 한글이 아니면 '을(를)'."""
    c = word.strip()[-1:] if word.strip() else ""
    if "가" <= c <= "힣":
        return "을" if (ord(c) - 0xAC00) % 28 else "를"
    return "을(를)"


def short_title(title: str) -> str:
    t = re.sub(r"\s*[\(\[（].*?[\)\]）]\s*$", "", title or "").strip() or title
    return t if len(t) <= 18 else t[:17] + "…"


def reason_for(title: str, status: str | None) -> str:
    t = short_title(title)
    verb = "읽고 있다면" if status == "reading" else "좋아했다면"
    return f"「{t}」{_particle(t)} {verb}"


def _nearest_liked(vec: list[float] | None, h: History, hist_vecs: dict) -> str | None:
    if not vec:
        return None
    best, best_s = None, -2.0
    for key, w in h.weights.items():
        hv = hist_vecs.get(key)
        if w <= 0 or not hv or len(hv) != len(vec):
            continue
        s = ai.cosine(vec, hv)
        if s > best_s:
            best, best_s = key, s
    return reason_for(h.titles[best], h.status.get(best)) if best else None


# --- 공개 함수 ---------------------------------------------------------------------------


def _rank(refs: list[BookRef], k: int) -> list[BookRef]:
    """가까운 순. 점수가 같으면(소수 셋째 자리) 바로 읽는 고전이 먼저."""
    refs.sort(key=lambda r: (-round(r.score, 3), not r.free, r.title))
    return refs[:k]


def _pick(hits: list[vectors.Hit], h: History | None, k: int, reason=None) -> list[BookRef]:
    seen_titles = set(h.title_keys) if h else set()
    shelved = h.keys if h else set()
    out: list[BookRef] = []
    for hit in hits:
        key = (hit.source, hit.external_id)
        tk = title_key(hit.title)
        if key in shelved or tk in seen_titles:
            continue
        ref = ref_of(hit.source, hit.external_id, hit.score, reason(hit) if reason else None)
        if ref is None:
            continue
        seen_titles.add(tk)
        out.append(ref)
    return _rank(out, k)


async def similar_books(
    session: AsyncSession, text: str, k: int = 8, exclude_user: int | None = None
) -> list[BookRef]:
    """글(질문 · 소개 · 제목)과 가까운 카탈로그 · 서점 책 k 권.

    exclude_user 가 주어지면 그 사람이 서재에 꽂았거나 게시물로 올린 책은 뺀다.

    글을 임베딩하므로 AI 가 꺼져 있거나 응답이 없으면 ai.AIUnavailable 을 던진다(부르는 쪽이 조용히 처리).
    """
    text = (text or "").strip()[:1000]
    if not text or k <= 0:
        return []
    query = (await ai.embed([text]))[0]
    h = await history(session, exclude_user) if exclude_user is not None else None
    hits = await vectors.nearest_books(session, query, k + (len(h.title_keys) if h else 0) + 10)
    return _pick(hits, h, k)


async def for_user(session: AsyncSession, user_id: int, k: int = 12) -> list[BookRef]:
    """이 사람을 위한 다음 책 k 권 — AI 호출 없이 저장된 벡터만 쓴다. 기록이 없으면 유명한 책."""
    h = await history(session, user_id)
    taste = await taste_of(session, user_id, h) if h.weights else None
    if taste is None:
        return cold_start(h, k)
    hits = await vectors.nearest_books(session, taste, k + len(h.title_keys) + 20)
    hist_vecs = await vectors.book_vectors_for(session, [key for key, w in h.weights.items() if w > 0])
    picked = _pick(hits, h, k, reason=lambda hit: _nearest_liked(hit.vec, h, hist_vecs))
    return picked or cold_start(h, k)


def cold_start(h: History | None, k: int) -> list[BookRef]:
    """/discover 가 쓰는 유명한 책 풀 — 바로 읽는 고전 먼저, 그다음 서점 책. 이미 꽂은 책은 뺀다."""
    from app import web

    free, store = web._famous_pool()
    seen = set(h.title_keys) if h else set()
    out: list[BookRef] = []
    for b, src in [(b, b["source"]) for b in free] + [(b, vectors.STORE) for b in store]:
        tk = title_key(b["title"])
        if tk in seen or (h and (src, str(b["id"])) in h.keys):
            continue
        ref = ref_of(src, str(b["id"]))
        if ref is None and src == vectors.STORE:  # 베스트셀러 · 홈 추천은 store.json 밖일 수 있다
            ref = BookRef(
                src,
                str(b["id"]),
                b["title"],
                b.get("author"),
                b.get("cover"),
                False,
                f"/store/{quote(str(b['id']), safe='')}",
                0.0,
                None,
            )
        if ref is None:
            continue
        seen.add(tk)
        out.append(ref)
        if len(out) >= k:
            break
    return out


# --- 화면에서 쓰는 한 줄 ---------------------------------------------------------------------


def on() -> bool:
    s = get_settings()
    return s.feature_ai_rag and ai.enabled()


@dataclass
class Row:
    books: list[BookRef]
    cold: bool  # 기록이 없어 유명한 책으로 채웠다


async def row(session: AsyncSession, user: User | None, k: int = 12) -> Row | None:
    """탐색 · 프로필의 '나를 위한 다음 책' 한 줄. 꺼져 있거나 무엇이든 실패하면 None — 화면은 그대로 뜬다."""
    if user is None or not on():
        return None
    try:
        books = await for_user(session, user.id, k)
    except Exception:  # noqa: BLE001 — 추천은 덤이다. 화면을 깨뜨리지 않는다
        log.exception("recs row failed")
        await session.rollback()
        return None
    if not books:
        return None
    return Row(books, cold=all(b.reason is None for b in books))
