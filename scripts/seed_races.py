"""대결 화면(/races) 스크린샷용 시드 — seed_board.py 다음에 쓴다(네트워크 없음).

하린에게 진행 중인 방 셋(여럿 · 혼자 · 안 읽은 채팅)과 끝난 방 하나를 만든다.

    python scripts/readme_shots.py --seed "$DATABASE_URL"
    python scripts/seed_board.py "$DATABASE_URL"
    python scripts/seed_races.py "$DATABASE_URL"
"""

import asyncio
import datetime as dt
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Book, LibraryEntry, Room, User
from app.services import chat
from app.services import races as race_service


async def verify(s, user: User, book: Book, pages: int) -> None:
    e = await s.scalar(
        select(LibraryEntry).where(LibraryEntry.user_id == user.id, LibraryEntry.book_id == book.id)
    )
    if e is None:
        e = LibraryEntry(user_id=user.id, book_id=book.id)
        s.add(e)
    e.verified_chunk = pages - 1
    await s.commit()


async def main(db_url: str) -> None:
    engine = create_async_engine(db_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        users = {u.email: u for u in await s.scalars(select(User))}
        books = {b.title: b for b in await s.scalars(select(Book))}
        harin, minjun, seoyeon, doyun = (
            users[e]
            for e in ("harin@example.com", "minjun@example.com", "seoyeon@example.com", "doyun@example.com")
        )
        # seed_board가 만든 하린 · 서연의 '운수 좋은 날' 방: 하린 12쪽, 서연 15쪽 + 서연이 남긴 말
        room = await s.scalar(select(Room).where(Room.owner_id == harin.id))
        await race_service.join(s, room, minjun)
        await race_service.join(s, room, doyun)
        book = books["운수 좋은 날"]
        for u, p in ((harin, 12), (seoyeon, 15), (minjun, 4), (doyun, 9)):
            await verify(s, u, book, p)
        conv = await chat.room_conv(s, seoyeon, room.id)
        await chat.send(s, seoyeon, conv, "다들 어디까지 읽었어요?", channel="chat")
        # 혼자 읽는 방
        await race_service.create_race(s, harin, books["벙어리 삼룡이"], 7)
        # 끝난 방
        done = await race_service.create_race(s, harin, books["감자"], 3)
        await race_service.join(s, done, minjun)
        await verify(s, harin, books["감자"], 20)
        await verify(s, minjun, books["감자"], 17)
        done = await s.get(Room, done.id)
        done.ends_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
        await s.commit()
    await engine.dispose()
    print("seeded races for harin")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
