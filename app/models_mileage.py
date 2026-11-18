"""마일리지 — 쓴 기록(원장) · 리워드 상품 · 교환 요청.

잔액 = 모은 마일리지(points_log 합) − 쓴 마일리지(mileage_spends 합). 두 표 모두 추가만 하고
행을 고치거나 지우지 않는다. 환불은 음수 amount 행 하나를 더 쓰는 것으로 한다.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models import BigId

# reward_redemptions.status
REQUESTED = "requested"
SENT = "sent"
REJECTED = "rejected"
CANCELLED = "cancelled"


class MileageSpend(Base):
    """append-only 사용 원장. amount > 0 은 쓴 것, < 0 은 돌려준 것(환불).

    seq: 차감(amount > 0)마다 사용자별로 1, 2, 3… 을 붙인다. (user_id, seq) unique 라서
    같은 잔액을 보고 동시에 두 번 쓰려 하면 하나는 반드시 막힌다 — 행 잠금이 없는 SQLite 에서도.
    idem_key: 폼마다 하나씩 주는 키. 같은 폼을 두 번 보내도 한 번만 쓴다.
    """

    __tablename__ = "mileage_spends"
    __table_args__ = (
        UniqueConstraint("user_id", "seq", name="uq_mileage_spends_user_seq"),
        UniqueConstraint("user_id", "idem_key", name="uq_mileage_spends_user_idem"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # freeze | reward | refund
    ref: Mapped[str | None] = mapped_column(String(64))
    idem_key: Mapped[str | None] = mapped_column(String(64))
    seq: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RewardItem(Base):
    """제휴 리워드 상품. is_sample 은 '예시 · 제휴 준비 중' — 교환해도 마일리지가 빠지지 않는다."""

    __tablename__ = "reward_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    partner_name: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(100))
    cost: Mapped[int] = mapped_column(Integer)
    # 남은 수량. 비어 있으면 제한 없음
    stock: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # 기본은 예시 — 관리자가 진짜 제휴를 확인하고 끈다
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RewardRedemption(Base):
    """교환 요청. requested → sent | rejected | cancelled. 거절·취소는 마일리지를 돌려준다."""

    __tablename__ = "reward_redemptions"
    __table_args__ = (
        # 한 사람에 기다리는 요청은 하나 — 동시에 두 번 눌러도 DB가 막는다
        Index(
            "uq_reward_redemptions_one_pending",
            "user_id",
            unique=True,
            sqlite_where=text("status = 'requested'"),
            postgresql_where=text("status = 'requested'"),
        ),
        Index("ix_reward_redemptions_status_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("reward_items.id"))
    cost: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=REQUESTED, server_default=REQUESTED)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_note: Mapped[str | None] = mapped_column(String(280))
