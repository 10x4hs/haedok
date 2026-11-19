"""AI 기능의 테이블 — 벡터 저장소(책 · 본문 조각), 책에게 물어본 기록, 사용자 취향 캐시.

벡터 열(Vector)은 Postgres 에서는 pgvector 의 vector(N), SQLite(테스트 · 로컬)에서는 float32 바이트다.
읽고 쓰는 일은 모두 app/services/vectors.py 가 맡는다 — 여기서는 열 모양만 정한다.
"""

from __future__ import annotations

import struct
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, UserDefinedType

from app.core.config import get_settings
from app.db.base import Base


class PGVector(UserDefinedType):
    """pgvector 의 vector(N) — DDL 만 그린다. 값은 vectors.py 가 text() 와 CAST 로 주고받는다."""

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kw) -> str:
        return f"vector({self.dim})"


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(data: bytes | memoryview) -> list[float]:
    data = bytes(data)
    return list(struct.unpack(f"<{len(data) // 4}f", data))


class Vector(TypeDecorator):
    """Postgres: vector(dim) / 그 밖: float32 바이트(LargeBinary)."""

    impl = LargeBinary
    cache_ok = True

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PGVector(self.dim))
        return dialect.type_descriptor(LargeBinary())

    def process_bind_param(self, value, dialect):
        if value is None or isinstance(value, bytes | str):
            return value
        if dialect.name == "postgresql":
            return "[" + ",".join(f"{x:.7g}" for x in value) + "]"
        return pack(list(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return [float(x) for x in value.strip("[]").split(",") if x]
        if isinstance(value, bytes | memoryview):
            return unpack(value)
        return list(value)


DIM = get_settings().ai_embed_dim


class BookVector(Base):
    """카탈로그(퍼블릭 도메인 고전) + 서점 책 한 권의 메타데이터 벡터. source="store" 는 서점 책."""

    __tablename__ = "book_vectors"
    __table_args__ = (
        # 코사인 최근접 — Postgres 에서만 만든다(SQLite 는 파이썬으로 계산)
        Index(
            "ix_book_vectors_vec_hnsw",
            "vec",
            postgresql_using="hnsw",
            postgresql_ops={"vec": "vector_cosine_ops"},
        ).ddl_if(dialect="postgresql"),
    )

    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    # 임베딩한 글의 해시 — 같으면 다시 임베딩하지 않는다(스크립트 · 관리자 버튼을 몇 번 돌려도 같다)
    content_hash: Mapped[str] = mapped_column(String(16))
    vec: Mapped[list[float]] = mapped_column(Vector(DIM))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkVector(Base):
    """책 본문 한 쪽(청크)을 몇 조각(part)으로 나눈 것의 벡터.

    글은 저장하지 않고 book_chunks 에서 다시 자른다(vectors.split_parts).
    """

    __tablename__ = "chunk_vectors"

    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    part: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    vec: Mapped[list[float]] = mapped_column(Vector(DIM))


class AskLog(Base):
    """책에게 물어본 질문과 답 — 사람마다 책마다 최근 5개만 남긴다."""

    __tablename__ = "ai_questions"
    __table_args__ = (Index("ix_ai_questions_user_book", "user_id", "book_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(String(300))
    answer: Mapped[str] = mapped_column(Text)
    pages: Mapped[list] = mapped_column(JSON, default=list)  # 근거 쪽 번호(1부터)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserTaste(Base):
    """사용자 취향 벡터 캐시 — 서재 · 게시물이 바뀌면(basis_at 보다 새 기록) 읽을 때 다시 계산한다."""

    __tablename__ = "ai_tastes"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    vec: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # float32, 양쪽 DB 같은 모양
    basis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
