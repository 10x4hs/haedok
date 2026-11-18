"""채팅 화면 스크린샷용 데이터 — readme_shots.py --seed 로 계정을 만든 뒤 실행한다.

    python scripts/seed_chat.py "$DATABASE_URL"

하린(10쪽까지 읽음)이 방장인 대결방에 민준(3쪽) · 지우(5쪽) · 서연이 함께 있고, 책 이야기 · 수다가 섞여 있다.
하린과 민준은 친구(개인 채팅에 가린 메시지 하나), 지우는 하린에게 친구 신청을 보낸 상태, 신고 한 건.
"""

import asyncio
import datetime as dt
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def main(db_url: str) -> None:
    from app import auth
    from app.models import Book, LibraryEntry, Room, RoomMember, User
    from app.models_chat import ChatMessage, ChatRead, ChatReport, ChatThread, Friendship

    engine = create_async_engine(db_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        users = {u.nickname: u for u in await s.scalars(select(User))}
        if "서연" not in users:
            u = User(
                nickname="서연", email="seoyeon@example.com", password_hash=auth.hash_password("password123")
            )
            s.add(u)
            await s.flush()
            users["서연"] = u
        h, m, j, y = users["하린"], users["민준"], users["지우"], users["서연"]
        book = Book(
            source="wikisource",
            external_id="채팅시연",
            title="운수 좋은 날",
            author="현진건",
            total_chunks=24,
            has_fulltext=True,
        )
        s.add(book)
        await s.flush()
        room = Room(
            book_id=book.id,
            owner_id=h.id,
            name="운수 좋은 날",
            invite_code="CHAT01",
            ends_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=5),
        )
        s.add(room)
        await s.flush()
        for u, chunk in ((h, 9), (m, 2), (j, 4), (y, 6)):
            s.add(RoomMember(room_id=room.id, user_id=u.id))
            s.add(LibraryEntry(user_id=u.id, book_id=book.id, verified_chunk=chunk, current_chunk=chunk + 1))
        t = ChatThread(room_id=room.id)
        s.add(t)
        await s.flush()
        now = dt.datetime.now(dt.UTC)
        lines = [
            (j, "다들 어디까지 읽었어요? 저는 이제 막 비 오는 장면이에요", None, 50),
            (h, "김첨지가 왜 그렇게 집에 가기 싫어했는지 이제 알 것 같아요…", 9, 40),
            (m, "저는 아직 초반이에요 ㅎㅎ 오늘 밤에 더 읽을게요", None, 30),
            (y, "설렁탕 장면에서 마음이 너무 아팠어요", 6, 20),
            (j, "인력거 손님이 계속 이어지는 게 오히려 불안했어요", 4, 12),
            (h, "주말에 다 같이 마지막 장 읽고 얘기해요!", None, 5),
        ]
        last = None
        for u, body, page, mins in lines:
            last = ChatMessage(
                thread_id=t.id,
                user_id=u.id,
                body=body,
                created_at=now - dt.timedelta(minutes=mins),
                book_id=book.id if page is not None else None,
                page=page,
            )
            s.add(last)
        await s.flush()
        s.add(ChatRead(thread_id=t.id, user_id=m.id, last_read_id=last.id - 3))

        low, high = sorted((h.id, m.id))
        s.add(Friendship(user_low=low, user_high=high, requester_id=h.id, status="accepted"))
        dm = ChatThread(user_low=low, user_high=high)
        s.add(dm)
        await s.flush()
        for u, body, veiled, mins in (
            (m, "하린님 오늘 레이스 몇 쪽 목표예요?", False, 25),
            (h, "열 쪽! 그리고 결말 진짜 충격이에요", False, 22),
            (h, "마지막에 아내가…", True, 21),
            (m, "앗 아직 안 봤어요 ㅋㅋ 가려 줘서 고마워요", False, 3),
        ):
            s.add(
                ChatMessage(
                    thread_id=dm.id,
                    user_id=u.id,
                    body=body,
                    veiled=veiled,
                    created_at=now - dt.timedelta(minutes=mins),
                )
            )
        lo, hi = sorted((h.id, j.id))
        s.add(Friendship(user_low=lo, user_high=hi, requester_id=j.id, status="pending"))
        await s.flush()
        s.add(ChatReport(message_id=last.id, reporter_id=y.id, reason="시연용 신고"))
        await s.commit()
    await engine.dispose()
    print("seeded chat: room", room.id)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
