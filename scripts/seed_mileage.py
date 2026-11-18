"""마일리지 화면을 로컬에서 보려고 데이터를 심는다(운영 DB에 쓰지 말 것).

    python scripts/readme_shots.py --seed "$DATABASE_URL"   # 계정 먼저
    python scripts/seed_mileage.py "$DATABASE_URL"          # 예시 상품 · 적립 · 요청 하나

예시 상품은 모두 지어낸 이름이고 is_sample 이다. --demo-real 을 주면 로컬 확인용
'진짜 교환' 상품 하나(이름에 '로컬 데모'가 붙는다)와 그 교환 요청을 만든다.
"""

import asyncio
import datetime as dt
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models  # noqa: F401
from app.db.base import Base
from app.models import PointsLog, User
from app.models_mileage import RewardItem
from app.services import mileage, points


async def main(url: str, demo_real: bool) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await mileage.seed_samples(s)
        old = dt.datetime.now(dt.UTC) - dt.timedelta(days=40)
        for email, amount in (("harin@example.com", 260), ("minjun@example.com", 640)):
            user = await s.scalar(select(User).where(User.email == email))
            if user is None:
                continue
            user.created_at = old
            row = PointsLog(user_id=user.id, source=points.STREAK, amount=amount, ref_id="seed-mileage")
            s.add(row)
            await s.commit()
        if demo_real:
            item = RewardItem(
                partner_name="로컬 데모", title="데모 교환권", cost=150, stock=5, is_sample=False
            )
            s.add(item)
            await s.commit()
            buyer = await s.scalar(select(User).where(User.email == "minjun@example.com"))
            if buyer is not None:
                await mileage.redeem(s, buyer, item.id, "seed-mileage-demo")
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], "--demo-real" in sys.argv))
