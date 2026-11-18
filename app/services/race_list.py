"""대결 화면(/races)의 '내 방' 줄 — 방마다 "어디까지 왔지?"에 답하는 상태 한 줄.

방이 여러 개여도 쿼리 몇 번으로 끝낸다(방마다 standings를 부르지 않는다).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Book, LibraryEntry, Room, RoomMember
from app.services import races as race_service


@dataclass
class RoomRow:
    room: Room
    book: Book
    over: bool
    count: int  # 방에 있는 사람(나 포함)
    rank: int  # 검증된 쪽 수 순위(같으면 같은 순위)
    pages: int  # 내가 검증받은 쪽 수
    total: int
    unread: int = 0

    @property
    def status(self) -> str:
        where = f"{self.pages}/{self.total}쪽"
        if self.over:
            return (
                f"끝남 · {self.count}명 중 {self.rank}위 · {where}" if self.count > 1 else f"끝남 · {where}"
            )
        if self.count <= 1:
            return "혼자 읽는 중 · 친구를 불러 보세요"
        return f"{self.count}명 중 {self.rank}위 · {where}"


async def _unread_by_room(session: AsyncSession, user_id: int, room_ids: list[int]) -> dict[int, int]:
    """채팅 기능이 켜져 있을 때만: 방 채팅의 안 읽은 수. 차단·삭제 규칙은 채팅 서비스 것을 그대로 쓴다."""
    if not room_ids or not get_settings().feature_chat:
        return {}
    from app.models_chat import ChatThread
    from app.services import chat

    threads = dict(
        (
            await session.execute(
                select(ChatThread.id, ChatThread.room_id).where(ChatThread.room_id.in_(room_ids))
            )
        ).all()
    )
    if not threads:
        return {}
    counts = await chat._unread(session, user_id, list(threads), await chat.blocked_by_me(session, user_id))
    return {threads[tid]: n for tid, n in counts.items() if n}


async def my_rooms(session: AsyncSession, user_id: int) -> list[RoomRow]:
    """내가 들어 있는 방, 최근에 만든 방부터."""
    pairs = await race_service.races_of(session, user_id)
    if not pairs:
        return []
    ids = [r.id for r, _ in pairs]
    rows = await session.execute(
        select(RoomMember.room_id, RoomMember.user_id, LibraryEntry.verified_chunk)
        .join(Room, Room.id == RoomMember.room_id)
        .outerjoin(
            LibraryEntry,
            (LibraryEntry.user_id == RoomMember.user_id) & (LibraryEntry.book_id == Room.book_id),
        )
        .where(RoomMember.room_id.in_(ids))
    )
    pages: dict[int, dict[int, int]] = {}
    for room_id, uid, verified in rows.all():
        pages.setdefault(room_id, {})[uid] = (verified + 1) if verified is not None else 0
    unread = await _unread_by_room(session, user_id, ids)
    out = []
    for room, book in pairs:
        by_user = pages.get(room.id, {})
        mine = by_user.get(user_id, 0)
        out.append(
            RoomRow(
                room=room,
                book=book,
                over=race_service.is_over(room),
                count=len(by_user),
                rank=1 + sum(1 for p in by_user.values() if p > mine),
                pages=mine,
                total=max(book.total_chunks, 1),
                unread=unread.get(room.id, 0),
            )
        )
    return out
