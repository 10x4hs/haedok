"""홈 화면 데이터 — 유명한 무료 고전 띠(showcase), 이어 읽기, 친구들이 읽는 중.

showcase는 app/data/showcase.json(scripts/build_showcase.py)에서 읽는다. 모든 책은 앱이 본문을
가져올 수 있는 퍼블릭 도메인 소스(위키문헌·구텐베르크)이고, 표지는 scripts/build_covers.py로 직접 그린
타이포그래피 표지다(출판사 표지를 쓰지 않는다).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Book, LibraryEntry, Shelf, User
from app.services.books import friend_ids
from app.services.search import FULLTEXT_SOURCES

APP = Path(__file__).resolve().parent.parent
GROUPS = ("ko", "world")


def load_showcase(path: Path = APP / "data" / "showcase.json") -> list[dict]:
    """읽을 수 있는 책만 남긴다: 본문 소스가 있고, 표지 파일이 저장소에 있는 것."""
    out = []
    for raw in json.loads(path.read_text()):
        b = dict(raw)
        if b.get("source") not in FULLTEXT_SOURCES or not b.get("id") or b.get("group") not in GROUPS:
            continue
        if not (APP / b["cover"].removeprefix("/")).is_file():
            continue
        b["readable"] = True  # 첫 화면(landing.html) 띠가 이 값으로 읽기 링크를 단다
        b["time"] = reading_time(b["minutes"])
        out.append(b)
    return out


def reading_time(minutes: int) -> str:
    """'약 20분', '약 3시간 20분' — 10분 단위로 다듬어 보여 준다."""
    if minutes < 10:
        return f"{max(1, minutes)}분"
    if minutes < 60:
        return f"약 {round(minutes / 5) * 5}분"
    h, m = divmod(round(minutes / 10) * 10, 60)
    return f"약 {h}시간" + (f" {m}분" if m else "")


async def continue_reading(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[tuple[LibraryEntry, Book]]:
    """읽다 만 책 — 다 읽은 책과 아직 펼치지 않은 책(0%, '읽고 싶은')은 뺀다. 최근 순."""
    rows = await session.execute(
        select(LibraryEntry, Book)
        .join(Book, Book.id == LibraryEntry.book_id)
        .where(
            LibraryEntry.user_id == user_id,
            LibraryEntry.shelf != Shelf.DONE,
            LibraryEntry.progress_pct < 100,
            (LibraryEntry.progress_pct > 0) | (LibraryEntry.shelf == Shelf.READING),
        )
        .order_by(LibraryEntry.updated_at.desc(), LibraryEntry.id.desc())
        .limit(limit)
    )
    return [(e, b) for e, b in rows.all()]


@dataclass
class FriendBook:
    book: Book
    friends: list[User] = field(default_factory=list)


async def friends_reading(session: AsyncSession, user_id: int, limit: int = 10) -> list[FriendBook]:
    """친구(같은 방·팀)가 읽고 있는 책 — 책마다 친구를 모아 최근 순으로. 남의 업로드는 뺀다."""
    ids = await friend_ids(session, user_id)
    if not ids:
        return []
    rows = await session.execute(
        select(LibraryEntry, Book, User)
        .join(Book, Book.id == LibraryEntry.book_id)
        .join(User, User.id == LibraryEntry.user_id)
        .where(
            LibraryEntry.user_id.in_(ids),
            Book.owner_id.is_(None),
            LibraryEntry.shelf != Shelf.DONE,
            (LibraryEntry.progress_pct > 0) | (LibraryEntry.shelf == Shelf.READING),
        )
        .order_by(LibraryEntry.updated_at.desc(), LibraryEntry.id.desc())
    )
    books: dict[int, FriendBook] = {}
    for _, book, user in rows.all():
        fb = books.get(book.id)
        if fb is None:
            if len(books) >= limit:
                continue
            fb = books[book.id] = FriendBook(book)
        fb.friends.append(user)
    return list(books.values())
