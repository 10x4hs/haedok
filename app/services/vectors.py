"""벡터 저장소 — Postgres(pgvector)와 SQLite(파이썬 코사인)의 차이를 여기서만 안다.

- 책 벡터(book_vectors): 카탈로그(퍼블릭 도메인 고전) + 서점 책의 메타데이터(제목 · 저자 · 갈래 · 연도).
  scripts/build_book_vectors.py 나 /admin/ai 버튼이 한 번에 BATCH 권씩 채운다. 글이 같으면(해시) 건너뛴다.
- 조각 벡터(chunk_vectors): 책 본문 한 쪽(청크)을 PART_CHARS 안팎으로 자른 조각. 누가 그 책에 처음
  물어볼 때 그 사람이 읽은 쪽까지만, 한 번에 CHUNK_BUDGET 조각까지 만든다(나머지는 다음 질문 때).

Postgres 쿼리는 모두 text() + 바인드 인자 + CAST(:q AS vector) 로 쓴다 — pgvector 파이썬 패키지 없이
asyncpg 가 벡터를 글자('[0.1,0.2,…]')로 주고받는다. 거리 연산자 <=> 는 코사인 거리(1 - 유사도)다.
페이지를 그리는 길에서는 임베딩(AI 호출)을 하지 않는다 — 저장된 벡터만 읽는다.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Book, BookChunk
from app.models_ai import BookVector, ChunkVector, pack
from app.services import ai

BATCH = 100  # 관리자 버튼 한 번에 채우는 책 수
CHUNK_BUDGET = 120  # 질문 한 번에 새로 만드는 본문 조각 수
PART_CHARS = 500  # 본문 조각 크기(글자) — 청크(1,200~1,800자)를 서너 조각으로
STORE = "store"  # 서점 책의 source


# --- 공통 ------------------------------------------------------------------------------


def dialect_of(session: AsyncSession) -> str:
    bind = session.get_bind()
    return bind.dialect.name


def pg_literal(vec: list[float]) -> str:
    """pgvector 글자 표기 '[0.1,0.2,…]' — CAST(:v AS vector) 로 넘긴다."""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def parse_pg(value: str | list | None) -> list[float]:
    if value is None:
        return []
    if isinstance(value, str):
        return [float(x) for x in value.strip("[]").split(",") if x]
    return [float(x) for x in value]


@dataclass
class Hit:
    source: str
    external_id: str
    title: str
    score: float  # 코사인 유사도(클수록 가깝다)
    vec: list[float] | None = None


# --- 책 벡터: 무엇을 임베딩하나 -----------------------------------------------------------


@dataclass
class Item:
    source: str
    external_id: str
    title: str
    text: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]


def catalogue_items() -> list[Item]:
    """카탈로그 + 서점 책 — 화면이 쓰는 목록 그대로(app/web.py). 순서는 늘 같다."""
    from app import web  # web 이 이 모듈을 쓰므로 여기서 늦게 부른다

    out: list[Item] = []
    for b in web.CATALOG:
        out.append(Item(b["source"], str(b["id"]), b["title"], describe(b)))
    for b in web.STORE:
        out.append(Item(STORE, str(b["id"]), b["title"], describe(b)))
    return out


def describe(b: dict) -> str:
    """임베딩할 짧은 소개 — 제목 · 저자 · 갈래 · 연도 · (있으면) 한두 줄 설명."""
    lines = [f"제목: {b['title']}"]
    if b.get("author"):
        lines.append(f"저자: {b['author']}")
    if b.get("genre"):
        lines.append(f"갈래: {b['genre']}")
    if b.get("year"):
        lines.append(f"연도: {b['year']}")
    desc = b.get("description") or b.get("desc")
    if desc:
        lines.append(str(desc)[:300])
    return "\n".join(lines)


# --- 책 벡터: 채우기 ----------------------------------------------------------------------


@dataclass
class Progress:
    done: int  # 지금 글과 해시가 맞는 벡터 수
    total: int
    added: int = 0  # 이번에 새로 만든 수

    @property
    def pct(self) -> int:
        return round(self.done * 100 / self.total) if self.total else 100


async def _hashes(session: AsyncSession) -> dict[tuple[str, str], str]:
    rows = await session.execute(select(BookVector.source, BookVector.external_id, BookVector.content_hash))
    return {(s, e): h for s, e, h in rows}


async def book_progress(session: AsyncSession, items: list[Item] | None = None) -> Progress:
    items = items if items is not None else catalogue_items()
    have = await _hashes(session)
    done = sum(have.get((i.source, i.external_id)) == i.digest for i in items)
    return Progress(done, len(items))


async def fill_books(session: AsyncSession, limit: int = BATCH, items: list[Item] | None = None) -> Progress:
    """아직 없거나 글이 바뀐 책을 limit 권까지 임베딩해 넣는다. AI 가 안 되면 ai.AIUnavailable."""
    items = items if items is not None else catalogue_items()
    have = await _hashes(session)
    todo = [i for i in items if have.get((i.source, i.external_id)) != i.digest][
        : max(0, min(limit, BATCH * 10))
    ]
    if todo:
        vecs = await ai.embed([i.text for i in todo])
        await upsert_books(session, [(i, v) for i, v in zip(todo, vecs, strict=True)])
    done = sum(have.get((i.source, i.external_id)) == i.digest for i in items) + len(todo)
    return Progress(done, len(items), added=len(todo))


PG_UPSERT_BOOK = text(
    "INSERT INTO book_vectors (source, external_id, title, content_hash, vec, updated_at) "
    "VALUES (:source, :external_id, :title, :content_hash, CAST(:vec AS vector), now()) "
    "ON CONFLICT (source, external_id) DO UPDATE SET title = EXCLUDED.title, "
    "content_hash = EXCLUDED.content_hash, vec = EXCLUDED.vec, updated_at = now()"
)


async def upsert_books(session: AsyncSession, rows: Iterable[tuple[Item, list[float]]]) -> None:
    rows = list(rows)
    if not rows:
        return
    if dialect_of(session) == "postgresql":
        await session.execute(
            PG_UPSERT_BOOK,
            [
                {
                    "source": i.source,
                    "external_id": i.external_id,
                    "title": i.title[:500],
                    "content_hash": i.digest,
                    "vec": pg_literal(v),
                }
                for i, v in rows
            ],
        )
    else:
        for i, v in rows:
            stmt = sqlite_dialect.insert(BookVector).values(
                source=i.source, external_id=i.external_id, title=i.title[:500], content_hash=i.digest, vec=v
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "external_id"],
                set_={
                    "title": stmt.excluded.title,
                    "content_hash": stmt.excluded.content_hash,
                    "vec": stmt.excluded.vec,
                    "updated_at": func.now(),
                },
            )
            await session.execute(stmt)
    await session.commit()


# --- 책 벡터: 찾기 ------------------------------------------------------------------------

PG_KNN_BOOKS = text(
    "SELECT source, external_id, title, 1 - (vec <=> CAST(:q AS vector)) AS score, vec::text AS vec "
    "FROM book_vectors ORDER BY vec <=> CAST(:q AS vector) LIMIT :k"
)


def ef_search_sql(k: int) -> str:
    """SET LOCAL 은 바인드 인자를 못 받는다 — 정수로 만든 뒤 넣는다(이 트랜잭션에서만)."""
    return f"SET LOCAL hnsw.ef_search = {max(40, min(int(k) * 2, 1000))}"


async def nearest_books(session: AsyncSession, query: list[float], k: int) -> list[Hit]:
    """query 에 가장 가까운 책 k 권(가까운 순). 걸러 낼 책이 있으면 부르는 쪽이 k 를 넉넉히 준다."""
    if not query or k <= 0:
        return []
    if dialect_of(session) == "postgresql":
        # HNSW 는 ef_search(기본 40)개까지만 후보를 돌려준다 — 서재 책을 걸러 낼 여유까지 넉넉히
        await session.execute(text(ef_search_sql(k)))
        rows = await session.execute(PG_KNN_BOOKS, {"q": pg_literal(query), "k": k})
        return [Hit(s, e, t, float(score), parse_pg(v)) for s, e, t, score, v in rows]
    rows = await session.execute(
        select(BookVector.source, BookVector.external_id, BookVector.title, BookVector.vec)
    )
    hits = [Hit(s, e, t, _dot(query, v), v) for s, e, t, v in rows if v]
    hits.sort(key=lambda h: -h.score)
    return hits[:k]


async def book_vectors_for(
    session: AsyncSession, keys: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], list[float]]:
    keys = list(dict.fromkeys(keys))
    if not keys:
        return {}
    out: dict[tuple[str, str], list[float]] = {}
    pg = dialect_of(session) == "postgresql"
    for start in range(0, len(keys), 200):
        part = keys[start : start + 200]
        if pg:
            stmt = text(
                "SELECT source, external_id, vec::text FROM book_vectors WHERE "
                + " OR ".join(f"(source = :s{n} AND external_id = :e{n})" for n in range(len(part)))
            )
            params = {f"s{n}": s for n, (s, _) in enumerate(part)} | {
                f"e{n}": e for n, (_, e) in enumerate(part)
            }
            rows = await session.execute(stmt, params)
            out |= {(s, e): parse_pg(v) for s, e, v in rows}
        else:
            from sqlalchemy import and_, or_

            cond = or_(*(and_(BookVector.source == s, BookVector.external_id == e) for s, e in part))
            rows = await session.execute(
                select(BookVector.source, BookVector.external_id, BookVector.vec).where(cond)
            )
            out |= {(s, e): v for s, e, v in rows if v}
    return out


async def count_books(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(BookVector)) or 0)


def _dot(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return -1.0  # 차원이 바뀐 옛 벡터 — 맞지 않으니 맨 뒤로
    return ai.cosine(a, b)


# --- 본문 조각 ---------------------------------------------------------------------------

_SENT = re.compile(r"(?<=[.!?。？！…\"”’])\s+")


def split_parts(chunk_text: str, size: int = PART_CHARS) -> list[str]:
    """한 쪽을 size 글자 안팎의 조각으로 — 문단을 먼저, 긴 문단은 문장에서 자른다.

    입력이 같으면 결과도 같다(조각 글은 저장하지 않고 필요할 때 다시 자른다).
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", chunk_text.replace("\r\n", "\n")) if p.strip()]
    pieces: list[str] = []
    for p in paras:
        if len(p) <= size * 1.5:
            pieces.append(p)
            continue
        cur = ""
        for s in _SENT.split(p):
            while len(s) > size * 2:  # 문장 부호가 없는 아주 긴 글
                if cur:
                    pieces.append(cur)
                    cur = ""
                pieces.append(s[:size])
                s = s[size:]
            if cur and len(cur) + 1 + len(s) > size:
                pieces.append(cur)
                cur = s
            else:
                cur = f"{cur} {s}" if cur else s
        if cur:
            pieces.append(cur)
    out: list[str] = []
    for p in pieces:  # 짧은 조각은 앞 조각에 붙인다
        if out and len(out[-1]) + len(p) + 2 <= size:
            out[-1] = f"{out[-1]}\n\n{p}"
        else:
            out.append(p)
    return out or ([chunk_text.strip()] if chunk_text.strip() else [])


@dataclass
class ChunkBuild:
    added: int  # 이번에 만든 조각 수
    pending: bool  # 읽은 쪽 안에 아직 못 만든 쪽이 남았나


async def ensure_chunks(
    session: AsyncSession, book: Book, upto: int, budget: int = CHUNK_BUDGET
) -> ChunkBuild:
    """book 의 0..upto 쪽 가운데 벡터가 없는 쪽을 앞에서부터 budget 조각까지 만든다.

    한 쪽의 조각은 늘 한꺼번에 넣는다(반쯤 만든 쪽이 없다). 부르는 쪽이 이 사람이 이 책을 읽어도
    되는지(can_read)와 upto(검증받은 쪽)를 이미 확인했다. AI 가 안 되면 ai.AIUnavailable.
    """
    if upto < 0:
        return ChunkBuild(0, False)
    done = set(
        await session.scalars(
            select(ChunkVector.chunk_index)
            .where(ChunkVector.book_id == book.id, ChunkVector.chunk_index <= upto)
            .distinct()
        )
    )
    missing = [i for i in range(upto + 1) if i not in done]
    if not missing:
        return ChunkBuild(0, False)
    window = missing[:budget]  # 한 쪽은 조각이 한 개 이상이라 budget 쪽이면 충분하다
    texts = dict(
        (
            await session.execute(
                select(BookChunk.chunk_index, BookChunk.text).where(
                    BookChunk.book_id == book.id, BookChunk.chunk_index.in_(window)
                )
            )
        ).all()
    )
    todo: list[tuple[int, int, str]] = []
    seen = 0
    for idx in window:
        parts = split_parts(texts.get(idx, ""))
        if todo and len(todo) + len(parts) > budget:
            break
        seen += 1
        todo.extend((idx, n, p) for n, p in enumerate(parts[:budget]))
    pending = seen < len(missing)
    if not todo:
        return ChunkBuild(0, pending)
    vecs = await ai.embed([t for _, _, t in todo])
    if dialect_of(session) == "postgresql":
        await session.execute(
            text(
                "INSERT INTO chunk_vectors (book_id, chunk_index, part, vec) "
                "VALUES (:b, :c, :p, CAST(:v AS vector)) ON CONFLICT DO NOTHING"
            ),
            [
                {"b": book.id, "c": c, "p": n, "v": pg_literal(v)}
                for (c, n, _), v in zip(todo, vecs, strict=True)
            ],
        )
    else:
        for (c, n, _), v in zip(todo, vecs, strict=True):
            await session.execute(
                sqlite_dialect.insert(ChunkVector)
                .values(book_id=book.id, chunk_index=c, part=n, vec=pack(v))
                .on_conflict_do_nothing()
            )
    await session.commit()
    return ChunkBuild(len(todo), pending)


@dataclass
class Piece:
    chunk_index: int
    part: int
    score: float
    text: str = ""

    @property
    def page(self) -> int:
        return self.chunk_index + 1


PG_KNN_CHUNKS = text(
    "SELECT chunk_index, part, 1 - (vec <=> CAST(:q AS vector)) AS score FROM chunk_vectors "
    "WHERE book_id = :book AND chunk_index <= :upto "
    "ORDER BY vec <=> CAST(:q AS vector) LIMIT :k"
)


async def nearest_pieces(
    session: AsyncSession, book_id: int, upto: int, query: list[float], k: int = 6
) -> list[Piece]:
    """이 책에서 upto 쪽(0부터)까지의 조각 가운데 query 와 가까운 k 개. 뒤쪽은 SQL 에서부터 걸러 낸다."""
    if upto < 0 or not query:
        return []
    if dialect_of(session) == "postgresql":
        rows = await session.execute(
            PG_KNN_CHUNKS, {"q": pg_literal(query), "book": book_id, "upto": upto, "k": k}
        )
        hits = [Piece(int(c), int(p), float(s)) for c, p, s in rows]
    else:
        rows = await session.execute(
            select(ChunkVector.chunk_index, ChunkVector.part, ChunkVector.vec).where(
                ChunkVector.book_id == book_id, ChunkVector.chunk_index <= upto
            )
        )
        hits = [Piece(c, p, _dot(query, v)) for c, p, v in rows if v]
        hits.sort(key=lambda h: (-h.score, h.chunk_index, h.part))
        hits = hits[:k]
    hits = [h for h in hits if h.chunk_index <= upto]  # 두 번 확인 — 뒤쪽 조각은 어떤 길로도 나가지 않는다
    if not hits:
        return []
    texts = dict(
        (
            await session.execute(
                select(BookChunk.chunk_index, BookChunk.text).where(
                    BookChunk.book_id == book_id,
                    BookChunk.chunk_index.in_({h.chunk_index for h in hits}),
                    BookChunk.chunk_index <= upto,
                )
            )
        ).all()
    )
    out = []
    for h in hits:
        parts = split_parts(texts.get(h.chunk_index, ""))
        if h.part < len(parts):
            h.text = parts[h.part]
            out.append(h)
    return out


async def chunk_stats(session: AsyncSession) -> tuple[int, int]:
    """(조각 벡터 수, 조각 벡터가 있는 책 수)."""
    n = await session.scalar(select(func.count()).select_from(ChunkVector)) or 0
    books = await session.scalar(select(func.count(func.distinct(ChunkVector.book_id)))) or 0
    return int(n), int(books)


async def drop_chunks(session: AsyncSession, book_id: int) -> None:
    await session.execute(delete(ChunkVector).where(ChunkVector.book_id == book_id))
    await session.commit()
