"""로컬 미리보기용: '책에게 물어보기' · '나를 위한 다음 책' 데이터를 심는다(AI_PROVIDER=fake 로).

    python scripts/readme_shots.py --seed "$DATABASE_URL"            # 계정 먼저
    AI_PROVIDER=fake python scripts/seed_ai_rag.py "$DATABASE_URL"   # 책 · 서재 · 벡터 · 문답 기록

하린에게 본문이 있는 짧은 책 한 권(6쪽, 4쪽까지 읽음)을 주고, 카탈로그 책 몇 권을 서재에 꽂고,
서점의 「데미안」에 별 다섯 게시물을 남긴다. 책 벡터는 전부 채우고 문답 두 개를 미리 남긴다.
본문은 미리보기를 위해 새로 쓴 짧은 글이다(실제 책 본문이 아니다).
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main  # noqa: F401  (모든 모델 등록)
from app import web
from app.db.base import Base
from app.models import Book, BookChunk, LibraryEntry, Shelf, User
from app.models_posts import ReadingPost
from app.services import ai, rag, vectors

PAGES = [
    "비가 추적추적 내리는 아침이었다. 인력거꾼 김 첨지는 오늘따라 손님이 잦아 들떠 있었다.\n\n"
    "첫 손님은 앞집 마나님이었고, 전찻길까지 모셔다 드리고 삼십 전을 받았다.",
    "두 번째 손님은 양복 입은 학생이었다. 남대문 정거장까지 가자는 말에 김 첨지는 잠깐 망설였다.\n\n"
    "집에는 기침을 하며 누워 있는 아내가 있었다. 아내는 아침에 설렁탕이 먹고 싶다고 했다.",
    "김 첨지는 정거장까지 달리며 아내 생각을 떨치려 애썼다.\n\n돈이 손에 쥐어질수록 마음은 무거워졌다. "
    "그는 오늘은 꼭 설렁탕을 사 가리라고 마음먹었다.",
    "돌아오는 길에 친구 치삼이를 만났다. 둘은 선술집에 들어가 막걸리를 마셨다.\n\n"
    "김 첨지는 취기가 오르자 괜히 큰소리를 치고, 또 괜히 울적해졌다.",
    "[미리보기용 뒤쪽] 해가 지고 김 첨지는 설렁탕을 들고 집으로 향했다. 집 안은 이상하리만큼 조용했다.",
    "[미리보기용 마지막 쪽] 이 쪽은 하린이 아직 읽지 않았으므로 AI 에게 보내지지 않는다.",
]
LIKED = ["메밀꽃 필 무렵", "동백꽃", "봄봄"]
READING: list[str] = []  # 본문 없는 카탈로그 책을 '읽는 중'에 두면 이어 읽기 카드가 0쪽으로 뜬다
QUESTIONS = ["김 첨지의 아내는 무엇을 먹고 싶어 했어요?", "치삼이는 누구예요?"]


async def main(url: str) -> None:
    if not ai.enabled():
        raise SystemExit("AI_PROVIDER=fake 로 돌려 주세요")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        harin = await s.scalar(select(User).where(User.email == "harin@example.com"))
        if harin is None:
            raise SystemExit("no harin@example.com — run readme_shots.py --seed first")
        book = Book(
            source="wikisource", external_id="ai-preview-운수", title="운수 좋은 날 (미리보기)",
            author="현진건", has_fulltext=True, total_chunks=len(PAGES),
        )  # fmt: skip
        s.add(book)
        await s.flush()
        for i, t in enumerate(PAGES):
            s.add(BookChunk(book_id=book.id, chunk_index=i, text=t, char_count=len(t)))
        s.add(LibraryEntry(user_id=harin.id, book_id=book.id, shelf=Shelf.READING, verified_chunk=3,
                           current_chunk=4, progress_pct=60))  # fmt: skip
        for title, shelf in [(t, Shelf.DONE) for t in LIKED] + [(t, Shelf.READING) for t in READING]:
            meta = next((b for b in web.CATALOG if b["title"] == title), None)
            if meta is None:
                continue
            b = Book(source=meta["source"], external_id=str(meta["id"]), title=meta["title"],
                     author=meta["author"])  # fmt: skip
            s.add(b)
            await s.flush()
            s.add(LibraryEntry(user_id=harin.id, book_id=b.id, shelf=shelf))
        demian = next(b for b in web.STORE if b["title"].startswith("데미안"))
        s.add(
            ReadingPost(
                author_id=harin.id, book_source="store", external_id=demian["id"], book_title=demian["title"],
                book_author=demian["author"], cover_url=demian["cover"], status="done", rating=5,
                caption="싱클레어와 함께 자란 기분", post_day="2026-11-18",
            )
        )  # fmt: skip
        await s.commit()
        while (await vectors.fill_books(s)).added:
            pass
        for q in QUESTIONS:
            a = await rag.ask(s, harin, book, q)
            print(q, "→", a.kind, a.pages)
        print("book id", book.id)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
