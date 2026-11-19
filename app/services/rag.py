"""책에게 물어보기 — 내가 읽은 쪽까지만 근거로 답한다(스포일러 가드).

1. 검증받은 마지막 쪽(social.verified_of)을 구한다. 반응 · 방 채팅과 같은 규칙이다.
2. 그 쪽까지의 본문 조각 벡터를 (없으면) 만든다 — 한 번에 vectors.CHUNK_BUDGET 조각까지.
3. 질문을 임베딩해 이 책의 '읽은 쪽까지' 조각에서 가장 가까운 TOP_K 개를 고른다.
   뒤쪽 조각은 SQL 에서 걸러지고, 프롬프트를 만들기 전에 한 번 더 걸러진다.
4. 가까운 조각이 없으면 AI 를 부르지 않고 "아직 읽은 부분에는 그 내용이 없어요".
5. 답에 붙은 쪽 표시 (12쪽) 는 보낸 쪽만 남긴다 — 모델이 지어낸 쪽 번호는 지운다.
최근 5개 문답을 사람 · 책마다 남긴다(ai_questions).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings
from app.models import Book, User
from app.models_ai import AskLog
from app.services import ai, social, vectors

TOP_K = 6
KEEP = 5  # 사람 · 책마다 남기는 문답 수
MAX_QUESTION = 300
DAY_S = 86_400

# 시스템 프롬프트 — 바꿀 때는 여기만 보면 된다
ASK_SYSTEM = """당신은 독자가 읽고 있는 책에 대한 질문에 답하는 독서 도우미예요.
규칙:
- 사용자가 준 [N쪽] 조각만 근거로 답해요.
- 조각에 없는 내용은 짐작하지 말고 "아직 읽은 부분에는 그 내용이 없어요"라고만 답해요.
- 독자는 주어진 쪽까지만 읽었어요.
- 이 책의 뒤 내용(사건, 결말, 반전, 인물의 운명)을 알고 있더라도 절대 말하거나 암시하지 않아요.
- 근거가 된 쪽을 문장 끝에 (12쪽)처럼 붙여요. 주어진 쪽 번호만 써요.
- 쉬운 해요체로 3~5문장, 짧게 답해요. 목록이나 머리말 없이 문장으로만 써요."""

NOT_FOUND = "아직 읽은 부분에는 그 내용이 없어요."
NOT_READ = "아직 다 읽은 쪽이 없어요. 몇 쪽 읽고 나서 물어봐 주세요."
CAPPED = "오늘은 여기까지 물어볼 수 있어요. 내일 다시 물어봐 주세요."
DOWN = "지금은 AI가 답하기 어려워요. 잠시 뒤에 다시 물어봐 주세요."
UPLOAD_NOTICE = "내 파일 내용 일부가 AI 서비스로 보내져요."


def min_score() -> float:
    """이보다 먼 조각은 '관련 없음'.

    진짜 임베딩(코사인 0.2 아래는 대개 딴 이야기)과 가짜(3-gram 해시)는 거리 감각이 다르다.
    """
    return 0.08 if get_settings().ai_provider == "fake" else 0.2


@dataclass
class Answer:
    kind: str  # ok | not_found | unread | capped | down
    text: str
    pages: list[int] = field(default_factory=list)  # 근거 쪽(1부터)
    building: bool = False  # 읽은 쪽 가운데 아직 살펴보지 못한 쪽이 남았다(다음 질문 때 이어서)
    log_id: int | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "answer": self.text,
            "pages": self.pages,
            "building": self.building,
            "id": self.log_id,
        }


# --- 하루 상한(다른 AI 기능과 같은 열쇠를 쓴다) -------------------------------------------------


def cap_key(user_id: int) -> str:
    return f"ai-day:{user_id}"


async def spend_daily(session: AsyncSession, user_id: int) -> bool:
    """오늘의 AI 답변 한 번을 쓴다. 상한(AI_DAILY_REQUESTS)을 넘었으면 False."""
    try:
        await ratelimit.check(session, cap_key(user_id), get_settings().ai_daily_requests, DAY_S, CAPPED)
    except ratelimit.RateLimited:
        return False
    return True


# --- 프롬프트와 답 다듬기 -------------------------------------------------------------------


def build_prompt(question: str, pieces: list[vectors.Piece], verified: int) -> str:
    """조각을 쪽 순서로 먼저, 질문을 마지막에. 조각 글자 수는 vectors.PART_CHARS 안팎이다."""
    body = "\n\n".join(
        f"[{p.page}쪽] {p.text.strip()}" for p in sorted(pieces, key=lambda p: (p.chunk_index, p.part))
    )
    scope = f"(독자는 1~{verified + 1}쪽까지 읽었어요. 위 조각은 그 안에서 찾은 거예요.)"
    return f"{body}\n\n{scope}\n질문: {question}"


_PAREN = re.compile(r"\s*\(([^()]*?\d+\s*쪽[^()]*)\)")
_BARE = re.compile(r"(\d+)\s*쪽")


_SENT = re.compile(r"[^.!?。\n]*(?:[.!?。]+|\n|$)")


def clean_cites(text: str, allowed: set[int]) -> tuple[str, list[int]]:
    """보낸 쪽만 근거로 남긴다. 보내지 않은 쪽 번호가 나온 문장은 통째로 지운다.

    번호만 '어느 쪽'으로 바꾸면 그 문장(뒤 내용일 수 있는 스포일러)이 그대로 남는다 —
    모델이 지어낸 쪽은 읽지 않은 곳일 수 있으니, 그 쪽을 말한 문장째로 버린다.
    """
    cited: list[int] = []
    kept: list[str] = []
    for sent in _SENT.findall(text):
        if not sent.strip():
            continue
        nums = [int(n) for n in _BARE.findall(sent)]
        if any(n not in allowed for n in nums):
            continue  # 지어낸 쪽이 하나라도 있으면 문장째 버린다
        cited.extend(nums)
        kept.append(sent)
    out = "".join(kept)
    out = _PAREN.sub(lambda m: f" ({m.group(1).strip()})", out)  # 괄호 앞 공백 정리
    return re.sub(r"[ \t]{2,}", " ", out).strip(), sorted(set(cited))


# --- 묻기 ---------------------------------------------------------------------------------


async def ask(session: AsyncSession, user: User, book: Book, question: str) -> Answer:
    """부르는 쪽이 이미 확인했다: 로그인, can_read(book, user) — 올린 파일은 올린 사람만."""
    question = " ".join((question or "").split())[:MAX_QUESTION]
    verified = await social.verified_of(session, user.id, book.id)
    if verified < 0:
        return Answer("unread", NOT_READ)
    verified = min(verified, max(book.total_chunks - 1, 0))
    if not await spend_daily(session, user.id):
        return Answer("capped", CAPPED)
    try:
        built = await vectors.ensure_chunks(session, book, verified)
        query = (await ai.embed([question]))[0]
        pieces = await vectors.nearest_pieces(session, book.id, verified, query, TOP_K)
        pieces = [p for p in pieces if p.chunk_index <= verified and p.score >= min_score()]
        if not pieces:
            answer = Answer("not_found", NOT_FOUND, building=built.pending)
        else:
            res = await ai.chat(
                [{"role": "user", "content": build_prompt(question, pieces, verified)}],
                system=ASK_SYSTEM,
                max_tokens=400,
            )
            allowed = {p.page for p in pieces}
            text, cited = clean_cites(res.text, allowed)
            if not text or NOT_FOUND.rstrip(".") in text:
                answer = Answer("not_found", NOT_FOUND, building=built.pending)
            else:
                pages = cited or sorted(allowed)[:3]
                answer = Answer("ok", text, pages, building=built.pending)
    except ai.AIUnavailable:
        await session.rollback()
        return Answer("down", DOWN)
    answer.log_id = await _remember(session, user.id, book.id, question, answer)
    return answer


async def _remember(session: AsyncSession, user_id: int, book_id: int, question: str, a: Answer) -> int:
    row = AskLog(user_id=user_id, book_id=book_id, question=question, answer=a.text, pages=a.pages)
    session.add(row)
    await session.flush()
    old = (
        await session.scalars(
            select(AskLog.id)
            .where(AskLog.user_id == user_id, AskLog.book_id == book_id)
            .order_by(AskLog.id.desc())
            .offset(KEEP)
        )
    ).all()
    if old:
        await session.execute(delete(AskLog).where(AskLog.id.in_(old)))
    await session.commit()
    return row.id


async def recent(session: AsyncSession, user_id: int, book_id: int) -> list[AskLog]:
    """최근 문답(새것 먼저)."""
    return list(
        await session.scalars(
            select(AskLog)
            .where(AskLog.user_id == user_id, AskLog.book_id == book_id)
            .order_by(AskLog.id.desc())
            .limit(KEEP)
        )
    )


async def forget(session: AsyncSession, user_id: int, book_id: int) -> int:
    res = await session.execute(delete(AskLog).where(AskLog.user_id == user_id, AskLog.book_id == book_id))
    await session.commit()
    return res.rowcount or 0


def as_item(row: AskLog) -> dict:
    return {
        "id": row.id,
        "question": row.question,
        "answer": row.answer,
        "pages": list(row.pages or []),
        "found": bool(row.pages),
    }
