"""같이 읽기 게시판(/together) 스크린샷용 시드 — 네트워크 없이 고전 몇 권과 모집 글을 심는다.

먼저 readme_shots.py --seed 로 계정 세 개를 심은 뒤에 쓴다:

    python scripts/readme_shots.py --seed "$DATABASE_URL"
    python scripts/seed_board.py "$DATABASE_URL"
"""

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import auth
from app.models import Book, BookChunk, User
from app.models_board import RecruitPost
from app.services import board as board_service
from app.services import races as race_service

CATALOG = json.loads((Path(__file__).parent.parent / "app" / "data" / "catalog.json").read_text())
TITLES = ["운수 좋은 날", "감자", "B사감과 러브레터", "벙어리 삼룡이"]
EXTRA = [("서연", "seoyeon@example.com"), ("도윤", "doyun@example.com"), ("예린", "yerin@example.com")]
# (방장 이메일, 책, 한 줄 소개, 하루 쪽, 정원, 같이 들어올 사람들)
POSTS = [
    (
        "minjun@example.com",
        "감자",
        "점심 먹고 5분씩, 단편 하나 끝까지 가 봐요",
        5,
        4,
        ["seoyeon@example.com"],
    ),
    ("seoyeon@example.com", "운수 좋은 날", "책 안 읽은 지 오래된 분 환영해요. 하루 3쪽이면 돼요", 3, 5, []),
    ("doyun@example.com", "벙어리 삼룡이", "주말에 몰아 읽는 사람들 모여요", 0, 3, ["yerin@example.com"]),
    (
        "yerin@example.com",
        "B사감과 러브레터",
        "출퇴근길 지하철에서 같이 읽을 분! 짧아서 부담 없어요",
        10,
        6,
        [],
    ),
]


async def main(db_url: str) -> None:
    engine = create_async_engine(db_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        for nick, email in EXTRA:
            s.add(User(nickname=nick, email=email, password_hash=auth.hash_password("password123")))
        books = {}
        for title in TITLES:
            meta = next(b for b in CATALOG if b["title"] == title)
            book = Book(
                source=meta["source"],
                external_id=meta["id"],
                title=title,
                author=meta.get("author"),
                language="ko",
                has_fulltext=True,
                total_chunks=20,
            )
            s.add(book)
            await s.flush()
            for i in range(20):
                text = f"{title} {i + 1}쪽. " + "본문 " * 80
                s.add(BookChunk(book_id=book.id, chunk_index=i, text=text, char_count=len(text)))
            books[title] = book
        await s.commit()
        users = {u.email: u for u in await s.scalars(select(User))}
        for host, title, pitch, pace, cap, joiners in POSTS:
            post = await board_service.create_post(s, users[host], books[title], pitch, pace, cap)
            for email in joiners:
                post = await s.get(RecruitPost, post.id)
                await board_service.join(s, post, users[email])
        # 하린은 서연과 예전에 다른 대결을 한 적이 있다 → 카드에 '아는 사람 1명'
        room = await race_service.create_race(s, users["harin@example.com"], books["운수 좋은 날"], 7)
        await race_service.join(s, room, users["seoyeon@example.com"])
    await engine.dispose()
    print("seeded board:", len(POSTS), "posts")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
