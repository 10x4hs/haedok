"""친구 + 채팅 테이블 — 친구 관계(쌍마다 한 행), 대화(방·개인), 메시지, 읽은 위치, 신고.

app/models.py 는 건드리지 않고 같은 Base 메타데이터에 올린다(app/chat.py 가 이 모듈을 불러온다).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, BigId


class Friendship(Base):
    """두 사람 사이의 관계 한 행. (작은 id, 큰 id) 순서로 저장해 방향이 달라도 한 행만 생긴다.

    status: pending(신청 중) · accepted(친구) · blocked(어느 한쪽이라도 차단)
    차단은 low_blocks / high_blocks 로 누가 막았는지 따로 적는다 — 둘 다 막을 수도 있다.
    """

    __tablename__ = "friendships"
    __table_args__ = (
        UniqueConstraint("user_low", "user_high", name="uq_friendships_pair"),
        CheckConstraint("user_low < user_high", name="pair_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_low: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    user_high: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(10))
    low_blocks: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    high_blocks: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatThread(Base):
    """대화 하나 — 대결방 채팅(room_id) 또는 친구 둘의 개인 채팅(user_low, user_high). 첫 메시지 때 생긴다."""

    __tablename__ = "chat_threads"
    __table_args__ = (UniqueConstraint("user_low", "user_high", name="uq_chat_threads_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    user_low: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    user_high: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    """메시지 한 줄. 방 채팅의 '책 이야기'는 보낼 때 서버가 검증한 쪽(page = 청크 번호)을 도장으로 찍는다.

    고치기는 없다(지우고 다시 보낸다) — 도장이 낡을 일이 없다. 지우기는 소프트 삭제(deleted_at).
    """

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_thread_id_id", "thread_id", "id"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(String(500))
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=True)
    # 보낸 사람이 그때 검증받은 마지막 청크(-1 = 아직 없음). 비어 있으면 '수다'(모두에게 보임)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 개인 채팅의 '가리기' — 탭하기 전까지 흐리게. 본문은 그대로 전달된다(예의일 뿐 보안이 아니다)
    veiled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatRead(Base):
    """사람마다 대화마다 마지막으로 본 메시지 id — 안 읽은 수는 이보다 큰 남의 메시지 수다."""

    __tablename__ = "chat_reads"

    thread_id: Mapped[int] = mapped_column(
        ForeignKey("chat_threads.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    last_read_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class ChatReport(Base):
    """메시지 신고 — 관리자 화면(/admin/chat-reports)에서 본다. 한 사람이 한 메시지에 한 번."""

    __tablename__ = "chat_reports"
    __table_args__ = (UniqueConstraint("message_id", "reporter_id", name="uq_chat_reports_message_reporter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("chat_messages.id", ondelete="CASCADE")
    )
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
