"""채팅 화면 스크린샷용 데이터 — readme_shots.py --seed 로 계정을 만든 뒤 실행한다.

    python scripts/seed_chat.py "$DATABASE_URL"

하린(10쪽까지 읽음)이 방장인 대결방에 민준(3쪽) · 지우(5쪽) · 서연(7쪽)이 함께 있다.
'# 책 이야기'와 '# 수다'에 메시지 · 답장 · 반응 · 스포일러 · 멘션 · 공지가 있고,
하린은 민준 · 서연과 친구(민준은 지금 공개 고전을 읽는 중, 서연은 개인 파일을 읽는 중 — 제목은 숨겨진다),
지우는 하린에게 친구 신청을 보낸 상태, 신고 한 건. 프로필 사진은 표준 라이브러리로 만든 단색 PNG.
"""

import asyncio
import datetime as dt
import struct
import sys
import zlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def png(rgb_a: tuple[int, int, int], rgb_b: tuple[int, int, int], size: int = 96) -> bytes:
    """대각선 두 색 그러데이션 PNG(사진 대신) — Pillow 없이."""
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            row += bytes(round(a + (b - a) * t) for a, b in zip(rgb_a, rgb_b, strict=True))
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )


async def main(db_url: str) -> None:
    from app import auth
    from app.models import Book, LibraryEntry, ReadingEvent, Room, RoomMember, Shelf, User
    from app.models_chat import ChatMessage, ChatReaction, ChatRead, ChatReport, ChatThread, Friendship

    engine = create_async_engine(db_url)
    now = dt.datetime.now(dt.UTC)
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
        h.avatar = png((255, 179, 199), (143, 211, 255))
        m.avatar = png((143, 211, 255), (155, 231, 196))
        y.avatar = png((230, 195, 106), (255, 169, 90))

        book = Book(
            source="wikisource",
            external_id="채팅시연",
            title="운수 좋은 날",
            author="현진건",
            total_chunks=24,
            has_fulltext=True,
        )
        other_book = Book(
            source="wikisource",
            external_id="채팅시연2",
            title="메밀꽃 필 무렵",
            author="이효석",
            total_chunks=30,
            has_fulltext=True,
        )
        secret_upload = Book(
            source="upload",
            external_id="u-seoyeon",
            title="서연의 개인 파일",
            owner_id=y.id,
            has_fulltext=True,
            total_chunks=12,
        )
        s.add_all([book, other_book, secret_upload])
        await s.flush()
        room = Room(
            book_id=book.id,
            owner_id=h.id,
            name="운수 좋은 날 함께 읽기",
            invite_code="CHAT01",
            ends_at=now + dt.timedelta(days=5),
        )
        s.add(room)
        await s.flush()
        for u, chunk in ((h, 9), (m, 2), (j, 4), (y, 6)):
            s.add(RoomMember(room_id=room.id, user_id=u.id))
            s.add(
                LibraryEntry(
                    user_id=u.id,
                    book_id=book.id,
                    verified_chunk=chunk,
                    current_chunk=chunk + 1,
                    shelf=Shelf.READING,
                    progress_pct=round((chunk + 1) / 24 * 100),
                    updated_at=now - dt.timedelta(hours=3),
                )
            )
        # 민준은 지금(3분 전) 공개 고전을, 서연은 개인 파일을 읽는 중
        s.add(
            LibraryEntry(
                user_id=m.id,
                book_id=other_book.id,
                current_chunk=11,
                shelf=Shelf.READING,
                updated_at=now - dt.timedelta(minutes=3),
            )
        )
        s.add(
            LibraryEntry(
                user_id=y.id,
                book_id=secret_upload.id,
                current_chunk=4,
                shelf=Shelf.READING,
                updated_at=now - dt.timedelta(minutes=6),
            )
        )
        for days_ago in (0, 1, 3):  # 민준의 이번 주 읽은 날
            s.add(
                ReadingEvent(
                    user_id=m.id,
                    book_id=other_book.id,
                    chunk_index=days_ago,
                    dwell_ms=60000,
                    verified=True,
                    occurred_at=now - dt.timedelta(days=days_ago, hours=1),
                )
            )

        t = ChatThread(room_id=room.id, notice="이번 주는 12쪽까지 읽고 토요일에 이야기해요", notice_at=now)
        s.add(t)
        await s.flush()

        def msg(u, body, page, mins, reply=None):
            mm = ChatMessage(
                thread_id=t.id,
                user_id=u.id,
                body=body,
                created_at=now - dt.timedelta(minutes=mins),
                book_id=book.id if page is not None else None,
                page=page,
                reply_to_id=reply,
            )
            s.add(mm)
            return mm

        b1 = msg(j, "비 오는 장면 묘사가 정말 생생해요", 4, 190)
        await s.flush()
        b2 = msg(h, "김첨지가 왜 그렇게 집에 가기 싫어했는지 이제 알 것 같아요…", 9, 140)
        msg(y, "설렁탕 사 들고 가는 장면에서 ||마음이 무너졌어요||", 6, 60)
        await s.flush()
        b4 = msg(j, "저도 그 부분에서 멈칫했어요", 4, 58, reply=b1.id)
        msg(h, "인력거 손님이 계속 이어지는 게 오히려 불안했어요", 9, 20)
        c1 = msg(j, "다들 어디까지 읽었어요? 저는 이제 막 비 오는 장면이에요", None, 300)
        await s.flush()
        c2 = msg(m, "저는 아직 초반이에요 ㅎㅎ 오늘 밤에 더 읽을게요", None, 240)
        msg(h, "@민준 천천히 와요! 주말에 다 같이 이야기해요", None, 30)
        c4 = msg(y, "토요일 저녁 좋아요 🙌", None, 12)
        await s.flush()
        for target, who, e in (
            (b2, y, "😢"),
            (b2, j, "😢"),
            (b1, h, "👏"),
            (c2, h, "❤️"),
            (c4, h, "❤️"),
            (c4, j, "👏"),
            (b4, y, "😮"),
        ):
            s.add(ChatReaction(message_id=target.id, user_id=who.id, emoji=e))
        s.add(ChatRead(thread_id=t.id, user_id=m.id, channel="chat", last_read_id=c1.id))

        low, high = sorted((h.id, m.id))
        s.add(Friendship(user_low=low, user_high=high, requester_id=h.id, status="accepted"))
        lo_hy = sorted((h.id, y.id))
        s.add(Friendship(user_low=lo_hy[0], user_high=lo_hy[1], requester_id=y.id, status="accepted"))
        dm = ChatThread(user_low=low, user_high=high)
        s.add(dm)
        await s.flush()
        dms = []
        for u, body, mins in (
            (m, "하린님 오늘 레이스 몇 쪽 목표예요?", 1500),
            (h, "열 쪽! 그리고 결말 진짜 충격이에요", 26),
            (h, "마지막에 ||아내가 이미…||", 25),
            (m, "앗 아직 안 봤어요 ㅋㅋ 가려 줘서 고마워요", 4),
            (h, "ㅋㅋ 다 읽으면 알려 줘요", 2),
        ):
            mm = ChatMessage(
                thread_id=dm.id, user_id=u.id, body=body, created_at=now - dt.timedelta(minutes=mins)
            )
            s.add(mm)
            dms.append(mm)
        await s.flush()
        dms[4].reply_to_id = dms[3].id
        s.add(ChatReaction(message_id=dms[3].id, user_id=h.id, emoji="😂"))
        s.add(ChatRead(thread_id=dm.id, user_id=m.id, channel="chat", last_read_id=dms[3].id))
        # 민준이 올린 읽기 기록을 개인 채팅에 공유(게시물 기능이 있을 때 카드로 보인다)
        try:
            from app.models_posts import ReadingPost

            rp = ReadingPost(
                author_id=m.id,
                book_source="wikisource",
                external_id="채팅시연2",
                book_title="메밀꽃 필 무렵",
                book_author="이효석",
                status="reading",
                caption="달밤 장면이 좋아요",
                visibility="all",
                post_day=now.strftime("%Y-%m-%d"),
            )
            s.add(rp)
            await s.flush()
            s.add(
                ChatMessage(
                    thread_id=dm.id,
                    user_id=m.id,
                    body=f"제 기록이에요 /p/{rp.id}",
                    created_at=now - dt.timedelta(minutes=1),
                )
            )
        except ImportError:
            pass
        # 서연과의 긴 대화(사흘에 걸쳐 40개) — 긴 대화 화면용
        long_t = ChatThread(user_low=lo_hy[0], user_high=lo_hy[1])
        s.add(long_t)
        await s.flush()
        lines = [
            "오늘 몇 쪽 읽었어요?",
            "저는 열 쪽!",
            "와 빠르다",
            "출퇴근길에 읽어요",
            "저도 그래 볼까 봐요",
            "이 책 문장이 너무 좋아요",
            "어떤 문장이요?",
            "‘비가 오면 운수가 좋다’는 부분이요",
            "ㅋㅋ 아이러니하죠",
            "내일 대결 시작해요?",
            "좋아요 저녁 9시!",
        ]
        for k in range(40):
            who = y if k % 3 else h
            when_ = (
                now - dt.timedelta(days=3) + dt.timedelta(minutes=k * 3 + (k // 6) * 600)
            )  # 몇 분 간격 묶음, 묶음 사이 10시간
            s.add(
                ChatMessage(thread_id=long_t.id, user_id=who.id, body=lines[k % len(lines)], created_at=when_)
            )
        lo, hi = sorted((h.id, j.id))
        s.add(Friendship(user_low=lo, user_high=hi, requester_id=j.id, status="pending"))
        s.add(ChatReport(message_id=c4.id, reporter_id=j.id, reason="시연용 신고"))
        await s.commit()
    await engine.dispose()
    print("seeded chat: room", room.id)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
