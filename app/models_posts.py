"""읽은 책 게시물 테이블 — 게시물(책 스냅샷 + 내 생각), 좋아요, 댓글, 신고.

책은 스냅샷(제목·저자·표지·출처·외부 id)으로 저장한다 —
카탈로그가 바뀌거나 books 행이 지워져도 게시물은 남는다.
app/models.py 는 건드리지 않고 같은 Base 메타데이터에 올린다(app/posts.py 가 이 모듈을 불러온다).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ReadingPost(Base):
    """게시물 한 개 = 책 한 권 + 내 생각. 지우기는 소프트 삭제(deleted_at)."""

    __tablename__ = "reading_posts"
    __table_args__ = (
        CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="rating_range"),
        # 같은 사람이 같은 책을 하루(한국 날짜)에 한 번만 — 서비스가 먼저 보고, 이 제약이 동시 요청을 막는다
        UniqueConstraint(
            "author_id", "book_source", "external_id", "post_day", name="uq_reading_posts_daily"
        ),
        # 피드 · 프로필은 '최신순 + 커서(id)'로만 읽는다
        Index("ix_reading_posts_author_id_id", "author_id", "id"),
        Index("ix_reading_posts_visibility_id", "visibility", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # 원래 books 행(있으면). 지워져도 스냅샷이 남는다
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"), nullable=True)
    book_source: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(512))
    book_title: Mapped[str] = mapped_column(String(500))
    book_author: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(10))  # done(읽었어요) | reading(읽는 중)
    rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    caption: Mapped[str] = mapped_column(String(2200), default="", server_default="")  # 인스타처럼 2,200자
    quote: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 좋은 문장 한 줄
    visibility: Mapped[str] = mapped_column(String(10), default="all", server_default="all")  # all | friends
    post_day: Mapped[str] = mapped_column(String(10))  # 올린 날(한국 날짜, YYYY-MM-DD)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 관리자가 내렸으면 그 관리자 id(본인이 지웠으면 본인 id)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PostLike(Base):
    """좋아요 — 복합 기본 키로 한 사람이 한 게시물에 하나만."""

    __tablename__ = "post_likes"

    post_id: Mapped[int] = mapped_column(ForeignKey("reading_posts.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PostComment(Base):
    """댓글 — 고치기는 없고 지우기만(소프트 삭제). 최신이 아래."""

    __tablename__ = "post_comments"
    __table_args__ = (Index("ix_post_comments_post_id_id", "post_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("reading_posts.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 답글이면 윗댓글 id — 인스타처럼 한 단계만(답글의 답글은 같은 윗댓글에 붙는다)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("post_comments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PostReport(Base):
    """게시물 신고 — 관리자 화면(/admin/posts)에서 본다. 한 사람이 한 게시물에 한 번."""

    __tablename__ = "post_reports"
    __table_args__ = (UniqueConstraint("post_id", "reporter_id", name="uq_post_reports_post_reporter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("reading_posts.id", ondelete="CASCADE"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(20))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PostPhoto(Base):
    """게시물 사진(1~5장) — 파일 저장소가 없어 프로필 사진(User.avatar)처럼 DB에 JPEG 바이트로 둔다.

    서버가 EXIF·GPS 같은 APP1~15 · COM 조각을 걷어 낸 뒤에만 저장한다(app/services/post_photos.py).
    data 는 목록을 읽을 때 끌려오지 않게 미룬다(deferred) — /media/p/... 에서만 읽는다.
    digest 는 내용 해시 앞 16자, 주소의 ?v= 에 쓴다(바뀌면 캐시가 새로 받는다).
    """

    __tablename__ = "post_photos"

    post_id: Mapped[int] = mapped_column(ForeignKey("reading_posts.id", ondelete="CASCADE"), primary_key=True)
    idx: Mapped[int] = mapped_column(SmallInteger, primary_key=True)  # 0부터, 보여 주는 순서
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PostSave(Base):
    """저장(책갈피) — 저장한 사람만 본다. 글쓴이에게도 누가 저장했는지 알리지 않는다."""

    __tablename__ = "post_saves"
    __table_args__ = (Index("ix_post_saves_user_id_created_at", "user_id", "created_at"),)

    post_id: Mapped[int] = mapped_column(ForeignKey("reading_posts.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PostCommentLike(Base):
    """댓글 좋아요(댓글 오른쪽의 작은 하트) — 한 사람이 한 댓글에 하나."""

    __tablename__ = "post_comment_likes"

    comment_id: Mapped[int] = mapped_column(
        ForeignKey("post_comments.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PostCommentReport(Base):
    """댓글 신고 — 관리자 화면(/admin/posts)에서 본다. 한 사람이 한 댓글에 한 번."""

    __tablename__ = "post_comment_reports"
    __table_args__ = (
        UniqueConstraint("comment_id", "reporter_id", name="uq_post_comment_reports_comment_reporter"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("post_comments.id", ondelete="CASCADE"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(20))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProfileBio(Base):
    """프로필 소개 한 줄(≤ 150자, 인스타와 같은 길이). users 테이블은 건드리지 않는다."""

    __tablename__ = "profile_bios"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    bio: Mapped[str] = mapped_column(String(150), default="", server_default="")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
