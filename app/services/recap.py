"""이번 달 독서(/me/recap) · 게시물 초안 만들기.

숫자는 AI 없이 DB 에서 센다(규칙은 잔디와 같다: 한국 시간 새벽 4시에 날짜가 바뀌고, 체류시간 검증을 통과한
쪽만, 같은 (책, 쪽)은 한 번만). AI 문단은 '내' 기록(내가 남긴 문장 · 반응 · 게시물 글)만 보내 만든다 —
다른 사람의 글은 한 글자도 보내지 않는다. 인용은 한 문장(최대 80자)까지만 자른다.

AI 문단은 users.settings["ai_recap"][YYYY-MM] 에 저장해 두고,
새로 만들기는 하루 한 번까지다(마이그레이션 없음).
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Book,
    Highlight,
    HighlightVote,
    LibraryEntry,
    RaceQuote,
    Reaction,
    ReactionKind,
    ReadingEvent,
    Room,
    Shelf,
    User,
)
from app.models_posts import ReadingPost
from app.services import ai, goals
from app.services.shop_ai import spend

RECAP_SYSTEM = """당신은 독서 앱 '해독'에서 한 독자의 한 달 독서를 돌아봐 주는 친구예요.
아래 기록은 모두 이 독자가 직접 남긴 것이에요.
기록에 나타난 주제 · 감정 · 관심사를 3~4문장 한 문단으로 묶어 주세요.
규칙: 쉬운 해요체, 독자를 '당신'이라 부르지 않기, 기록에 없는 책이나 사실을 지어내지 않기,
인용은 한 문장 이하로 한 번만, 숫자는 말하지 않기(화면에 따로 있어요), 350자 이내. 문단만 답해요."""

DRAFT_SYSTEM = """당신은 독서 앱 '해독'에서 게시물 초안을 같이 써 주는 친구예요.
아래는 한 독자가 다 읽은 책과, 그 책에서 직접 남긴 문장 · 반응이에요.
JSON 하나로만 답해요:
{"caption": "독자가 1인칭으로 쓴 듯한 감상 2~4문장, 쉬운 해요체, 280자 이내, 줄거리 결말 금지",
"quote_id": "마음에 남은 문장으로 고른 q 번호(예: q2), 고를 문장이 없으면 null"}
기록에 없는 사실은 지어내지 않아요."""

MAX_PARAGRAPH = 600
MAX_CAPTION = 300
QUOTE_CHARS = 80
SEND = {"highlights": 12, "reactions": 12, "captions": 6}
KEEP_MONTHS = 3  # settings 에 남겨 두는 달 수
DAY_PROMPT_MSG = "오늘은 이미 새로 만들었어요. 내일 다시 만들 수 있어요."


def clock() -> dt.datetime:
    return goals.clock()


# --------------------------------------------------------------------------- 달


@dataclass(frozen=True)
class Month:
    year: int
    month: int

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        return f"{self.year}년 {self.month}월"

    @property
    def first(self) -> dt.date:
        return dt.date(self.year, self.month, 1)

    def shift(self, n: int) -> Month:
        i = self.year * 12 + self.month - 1 + n
        return Month(i // 12, i % 12 + 1)

    @property
    def start(self) -> dt.datetime:
        return goals.day_start(self.first)

    @property
    def end(self) -> dt.datetime:
        return goals.day_start(self.shift(1).first)


def this_month() -> Month:
    today = goals.reading_day(clock())
    return Month(today.year, today.month)


def parse_month(value: str | None) -> Month:
    """'YYYY-MM' — 잘못된 값이나 미래 달은 이번 달."""
    now = this_month()
    m = re.fullmatch(r"(\d{4})-(\d{2})", (value or "").strip())
    if not m:
        return now
    y, mo = int(m.group(1)), int(m.group(2))
    if not (2000 <= y <= 2100 and 1 <= mo <= 12):
        return now
    month = Month(y, mo)
    return now if (y, mo) > (now.year, now.month) else month


# --------------------------------------------------------------------------- 숫자(AI 없이)


def one_sentence(text: str, n: int = QUOTE_CHARS) -> str:
    """인용은 한 문장까지 — 첫 문장 끝(. ! ? 。 …)에서 자르고, 그래도 길면 n 자에서 자른다."""
    t = " ".join((text or "").split())
    m = re.search(r"[.!?。…](?=\s|$)", t)
    if m:
        t = t[: m.end()]
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


@dataclass
class Line:
    """내가 남긴 문장 하나(하이라이트 · 대결방에 공유한 문장)."""

    quote: str
    book_id: int
    title: str
    votes: int = 0


@dataclass
class Recap:
    month: Month
    pages: int = 0
    days: int = 0
    finished: list[Book] = field(default_factory=list)
    posts: int = 0
    reactions: int = 0
    lines: list[Line] = field(default_factory=list)
    top_emojis: list[tuple[str, int]] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)  # 내 반응 글
    captions: list[str] = field(default_factory=list)  # 내 게시물 글
    titles: list[str] = field(default_factory=list)  # 이번 달 읽은 책

    @property
    def empty(self) -> bool:
        return not (self.pages or self.posts or self.reactions or self.lines)

    @property
    def top_lines(self) -> list[Line]:
        return sorted(self.lines, key=lambda x: -x.votes)[:3]


async def numbers(session: AsyncSession, user_id: int, month: Month) -> Recap:
    out = Recap(month)
    lo, hi = month.start, month.end

    rows = await session.execute(
        select(ReadingEvent.book_id, ReadingEvent.chunk_index, ReadingEvent.occurred_at)
        .where(
            ReadingEvent.user_id == user_id,
            ReadingEvent.verified.is_(True),
            ReadingEvent.occurred_at >= lo,
            ReadingEvent.occurred_at < hi,
        )
        .order_by(ReadingEvent.occurred_at, ReadingEvent.id)
    )
    seen: set[tuple[int, int]] = set()
    days: set[dt.date] = set()
    book_ids: list[int] = []
    last_chunk: dict[int, int] = {}
    for book_id, chunk, at in rows.all():
        if (book_id, chunk) in seen:
            continue
        seen.add((book_id, chunk))
        days.add(goals.reading_day(at))
        if book_id not in book_ids:
            book_ids.append(book_id)
        last_chunk[book_id] = max(last_chunk.get(book_id, -1), chunk)
    out.pages, out.days = len(seen), len(days)
    books = (
        {b.id: b for b in await session.scalars(select(Book).where(Book.id.in_(book_ids)))}
        if book_ids
        else {}
    )
    # 다 읽은 책 = 이번 달에 마지막 쪽이 검증된 책
    out.finished = [
        books[i] for i in book_ids if i in books and last_chunk[i] >= books[i].total_chunks - 1 > -1
    ]
    out.titles = [books[i].title for i in book_ids if i in books][:10]

    posts = list(
        await session.scalars(
            select(ReadingPost)
            .where(
                ReadingPost.author_id == user_id,
                ReadingPost.deleted_at.is_(None),
                ReadingPost.created_at >= lo,
                ReadingPost.created_at < hi,
            )
            .order_by(ReadingPost.id.desc())
        )
    )
    out.posts = len(posts)
    out.captions = [one_sentence(p.caption, 160) for p in posts if p.caption.strip()][: SEND["captions"]]

    reactions = list(
        await session.scalars(
            select(Reaction).where(
                Reaction.user_id == user_id, Reaction.created_at >= lo, Reaction.created_at < hi
            )
            .order_by(Reaction.id.desc())
        )
    )  # fmt: skip
    out.reactions = len(reactions)
    out.top_emojis = Counter(r.body for r in reactions if r.kind == ReactionKind.EMOJI).most_common(3)
    out.comments = [one_sentence(r.body, 120) for r in reactions if r.kind == ReactionKind.COMMENT][
        : SEND["reactions"]
    ]

    out.lines = await _my_lines(session, user_id, lo, hi)
    return out


async def _my_lines(
    session: AsyncSession,
    user_id: int,
    lo: dt.datetime | None,
    hi: dt.datetime | None,
    book_id: int | None = None,
) -> list[Line]:
    """내가 남긴 문장 — 하이라이트(표 수 포함)와 대결방에 공유한 문장. 다른 사람의 것은 읽지 않는다."""
    votes = (
        select(HighlightVote.highlight_id, func.count().label("n"))
        .group_by(HighlightVote.highlight_id)
        .subquery()
    )
    hq = (
        select(Highlight.quote, Highlight.book_id, Book.title, func.coalesce(votes.c.n, 0))
        .join(Book, Book.id == Highlight.book_id)
        .outerjoin(votes, votes.c.highlight_id == Highlight.id)
        .where(Highlight.user_id == user_id)
        .order_by(Highlight.id.desc())
    )
    rq = (
        select(RaceQuote.body, Room.book_id, Book.title)
        .join(Room, Room.id == RaceQuote.room_id)
        .join(Book, Book.id == Room.book_id)
        .where(RaceQuote.user_id == user_id)
        .order_by(RaceQuote.id.desc())
    )
    if lo is not None:
        hq = hq.where(Highlight.created_at >= lo, Highlight.created_at < hi)
        rq = rq.where(RaceQuote.created_at >= lo, RaceQuote.created_at < hi)
    if book_id is not None:
        hq = hq.where(Highlight.book_id == book_id)
        rq = rq.where(Room.book_id == book_id)
    out = [Line(one_sentence(q), b, t, int(n)) for q, b, t, n in (await session.execute(hq.limit(40))).all()]
    out += [Line(one_sentence(q), b, t) for q, b, t in (await session.execute(rq.limit(40))).all()]
    keep, seen = [], set()
    for line in out:
        if line.quote and line.quote not in seen:
            seen.add(line.quote)
            keep.append(line)
    return keep


# --------------------------------------------------------------------------- AI 문단


def recap_prompt(r: Recap) -> str:
    """AI 에게 보내는 글 — 내 기록만, 짧게."""
    parts = [f"{r.month.label} 기록"]
    if r.titles:
        parts.append("읽은 책: " + ", ".join(r.titles))
    if r.lines:
        parts.append(
            "남긴 문장:\n" + "\n".join(f"- 「{x.quote}」 ({x.title})" for x in r.lines[: SEND["highlights"]])
        )
    if r.comments:
        parts.append("반응:\n" + "\n".join(f"- {c}" for c in r.comments))
    if r.captions:
        parts.append("게시물 글:\n" + "\n".join(f"- {c}" for c in r.captions))
    if r.top_emojis:
        parts.append("자주 쓴 반응: " + " ".join(e for e, _ in r.top_emojis))
    return "\n\n".join(parts)


def cached(user: User, month: Month) -> dict | None:
    box = (user.settings or {}).get("ai_recap") or {}
    item = box.get(month.key) if isinstance(box, dict) else None
    return item if isinstance(item, dict) and isinstance(item.get("text"), str) else None


def made_today(user: User, month: Month) -> bool:
    item = cached(user, month)
    return bool(item) and item.get("day") == goals.reading_day(clock()).isoformat()


class TooSoon(Exception):
    pass


async def summarise(session: AsyncSession, client: httpx.AsyncClient | None, user: User, r: Recap) -> str:
    """AI 문단을 만들어 저장한다. 하루 한 번 · 하루 AI 한도 · AI 꺼짐은 예외로 알린다."""
    if made_today(user, r.month):
        raise TooSoon(DAY_PROMPT_MSG)
    await spend(session, user)
    res = await ai.chat(
        [{"role": "user", "content": recap_prompt(r)}], system=RECAP_SYSTEM, max_tokens=420, http=client
    )
    text = " ".join(res.text.split())[:MAX_PARAGRAPH]
    if not text:
        raise ai.AIUnavailable("빈 답")
    box = dict((user.settings or {}).get("ai_recap") or {})
    box[r.month.key] = {"text": text, "day": goals.reading_day(clock()).isoformat()}
    box = dict(sorted(box.items())[-KEEP_MONTHS:])
    user.settings = {**(user.settings or {}), "ai_recap": box}  # 새 dict 로 바꿔야 JSON 칸이 저장된다
    await session.commit()
    return text


# --------------------------------------------------------------------------- 게시물 초안


async def finished_books(session: AsyncSession, user_id: int, limit: int = 12) -> list[Book]:
    """초안을 만들 수 있는 책: 다 읽은 책 가운데 게시물로 올릴 수 있는 것(내 개인 파일은 뺀다)."""
    rows = await session.scalars(
        select(Book)
        .join(LibraryEntry, LibraryEntry.book_id == Book.id)
        .where(LibraryEntry.user_id == user_id, LibraryEntry.shelf == Shelf.DONE, Book.owner_id.is_(None))
        .where(Book.source != "upload")
        .order_by(LibraryEntry.updated_at.desc(), LibraryEntry.id.desc())
        .limit(limit)
    )
    return list(rows)


@dataclass
class Draft:
    caption: str
    quote: str


def draft_prompt(book: Book, lines: list[Line], comments: list[str]) -> str:
    parts = [f"책: {book.title}" + (f" · {book.author}" if book.author else "")]
    if lines:
        parts.append("내가 남긴 문장:\n" + "\n".join(f"q{i} 「{x.quote}」" for i, x in enumerate(lines, 1)))
    if comments:
        parts.append("내 반응:\n" + "\n".join(f"- {c}" for c in comments))
    return "\n\n".join(parts)


async def draft(session: AsyncSession, client: httpx.AsyncClient | None, user: User, book: Book) -> Draft:
    """초안만 만든다 — 게시물은 만들지 않는다. 좋은 문장은 내 문장 가운데서만 고른다."""
    from app.services.shop_ai import _json_obj

    lines = (await _my_lines(session, user.id, None, None, book.id))[:10]
    comments = [
        " ".join(b.split())[:120]
        for b in await session.scalars(
            select(Reaction.body)
            .where(
                Reaction.user_id == user.id,
                Reaction.book_id == book.id,
                Reaction.kind == ReactionKind.COMMENT,
            )
            .order_by(Reaction.id.desc())
            .limit(8)
        )
    ]
    await spend(session, user)
    res = await ai.chat(
        [{"role": "user", "content": draft_prompt(book, lines, comments)}],
        system=DRAFT_SYSTEM, max_tokens=400, json_mode=True, http=client,
    )  # fmt: skip
    data = _json_obj(res.text)
    if data is None:  # JSON 이 아니면 글 전체를 문구로(가짜 공급자 · 엉뚱한 답)
        caption, qid = res.text, None
    else:
        caption, qid = data.get("caption"), data.get("quote_id")
    caption = " ".join(str(caption or "").split())[:MAX_CAPTION] if isinstance(caption, str) else ""
    quote = ""
    if isinstance(qid, str) and re.fullmatch(r"q\d{1,2}", qid) and 1 <= int(qid[1:]) <= len(lines):
        quote = lines[int(qid[1:]) - 1].quote
    elif data is None and lines:
        quote = lines[0].quote  # AI 가 못 골랐어도 내 문장 하나는 제안한다
    return Draft(caption, quote[:200])
