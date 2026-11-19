"""라운드 7 피드 스크린샷용 데이터 — seed_feed.py 뒤에 실행한다.

    python scripts/seed_feed_r7.py "$DATABASE_URL"

- 친구가 아닌 '새로운 사람' 유나의 공개 게시물 둘(전체 탭에 섞인다) · 친구만 글 하나(보이면 안 된다).
- 민준이 하린에게 보낸 안 읽은 메시지 둘(피드 머리의 메시지 숫자).
- 하린이 다 읽은 「소년이 온다」를 민준이 지금 읽는 중(이 책 읽는 사람들) ·
  유나도 서연의 공개 모집 방에 들어 있고 같은 책을 읽는 중(친구 신청 단추).
- 하린 · 유나 · 도윤이 같은 대결방(알 수도 있는 사람).
"""

import asyncio
import datetime as dt
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def main(db_url: str) -> None:
    from app import auth
    from app.models import Book, LibraryEntry, Room, RoomMember, Shelf, User
    from app.models_board import RecruitPost
    from app.models_posts import PostLike, ReadingPost
    from app.services import chat as chat_service

    engine = create_async_engine(db_url)
    now = dt.datetime.now(dt.UTC)
    ago = lambda h: now - dt.timedelta(hours=h)  # noqa: E731
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        users = {u.nickname: u for u in await s.scalars(select(User))}
        harin, minjun, doyun = users["하린"], users["민준"], users["도윤"]
        yuna = User(
            nickname="유나", email="yuna@example.com", password_hash=auth.hash_password("password123")
        )
        s.add(yuna)
        await s.flush()
        for i, (title, author, caption, h, vis) in enumerate(
            (
                ("나의 라임 오렌지나무", "바스콘셀로스", "제제가 자꾸 생각나요.", 1.2, "all"),
                ("아몬드", "손원평", "감정을 배운다는 말이 오래 남았어요.", 26, "all"),
                ("일기", "", "친구에게만 보이는 글", 0.8, "friends"),
            )
        ):
            s.add(
                ReadingPost(
                    author_id=yuna.id,
                    book_source="store",
                    external_id=f"seed-r7-{i}",
                    book_title=title,
                    book_author=author or None,
                    status="done" if i else "reading",
                    rating=5 if i else None,
                    caption=caption,
                    visibility=vis,
                    post_day=ago(h).date().isoformat(),
                    created_at=ago(h),
                )
            )
        await s.flush()
        newest = await s.scalar(
            select(ReadingPost.id).where(ReadingPost.author_id == yuna.id).order_by(ReadingPost.id)
        )
        s.add_all([PostLike(post_id=newest, user_id=minjun.id), PostLike(post_id=newest, user_id=doyun.id)])

        # 이 책 읽는 사람들: 하린이 다 읽은 책을 민준(친구) · 유나(공개 모집 방 사람)가 읽는 중
        book = Book(source="store", external_id="seed-r7", title="소년이 온다", author="한강", total_chunks=9)
        s.add(book)
        await s.flush()
        s.add(LibraryEntry(user_id=harin.id, book_id=book.id, shelf=Shelf.DONE, updated_at=ago(30)))
        s.add(LibraryEntry(user_id=minjun.id, book_id=book.id, shelf=Shelf.READING, updated_at=ago(2)))
        s.add(LibraryEntry(user_id=yuna.id, book_id=book.id, shelf=Shelf.READING, updated_at=ago(3)))
        rp = await s.scalar(select(RecruitPost).where(RecruitPost.status == "open").order_by(RecruitPost.id))
        if rp is not None:
            s.add(RoomMember(room_id=rp.room_id, user_id=yuna.id))

        # 알 수도 있는 사람: 하린 · 유나 · 도윤이 같은 대결방
        room = Room(book_id=book.id, owner_id=harin.id, name="소년이 온다 함께", invite_code="R7ROOM")
        s.add(room)
        await s.flush()
        for u in (harin, yuna, doyun):
            s.add(RoomMember(room_id=room.id, user_id=u.id))

        # 안 읽은 메시지 둘(민준 → 하린)
        conv = await chat_service.dm_conv(s, minjun, harin.id)
        await chat_service.send(s, minjun, conv, "소년이 온다 다 읽었어요? 같이 이야기해요")
        await chat_service.send(s, minjun, conv, "주말에 시간 돼요?")
        await s.commit()
    await engine.dispose()
    print("seeded round-7 feed data")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
