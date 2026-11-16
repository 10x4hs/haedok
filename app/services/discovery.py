"""탐색 화면의 재료: 책 분량 → 예상 시간, 분위기 필터, 친구들이 읽는 책.

분량은 scripts/build_lengths.py가 모은 어림 글자 수(app/data/lengths.json)를 쓴다. 값이 없는 책은
같은 장르의 중앙값으로 채운다 — 화면에는 늘 '약'을 붙여 어림값이라는 걸 드러낸다.
"""

import json
import math
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Book, LibraryEntry, Shelf, User
from app.services.books import friend_ids
from app.services.chunker import target_for

DATA = Path(__file__).resolve().parent.parent / "data"
# 묵독 속도: 한국어 1분에 약 550자, 영어 약 230단어(≈ 1,300자)
CHARS_PER_MIN = {"ko": 550}
DEFAULT_CHARS_PER_MIN = 1300
FALLBACK_CHARS = 20_000


def load_lengths() -> dict[str, int]:
    path = DATA / "lengths.json"
    return json.loads(path.read_text()) if path.exists() else {}


def minutes_for(chars: int, lang: str | None) -> int:
    return max(1, round(chars / CHARS_PER_MIN.get(lang or "", DEFAULT_CHARS_PER_MIN)))


def pages_for(chars: int, lang: str | None) -> int:
    """앱의 '쪽'(청크) 단위로 센 분량."""
    return max(1, math.ceil(chars / target_for(lang)))


def time_label(minutes: int) -> str:
    """'약 15분' · '약 1시간 40분' · '약 12시간' — 길수록 거칠게 반올림한다."""
    if minutes < 10:
        return f"약 {minutes}분"
    if minutes < 60:
        return f"약 {round(minutes / 5) * 5}분"
    if minutes < 600:
        h, m = divmod(round(minutes / 10) * 10, 60)
        return f"약 {h}시간" + (f" {m}분" if m else "")
    return f"약 {round(minutes / 60)}시간"


def enrich(catalog: list[dict], lengths: dict[str, int]) -> None:
    """카탈로그 항목마다 chars · minutes · pages · time 을 붙인다(제자리 수정)."""
    by_genre: dict[str, list[int]] = defaultdict(list)
    for b in catalog:
        if n := lengths.get(f"{b['source']}:{b['id']}"):
            by_genre[b["genre"]].append(n)
    medians = {g: int(statistics.median(v)) for g, v in by_genre.items()}
    for b in catalog:
        chars = lengths.get(f"{b['source']}:{b['id']}") or medians.get(b["genre"], FALLBACK_CHARS)
        b["chars"] = chars
        b["minutes"] = minutes_for(chars, b.get("lang"))
        b["pages"] = pages_for(chars, b.get("lang"))
        b["time"] = time_label(b["minutes"])


@dataclass(frozen=True)
class Mood:
    key: str
    label: str
    icon: str
    title: str
    hint: str
    match: Callable[[dict], bool]


WORLD_GENRES = {"세계 고전", "세계 · 로맨스", "세계 · 모험", "세계 · 철학"}
NIGHT_GENRES = {"시", "시집", "수필", "동화", "세계 · 시", "세계 · 공포"}

MOODS: dict[str, Mood] = {
    m.key: m
    for m in [
        Mood("short", "짧게 읽기 좋은", "clock", "30분이면 다 읽는 책", "출퇴근길 한 번이면 끝나요",
             lambda b: b["minutes"] <= 30),
        Mood("night", "밤에 읽기 좋은", "moon", "잠들기 전에 펼치기 좋은 책", "시·수필·동화, 조금 오싹한 이야기",  # noqa: E501
             lambda b: b["genre"] in NIGHT_GENRES and b["minutes"] <= 90),
        Mood("ko-short", "한국 단편", "leaf", "한국 단편소설", "교과서에서 만난 그 이야기들",
             lambda b: b["genre"] == "단편소설"),
        Mood("world", "세계 고전", "globe", "세계 고전", "한 번쯤 이름을 들어 본 책",
             lambda b: b["lang"] != "ko" and b["genre"] in WORLD_GENRES),
        Mood("poem", "시", "feather", "시와 시집", "한 편에 1분, 오래 남는 문장",
             lambda b: b["genre"] in {"시", "시집", "세계 · 시"}),
        Mood("mystery", "추리", "lens", "추리·미스터리", "범인을 끝까지 맞혀 보세요",
             lambda b: b["genre"] == "세계 · 추리"),
        Mood("ko", "한국 문학", "book", "한국 문학 대표작", "많이 읽는 순서로 모았어요",
             lambda b: b["lang"] == "ko"),
    ]
}  # fmt: skip
CHIP_MOODS = ["short", "night", "ko-short", "world", "poem", "mystery"]


def by_popularity(books: list[dict], order: list[tuple[str, str]]) -> list[dict]:
    """인기 순서(popular.json)에 있는 책을 앞으로, 나머지는 표지 있는 책 → 원래 순서."""
    rank = {k: i for i, k in enumerate(order)}
    return sorted(books, key=lambda b: (rank.get((b["source"], b["id"]), len(rank)), not b.get("cover")))


def mood_books(catalog: list[dict], key: str, order: list[tuple[str, str]]) -> list[dict]:
    mood = MOODS[key]
    return by_popularity([b for b in catalog if mood.match(b)], order)


async def friends_reading(session: AsyncSession, user_id: int) -> dict[tuple[str, str], list[User]]:
    """나와 방(대결)이나 팀을 같이 쓰는 사람들이 서재에 담아 읽고 있거나 다 읽은 공개 책.

    (source, external_id) → 친구 목록(최근에 펼친 순). 올린 개인 파일은 빼고, '읽고 싶어요'만 담은 책도 뺀다.
    """
    ids = await friend_ids(session, user_id)
    if not ids:
        return {}
    rows = await session.execute(
        select(Book.source, Book.external_id, User)
        .join(LibraryEntry, LibraryEntry.book_id == Book.id)
        .join(User, User.id == LibraryEntry.user_id)
        .where(
            LibraryEntry.user_id.in_(ids),
            Book.owner_id.is_(None),
            LibraryEntry.shelf.in_([Shelf.READING, Shelf.DONE]),
        )
        .order_by(LibraryEntry.updated_at.desc(), User.id)
    )
    out: dict[tuple[str, str], list[User]] = defaultdict(list)
    for source, ext, user in rows.all():
        if user not in out[(source, ext)]:
            out[(source, ext)].append(user)
    return dict(out)
