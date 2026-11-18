"""로컬 시연용: 하루 목표·잔디 데이터를 심는다(feature goals).

    python scripts/readme_shots.py --seed "$DATABASE_URL"   # 계정 먼저
    python scripts/seed_goals.py "$DATABASE_URL" [email]     # 그 계정에 지난 17주 읽은 기록

검증된 쪽(ReadingEvent.verified)을 날마다 무작위로 남기고, 보호권으로 메운 날 하나와 보호권 1개를 둔다.
"""

import asyncio
import datetime as dt
import random
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models_goals  # noqa: F401
from app.db.base import Base
from app.models import Book, ReadingEvent, User
from app.models_goals import ReadingDayMark, ReadingGoal
from app.services import goals


async def main(url: str, email: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        user = await s.scalar(select(User).where(User.email == email))
        if user is None:
            raise SystemExit(f"no user {email} — run readme_shots.py --seed first")
        book = Book(source="wikisource", external_id="seed-goals", title="잔디 시연 책", total_chunks=5000)
        s.add(book)
        await s.flush()
        rng = random.Random(7)
        today = goals.reading_day(goals.clock())
        chunk, freeze_day = 0, today - dt.timedelta(days=9)
        for back in range(118, -1, -1):
            day = today - dt.timedelta(days=back)
            if day == freeze_day or (back > 60 and rng.random() < 0.35) or (back == 0):
                n = 3 if back == 0 else 0
            else:
                n = rng.choice([1, 2, 3, 4, 5, 6, 7, 8, 10, 12])
            for i in range(n):
                at = goals.day_start(day) + dt.timedelta(hours=14, minutes=i * 2)
                s.add(
                    ReadingEvent(
                        user_id=user.id,
                        book_id=book.id,
                        chunk_index=chunk,
                        dwell_ms=48_000,
                        verified=True,
                        occurred_at=at,
                    )
                )
                chunk += 1
        s.add(ReadingDayMark(user_id=user.id, day=freeze_day, kind=goals.FREEZE))
        s.add(
            ReadingGoal(
                user_id=user.id, daily_pages=5, freezes=1, settled_through=today - dt.timedelta(days=1)
            )
        )
        await s.commit()
    await engine.dispose()
    print(f"seeded goals for {email}: {chunk} pages")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "harin@example.com"))
