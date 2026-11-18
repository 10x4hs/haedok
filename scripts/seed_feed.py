"""라운드 6 피드 스크린샷용 활동 소식 — seed_posts_ig.py 뒤에 실행한다.

    python scripts/seed_feed.py "$DATABASE_URL"

하린의 친구(민준 · 서연)에게 이미 있는 표로만 활동을 남긴다(새 테이블 없음):
민준이 「메밀꽃 필 무렵」을 다 읽음(서재) · 민준이 대결에서 가장 먼저 완주(points_log race_finish) ·
서연이 14일 연속 읽기(points_log streak 두 번) · 서연이 같이 읽을 사람 모집(recruit_posts).
"""

import asyncio
import datetime as dt
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def main(db_url: str) -> None:
    from app.models import Book, LibraryEntry, PointsLog, Room, RoomMember, Shelf, User
    from app.models_board import RecruitPost

    engine = create_async_engine(db_url)
    now = dt.datetime.now(dt.UTC)
    ago = lambda h: now - dt.timedelta(hours=h)  # noqa: E731
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        users = {u.nickname: u for u in await s.scalars(select(User))}
        minjun, seoyeon = users["민준"], users["서연"]
        books = {}
        for title, author in (
            ("메밀꽃 필 무렵", "이효석"),
            ("소나기", "황순원"),
            ("사랑손님과 어머니", "주요섭"),
        ):
            b = Book(
                source="store", external_id=f"seed-feed-{title}", title=title, author=author, total_chunks=12
            )
            s.add(b)
            books[title] = b
        await s.flush()
        s.add(
            LibraryEntry(
                user_id=minjun.id, book_id=books["메밀꽃 필 무렵"].id, shelf=Shelf.DONE, updated_at=ago(3.5)
            )
        )
        race = Room(
            book_id=books["소나기"].id, owner_id=minjun.id, name="소나기 한 번에", invite_code="FEEDRC"
        )
        s.add(race)
        await s.flush()
        s.add(RoomMember(room_id=race.id, user_id=minjun.id))
        s.add(
            PointsLog(
                user_id=minjun.id, source="race_finish", amount=20, ref_id=str(race.id), created_at=ago(9)
            )
        )
        today = now.date()
        for i, h in ((1, 30 + 7 * 24), (0, 6.5)):
            ref = (today - dt.timedelta(days=7 * i)).isoformat()
            s.add(PointsLog(user_id=seoyeon.id, source="streak", amount=30, ref_id=ref, created_at=ago(h)))
        recruit_room = Room(
            book_id=books["사랑손님과 어머니"].id,
            owner_id=seoyeon.id,
            name="주말에 같이",
            invite_code="FEEDRP",
        )
        s.add(recruit_room)
        await s.flush()
        s.add(RoomMember(room_id=recruit_room.id, user_id=seoyeon.id))
        s.add(
            RecruitPost(
                room_id=recruit_room.id,
                author_id=seoyeon.id,
                book_id=books["사랑손님과 어머니"].id,
                pitch="주말마다 한 장씩 같이 읽어요",
                pace=5,
                capacity=4,
                created_at=ago(12),
            )
        )
        await s.commit()
    await engine.dispose()
    print("seeded feed activities")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
