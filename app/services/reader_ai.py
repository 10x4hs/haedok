"""읽는 중의 AI — 문단에서 바로 묻기 · AI 노트 · 지난 이야기 · 등장인물 · 생각해 볼 질문.

스포일러 규칙(모든 기능이 같다)
- 문단에서 묻기(explain · word · summary): 다 읽은 쪽(verified)과, 지금 펼친 다음 쪽(verified + 1)의
  '고른 문단까지'만 보낸다. 반응을 남길 수 있는 범위(social.add_reaction)와 같다. 고른 문단 뒤의 문단,
  그 뒤의 쪽은 어떤 길로도 보내지 않는다. 고른 글(selection)은 서버가 그 문단 안에 있는지 확인한다 —
  브라우저가 보낸 글을 그대로 믿지 않고, 본문은 늘 DB 에서 다시 읽는다.
- 지난 이야기 · 등장인물 · 생각해 볼 질문: 다 읽은 쪽(0..verified)만.
비용: AI 를 부를 때마다 하루 상한(rag.spend_daily, 'ai-day:{user}')을 하나 쓴다. 캐시에서 주면 쓰지 않는다.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Book, BookChunk, Reaction, User
from app.models_ai import AIBookCache, AINote
from app.services import ai, rag, social

ACTIONS = {"explain": "이 부분 쉽게 설명", "word": "이 단어 뜻", "summary": "여기까지 요약"}
MAX_WORD = 30
MAX_SELECTION = 300
DRAFTS_KEPT = 10  # 저장하지 않은 답은 사람마다 최근 10개만 남긴다
SUMMARY_PAGES = 3  # '여기까지 요약'에 쓰는 쪽 수(고른 쪽 포함)
SUMMARY_CHARS = 3600
RECAP_PAGES, RECAP_PIECE = 10, 420  # 지난 이야기: 많아야 10쪽에서 쪽마다 앞부분 420자
REFLECT_EVERY = 10  # 10쪽마다 생각해 볼 질문 하나
AWAY = dt.timedelta(days=1)

# --- 시스템 프롬프트(기능마다 하나) ---------------------------------------------------------

QUICK_SYSTEM = """당신은 책을 읽는 독자 곁의 독서 도우미예요.
규칙:
- 사용자가 준 [N쪽] 글만 근거로 답해요. 독자는 거기까지만 읽었어요.
- 이 책의 뒤 내용(사건, 결말, 반전, 인물의 운명)을 알고 있더라도 절대 말하거나 암시하지 않아요.
- 근거가 된 쪽을 문장 끝에 (12쪽)처럼 붙여요. 주어진 쪽 번호만 써요.
- 쉬운 해요체로 짧게, 목록이나 머리말 없이 문장으로만 써요."""

ACTION_ASK = {
    "explain": "위 글에서 «{target}» 부분을 처음 읽는 사람도 알 수 있게 2~3문장으로 쉽게 풀어 주세요.",
    "word": "위 글에 나온 «{target}»의 뜻을 이 글의 맥락에 맞게 1~2문장으로 알려 주세요.",
    "summary": "독자가 지금까지 읽은 위 글을 3~4문장으로 요약해 주세요. 마지막 [쪽] 글의 끝에서 멈춰요.",
}

RECAP_SYSTEM = """당신은 독자가 오랜만에 책을 다시 펼칠 때 '지난 이야기'를 들려주는 도우미예요.
규칙:
- 사용자가 준 글(이전 요약과 [N쪽] 발췌)만 근거로 써요. 독자는 거기까지만 읽었어요.
- 이 책의 뒤 내용은 알고 있더라도 절대 말하거나 암시하지 않아요.
- 쉬운 해요체로 딱 3줄, 한 줄에 한 문장. 쪽 번호와 머리말은 쓰지 않아요."""

CHARS_SYSTEM = """당신은 독자가 읽은 부분에 나온 등장인물을 정리해 주는 도우미예요.
규칙:
- 사용자가 준 글(이전 목록과 [N쪽] 발췌)에 실제로 나온 인물만 써요. 글에 없는 이름은 쓰지 않아요.
- 인물의 앞날이나 뒤 내용은 알고 있더라도 절대 말하지 않아요.
- JSON 으로만 답해요: {"characters": [{"name": "이름", "line": "읽은 부분에서 어떤 사람인지 해요체 한 문장"}]}
- 많아야 8명, 중요한 순서로."""

REFLECT_SYSTEM = """당신은 독자가 방금 읽은 부분을 되새기도록 질문 하나를 건네는 도우미예요.
규칙:
- 사용자가 준 [N쪽] 발췌만 근거로 해요. 뒤 내용은 알고 있더라도 말하거나 암시하지 않아요.
- 정답이 없는, 독자 자신의 생각을 묻는 질문 딱 하나를 해요체 한 문장(60자 안팎)으로만 써요."""

NO_WORD = "뜻을 알고 싶은 낱말을 길게 눌러 골라 주세요."
NOT_YET = "아직 읽지 않은 쪽이에요."
NOTHING_READ = "아직 다 읽은 쪽이 없어요. 몇 쪽 읽고 나서 다시 눌러 주세요."


class ReaderAIError(ValueError):
    """독자에게 그대로 보여 줄 수 있는 거절(422)."""


def on() -> bool:
    s = get_settings()
    return s.feature_ai_reader and s.feature_ai_rag and ai.enabled()


def paragraphs(text: str) -> list[str]:
    """리더(reader.js)와 같은 규칙으로 문단을 나눈다: 빈 줄(/\\n\\s*\\n/)."""
    return re.split(r"\n\s*\n", text or "")


@dataclass
class Result:
    kind: str  # ok | capped | down
    text: str
    pages: list[int] = field(default_factory=list)
    note_id: int | None = None
    cached: bool = False
    items: list[dict] = field(default_factory=list)


async def _spend(session: AsyncSession, user_id: int) -> bool:
    return await rag.spend_daily(session, user_id)


async def _texts(session: AsyncSession, book_id: int, indexes: list[int]) -> dict[int, str]:
    if not indexes:
        return {}
    rows = await session.execute(
        select(BookChunk.chunk_index, BookChunk.text).where(
            BookChunk.book_id == book_id, BookChunk.chunk_index.in_(indexes)
        )
    )
    return dict(rows.all())


# --- 1. 문단에서 묻기 ------------------------------------------------------------------------


@dataclass
class Scope:
    """이 질문에 보낼 수 있는 글: (쪽 번호(1부터), 글) 목록 — 고른 문단에서 끝난다."""

    parts: list[tuple[int, str]]
    para: str  # 고른 문단
    target: str  # 고른 글(없으면 문단 첫머리)


async def scope_for(
    session: AsyncSession, user: User, book: Book, chunk: int, anchor: int, action: str, selection: str
) -> Scope:
    verified = await social.verified_of(session, user.id, book.id)
    if not 0 <= chunk < book.total_chunks or not 0 <= anchor <= social.MAX_ANCHOR:
        raise ReaderAIError("없는 쪽이에요")
    if chunk > verified + 1:  # 반응과 같은 규칙: 지금 펼친 쪽까지
        raise ReaderAIError(NOT_YET)
    first = max(0, chunk - (SUMMARY_PAGES - 1)) if action == "summary" else chunk
    texts = await _texts(session, book.id, list(range(first, chunk + 1)))
    paras = paragraphs(texts.get(chunk, ""))
    if anchor >= len(paras):
        raise ReaderAIError("없는 문단이에요")
    para = paras[anchor].strip()
    sel = " ".join((selection or "").split())[:MAX_SELECTION]
    if sel and sel not in " ".join(para.split()):
        sel = ""  # 이 문단 밖의 글은 받지 않는다(다른 쪽 글을 끼워 넣지 못하게)
    if action == "word":
        if not sel or len(sel) > MAX_WORD:
            raise ReaderAIError(NO_WORD)
    here = "\n\n".join(p.strip() for p in paras[: anchor + 1] if p.strip())  # 고른 문단까지
    if action == "summary":
        parts = [(i + 1, texts.get(i, "").strip()) for i in range(first, chunk)] + [(chunk + 1, here)]
        parts = _tail(parts, SUMMARY_CHARS)
    elif action == "explain":
        prev = paras[anchor - 1].strip() if anchor > 0 else ""
        parts = [(chunk + 1, f"{prev}\n\n{para}".strip())]
    else:
        parts = [(chunk + 1, para)]
    target = sel or (para[:80] + ("…" if len(para) > 80 else ""))
    return Scope(parts, para, target)


def _tail(parts: list[tuple[int, str]], budget: int) -> list[tuple[int, str]]:
    """뒤(고른 곳)에서부터 budget 글자까지 — 오래된 쪽의 앞부분부터 버린다."""
    out: list[tuple[int, str]] = []
    left = budget
    for page, text in reversed(parts):
        if left <= 0:
            break
        if not text:
            continue
        piece = text if len(text) <= left else "…" + text[-left:]
        out.append((page, piece))
        left -= len(piece)
    return list(reversed(out))


def quick_prompt(action: str, sc: Scope) -> str:
    body = "\n\n".join(f"[{page}쪽] {text}" for page, text in sc.parts)
    return f"{body}\n\n{ACTION_ASK[action].format(target=sc.target)}"


async def quick(
    session: AsyncSession, user: User, book: Book, chunk: int, anchor: int, action: str, selection: str = ""
) -> Result:
    """문단에서 바로 묻기. 답은 저장 전 초안(AINote saved=False)으로 남겨 note_id 를 준다."""
    if action not in ACTIONS:
        raise ReaderAIError("모르는 동작이에요")
    sc = await scope_for(session, user, book, chunk, anchor, action, selection)
    if not await _spend(session, user.id):
        return Result("capped", rag.CAPPED)
    try:
        res = await ai.chat(
            [{"role": "user", "content": quick_prompt(action, sc)}], system=QUICK_SYSTEM, max_tokens=320
        )
    except ai.AIUnavailable:
        await session.rollback()
        return Result("down", rag.DOWN)
    allowed = {page for page, _ in sc.parts}
    text, cited = rag.clean_cites(res.text, allowed)
    if not text:
        return Result("down", rag.DOWN)
    pages = cited or [chunk + 1]
    quote = sc.target if action == "word" or selection else sc.para[:MAX_SELECTION]
    note = AINote(
        user_id=user.id, book_id=book.id, chunk_index=chunk, anchor=anchor, kind=action,
        quote=quote[:MAX_SELECTION], answer=text, pages=pages, saved=False,
    )  # fmt: skip
    session.add(note)
    await session.flush()
    stale = (
        await session.scalars(
            select(AINote.id)
            .where(AINote.user_id == user.id, AINote.saved.is_(False))
            .order_by(AINote.id.desc())
            .offset(DRAFTS_KEPT)
        )
    ).all()
    if stale:
        await session.execute(delete(AINote).where(AINote.id.in_(stale)))
    await session.commit()
    return Result("ok", text, pages, note_id=note.id)


# --- 2. AI 노트 ---------------------------------------------------------------------------


def note_out(n: AINote) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "label": ACTIONS.get(n.kind, "AI 노트"),
        "chunk": n.chunk_index,
        "anchor": n.anchor,
        "quote": n.quote,
        "answer": n.answer,
        "pages": list(n.pages or []),
        "saved": n.saved,
        "shared": n.reaction_id is not None,
    }


async def my_note(session: AsyncSession, user: User, book: Book, note_id: int) -> AINote | None:
    """내 노트만 — 남의 노트는 없는 것과 같다(같은 404)."""
    n = await session.get(AINote, note_id)
    if n is None or n.user_id != user.id or n.book_id != book.id:
        return None
    return n


async def notes(session: AsyncSession, user: User, book: Book, chunk: int | None = None) -> list[AINote]:
    q = select(AINote).where(AINote.user_id == user.id, AINote.book_id == book.id, AINote.saved.is_(True))
    if chunk is not None:
        q = q.where(AINote.chunk_index == chunk)
    return list(await session.scalars(q.order_by(AINote.chunk_index, AINote.anchor, AINote.id)))


async def save(session: AsyncSession, n: AINote) -> AINote:
    n.saved = True
    await session.commit()
    return n


async def remove(session: AsyncSession, n: AINote) -> None:
    """노트만 지운다. 이미 공유한 반응은 보통 반응처럼 리더에서 따로 지운다."""
    await session.delete(n)
    await session.commit()


def share_line(answer: str) -> str:
    """반응 한 줄(≤140자): 쪽 표시를 뗀 첫 문장."""
    first = re.split(r"(?<=[.!?。])\s", answer.strip(), maxsplit=1)[0]
    first = re.sub(r"\s*\(\d+\s*쪽[^()]*\)", "", first).strip()
    line = f"✦ {first}"
    return line if len(line) <= social.MAX_COMMENT else line[: social.MAX_COMMENT - 1] + "…"


async def share(session: AsyncSession, user: User, book: Book, n: AINote) -> Reaction:
    """노트의 한 줄을 보통 반응으로 남긴다 — 그때부터 친구에게는 반응의 스포일러 가드가 걸린다."""
    if n.reaction_id is not None:
        existing = await session.get(Reaction, n.reaction_id)
        if existing is not None:
            return existing
    r = await social.add_reaction(session, user, book, n.chunk_index, n.anchor, None, share_line(n.answer))
    n.saved = True
    n.reaction_id = r.id
    await session.commit()
    return r


# --- 3. 지난 이야기 · 등장인물 · 생각해 볼 질문 ------------------------------------------------


def away(updated_at: dt.datetime | None, now: dt.datetime | None = None) -> bool:
    """마지막으로 읽은 지 하루가 지났나 — 리더를 그릴 때(이번 방문의 저장 전) 판단한다."""
    if updated_at is None:
        return False
    from app.services.books import clock

    t = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=dt.UTC)
    return (now or clock()) - t >= AWAY


def sample(start: int, end: int, n: int) -> list[int]:
    """start..end 에서 고르게 n 쪽(끝 쪽은 늘 포함)."""
    if end < start:
        return []
    span = end - start + 1
    if span <= n:
        return list(range(start, end + 1))
    step = (span - 1) / (n - 1)
    return sorted({start + round(i * step) for i in range(n)})


async def _excerpts(session: AsyncSession, book: Book, start: int, end: int, pages: int, each: int) -> str:
    idx = sample(start, end, pages)
    texts = await _texts(session, book.id, idx)
    out = []
    for i in idx:
        t = " ".join((texts.get(i) or "").split())
        if t:
            out.append(f"[{i + 1}쪽] {t[:each]}{'…' if len(t) > each else ''}")
    return "\n\n".join(out)


async def _cache(session: AsyncSession, user: User, book: Book, kind: str) -> AIBookCache | None:
    return await session.get(AIBookCache, (user.id, book.id, kind))


async def _store(session: AsyncSession, row: AIBookCache | None, user: User, book: Book, kind: str,
                 verified: int, body: str) -> None:  # fmt: skip
    if row is None:
        row = AIBookCache(user_id=user.id, book_id=book.id, kind=kind)
        session.add(row)
    row.verified_chunk = verified
    row.body = body
    row.updated_at = dt.datetime.now(dt.UTC)
    await session.commit()


async def _verified(session: AsyncSession, user: User, book: Book) -> int:
    v = await social.verified_of(session, user.id, book.id)
    return min(v, book.total_chunks - 1)


def recap_prompt(prev: str | None, prev_upto: int, excerpts: str, verified: int) -> str:
    head = (
        f"이전 요약(1~{prev_upto + 1}쪽):\n{prev}\n\n새로 읽은 부분 발췌:\n" if prev else "읽은 부분 발췌:\n"
    )
    return f"{head}{excerpts}\n\n(독자는 1~{verified + 1}쪽까지 읽었어요.) 지난 이야기를 3줄로 들려주세요."


async def recap(session: AsyncSession, user: User, book: Book) -> Result:
    """지난 이야기 3줄. 같은 쪽까지면 캐시, 더 읽었으면 이전 요약 + 새로 읽은 쪽 발췌로 한 번 부른다."""
    verified = await _verified(session, user, book)
    if verified < 0:
        raise ReaderAIError(NOTHING_READ)
    row = await _cache(session, user, book, "recap")
    if row is not None and row.verified_chunk == verified:
        return Result("ok", row.body, cached=True)
    prev = row.body if row is not None and row.verified_chunk < verified else None
    start = row.verified_chunk + 1 if prev else 0
    excerpts = await _excerpts(session, book, start, verified, RECAP_PAGES, RECAP_PIECE)
    if not await _spend(session, user.id):
        return Result("capped", rag.CAPPED)
    try:
        res = await ai.chat(
            [{"role": "user", "content": recap_prompt(prev, start - 1, excerpts, verified)}],
            system=RECAP_SYSTEM, max_tokens=260,
        )  # fmt: skip
    except ai.AIUnavailable:
        await session.rollback()
        return Result("down", rag.DOWN)
    text, _ = rag.clean_cites(res.text, set(range(1, verified + 2)))
    lines = [ln.strip(" -•·") for ln in text.splitlines() if ln.strip(" -•·")][:3]
    body = "\n".join(lines) or text[:300]
    if not body:
        return Result("down", rag.DOWN)
    await _store(session, row, user, book, "recap", verified, body)
    return Result("ok", body)


def parse_characters(text: str, seen_in: str) -> list[dict]:
    """모델 답(JSON 또는 '이름: 설명' 줄)에서 인물을 꺼낸다.

    보낸 글에 없는 이름은 버린다(지어낸 인물 금지)."""
    items: list[dict] = []
    try:
        data = json.loads(text[text.index("{") : text.rindex("}") + 1])
        raw = data.get("characters") or []
        items = [
            {"name": str(c.get("name", "")), "line": str(c.get("line", ""))}
            for c in raw
            if isinstance(c, dict)
        ]
    except (ValueError, AttributeError):
        for ln in text.splitlines():
            m = re.match(r"\s*[-•·*]?\s*([^:—\-]{1,20})\s*[:—\-]\s*(.+)", ln)
            if m:
                items.append({"name": m.group(1), "line": m.group(2)})
    out, names = [], set()
    for c in items:
        name, line = c["name"].strip(" «»\"'"), " ".join(c["line"].split())
        if not name or not line or name in names or name not in seen_in:
            continue
        names.add(name)
        out.append({"name": name[:20], "line": line[:120]})
    return out[:8]


async def characters(session: AsyncSession, user: User, book: Book) -> Result:
    """등장인물(읽은 쪽까지). recap 과 같은 방식으로 캐시 · 갱신한다."""
    verified = await _verified(session, user, book)
    if verified < 0:
        raise ReaderAIError(NOTHING_READ)
    row = await _cache(session, user, book, "chars")
    if row is not None and row.verified_chunk == verified:
        return Result("ok", "", cached=True, items=json.loads(row.body))
    prev = json.loads(row.body) if row is not None and row.verified_chunk < verified else []
    start = row.verified_chunk + 1 if row is not None and row.verified_chunk < verified else 0
    excerpts = await _excerpts(session, book, start, verified, RECAP_PAGES, RECAP_PIECE)
    prev_txt = "\n".join(f"- {c['name']}: {c['line']}" for c in prev)
    prompt = (
        (f"이전 목록:\n{prev_txt}\n\n" if prev else "")
        + f"읽은 부분 발췌:\n{excerpts}\n\n"
        + (f"(독자는 1~{verified + 1}쪽까지 읽었어요.) 지금까지 나온 등장인물을 정리해 주세요.")
    )
    if not await _spend(session, user.id):
        return Result("capped", rag.CAPPED)
    try:
        res = await ai.chat(
            [{"role": "user", "content": prompt}], system=CHARS_SYSTEM, max_tokens=500, json_mode=True
        )
    except ai.AIUnavailable:
        await session.rollback()
        return Result("down", rag.DOWN)
    items = parse_characters(res.text, prompt)
    by = {c["name"]: c for c in prev} | {c["name"]: c for c in items}  # 새 설명이 옛 설명을 덮는다
    merged = list(by.values())[:8]
    await _store(session, row, user, book, "chars", verified, json.dumps(merged, ensure_ascii=False))
    return Result("ok", "", items=merged)


def milestone(verified: int) -> int | None:
    """생각해 볼 질문을 줄 수 있는 가장 최근 쪽(0부터): 10 · 20 · 30쪽…을 다 읽었을 때."""
    done = verified + 1
    return done // REFLECT_EVERY * REFLECT_EVERY - 1 if done >= REFLECT_EVERY else None


async def reflect(session: AsyncSession, user: User, book: Book) -> Result:
    """방금 읽은 10쪽에서 생각해 볼 질문 하나. 같은 구간이면 캐시."""
    verified = await _verified(session, user, book)
    m = milestone(verified)
    if m is None:
        raise ReaderAIError(f"{REFLECT_EVERY}쪽을 다 읽으면 질문을 드려요.")
    row = await _cache(session, user, book, "reflect")
    if row is not None and row.verified_chunk == m:
        return Result("ok", row.body, [m + 1], cached=True)
    excerpts = await _excerpts(session, book, m - REFLECT_EVERY + 1, m, 5, 360)
    if not await _spend(session, user.id):
        return Result("capped", rag.CAPPED)
    ask = f"{excerpts}\n\n(독자는 1~{m + 1}쪽까지 읽었어요.) 질문 하나를 건네 주세요."
    try:
        res = await ai.chat([{"role": "user", "content": ask}], system=REFLECT_SYSTEM, max_tokens=120)
    except ai.AIUnavailable:
        await session.rollback()
        return Result("down", rag.DOWN)
    text, _ = rag.clean_cites(res.text, set(range(m - REFLECT_EVERY + 2, m + 2)))
    q = " ".join(text.split())[:120]
    if not q:
        return Result("down", rag.DOWN)
    await _store(session, row, user, book, "reflect", m, q)
    return Result("ok", q, [m + 1])
