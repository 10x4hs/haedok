"""카탈로그(바로 읽는 고전) + 서점 책의 메타데이터 벡터를 채운다 — '나를 위한 다음 책'과 AI 도우미가 쓴다.

    # 운영(Neon): 마이그레이션(alembic upgrade head) 뒤, AI 설정을 넣고 한 번 돌린다
    DATABASE_URL=postgresql+asyncpg://… AI_PROVIDER=openai_compat AI_BASE_URL=… AI_API_KEY=… \\
        AI_EMBED_MODEL=… python scripts/build_book_vectors.py
    python scripts/build_book_vectors.py --check      # 몇 권 남았는지만 본다(AI 호출 없음)
    python scripts/build_book_vectors.py --max 300    # 이번에는 300권까지만

글(제목 · 저자 · 갈래 · 연도 · 있으면 짧은 소개)의 해시가 같으면 건너뛰므로 몇 번 돌려도 같다.
카탈로그나 서점 목록(app/data/*.json)이 바뀐 뒤 다시 돌리면 바뀐 책만 새로 임베딩한다.
한 번에 vectors.BATCH 권씩 넣고 커밋하므로 중간에 끊겨도 다음 실행이 이어서 채운다.
/admin/ai 의 '100권 채우기' 버튼과 같은 함수(vectors.fill_books)를 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main  # noqa: F401  (모든 모델 등록)
from app.core.config import get_settings
from app.services import ai, vectors


async def run(url: str, max_items: int | None, check: bool) -> int:
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            items = vectors.catalogue_items()
            p = await vectors.book_progress(s, items)
            print(f"책 벡터 {p.done:,} / {p.total:,} ({p.pct}%)")
            if check or p.done >= p.total:
                return 0
            if not ai.enabled():
                print("AI 설정이 없어요 — AI_PROVIDER · AI_BASE_URL · AI_API_KEY · AI_EMBED_MODEL")
                return 2
            left = max_items if max_items is not None else p.total
            while left > 0:
                try:
                    p = await vectors.fill_books(s, min(vectors.BATCH, left), items)
                except ai.AIUnavailable as e:
                    print(f"임베딩을 받지 못했어요: {e} — 다시 돌리면 이어서 채워요.")
                    return 1
                if not p.added:
                    break
                left -= p.added
                print(f"  +{p.added}권 → {p.done:,} / {p.total:,} ({p.pct}%)")
            return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=None, help="DATABASE_URL (기본: 환경 변수)")
    ap.add_argument("--max", type=int, default=None, help="이번 실행에서 채울 최대 권수")
    ap.add_argument("--check", action="store_true", help="남은 수만 보여 주고 끝낸다")
    args = ap.parse_args(argv)
    return asyncio.run(run(args.url or get_settings().database_url, args.max, args.check))


if __name__ == "__main__":
    sys.exit(main())
