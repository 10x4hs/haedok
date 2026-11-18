"""같이 읽을 사람 모집 게시판 테이블 — 글 하나가 대결방(rooms) 하나에 붙는다.

참가 자체는 room_members가 기록한다. 여기에는 모집 조건(정원·속도·한 줄 소개)과 상태만 둔다.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models import _now


class RecruitPost(Base):
    __tablename__ = "recruit_posts"
    __table_args__ = (
        CheckConstraint("capacity BETWEEN 2 AND 8", name="capacity"),
        # 게시판은 '모집 중인 글을 최신순'으로만 읽는다
        Index("ix_recruit_posts_status_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), unique=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    pitch: Mapped[str] = mapped_column(String(140))
    pace: Mapped[int] = mapped_column(SmallInteger, default=0)  # 하루 몇 쪽. 0은 '자유'
    capacity: Mapped[int] = mapped_column(SmallInteger, default=4)
    # 게시판으로 잡힌 자리 수(방장 포함). 조건부 UPDATE 한 문장으로 올려서 마지막 자리를 한 명만 잡는다
    seats: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    created_at: Mapped[datetime] = _now()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecruitReport(Base):
    """모집 글 신고 — 관리자 화면(/admin/together)에서 본다. 한 사람이 한 글에 한 번."""

    __tablename__ = "recruit_reports"
    __table_args__ = (UniqueConstraint("post_id", "reporter_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("recruit_posts.id", ondelete="CASCADE"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = _now()
