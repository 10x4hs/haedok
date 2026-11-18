"""하루 목표 · 잔디 · 연속 읽기 보호권 테이블 (feature "goals").

app/models.py 를 건드리지 않으려고 따로 둔다 — Base 는 같아서 create_all·Alembic 이 함께 본다.
"""

import datetime as dt

from sqlalchemy import Date, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models import BigId


class ReadingGoal(Base):
    """사람마다 한 줄: 하루 목표 쪽수, 가진 보호권, 어디까지 지난 날을 정산했는지."""

    __tablename__ = "reading_goals"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    daily_pages: Mapped[int] = mapped_column(SmallInteger, default=5, server_default="5")
    freezes: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    # 이 날(읽기 날짜, 새벽 4시 경계)까지는 보호권·복구 판정을 끝냈다
    settled_through: Mapped[dt.date | None] = mapped_column(Date)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReadingDayMark(Base):
    """읽지 않았지만 연속이 이어진 날 — 보호권(freeze)을 썼거나 무료 복구(repair)한 날."""

    __tablename__ = "reading_day_marks"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_reading_day_marks_user_day"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    day: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(10))  # "freeze" | "repair"
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
