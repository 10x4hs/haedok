"""포인트 원장(points_log) — 적립 규칙과 집계.

원장은 append-only 라서 잔액은 늘 SUM(amount)로 센다. 같은 일로 두 번 주지 않도록
(user_id, source, ref_id) 를 넣기 전에 먼저 찾아본다(스키마에 unique 제약이 없어 코드로 지킨다).
날짜 경계(주간 순위·하루 상한·연속 일수)는 모두 한국 시간(KST) 기준이고, 저장은 UTC 다.
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Book, LibraryEntry, PointsLog, Room, RoomMember, User

KST = dt.timezone(dt.timedelta(hours=9), "KST")

# source 값은 30자 제한 — 짧게 쓴다
CHUNK = "chunk"  # 체류시간 검증을 통과한 쪽 1장
REACTION = "reaction"  # 문단에 처음 남긴 반응
RACE_FINISH = "race_finish"  # 대결 책 완주
OVERTAKE = "overtake"  # 대결에서 친구 추월
QUOTE = "quote"  # 문장 공유
STREAK = "streak"  # 7일 연속 읽기

RULES = {CHUNK: 1, REACTION: 1, RACE_FINISH: 20, OVERTAKE: 5, QUOTE: 3, STREAK: 30}
LABELS = {
    CHUNK: "쪽 읽기",
    REACTION: "반응 남기기",
    RACE_FINISH: "대결 완주",
    OVERTAKE: "친구 추월",
    QUOTE: "문장 공유",
    STREAK: "7일 연속 읽기",
}
REACTION_DAILY_CAP = 20
STREAK_DAYS = 7
STREAK_MAX = 400  # 연속 일수 계산 상한(하루에 쿼리 하나)
LEADERBOARD_SIZE = 50
LEDGER_SIZE = 20


def clock() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _utc(t: dt.datetime) -> dt.datetime:
    """SQLite는 시간대를 버리므로 읽어 온 값은 UTC 로 본다."""
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def day_start(now: dt.datetime, days_ago: int = 0) -> dt.datetime:
    """KST 자정(days_ago 일 전)을 UTC 로."""
    local = now.astimezone(KST).date() - dt.timedelta(days=days_ago)
    return dt.datetime.combine(local, dt.time.min, KST).astimezone(dt.UTC)


def week_start(now: dt.datetime) -> dt.datetime:
    """이번 주 월요일 00:00 KST 를 UTC 로 — 주간 순위의 시작점."""
    return day_start(now, now.astimezone(KST).weekday())


async def award(
    session: AsyncSession, user_id: int, source: str, amount: int | None, ref_id: str
) -> PointsLog | None:
    """한 번만 적립한다. 이미 같은 (user, source, ref) 가 있으면 None.

    unique 제약이 없어 select-before-insert 로 지킨다. 서버리스에서 같은 요청이 동시에
    두 번 와도 insert 는 commit 직전에 한 번 더 확인하므로 겹칠 틈이 매우 좁다.
    """
    if await _exists(session, user_id, source, ref_id):
        return None
    row = PointsLog(
        user_id=user_id, source=source, amount=RULES[source] if amount is None else amount, ref_id=ref_id
    )
    row.created_at = clock()
    session.add(row)
    await session.flush()
    if await _count(session, user_id, source, ref_id) > 1:  # 동시 요청이 먼저 넣었다
        await session.delete(row)
        await session.commit()
        return None
    await session.commit()
    return row


async def _exists(session: AsyncSession, user_id: int, source: str, ref_id: str) -> bool:
    return await _count(session, user_id, source, ref_id) > 0


async def _count(session: AsyncSession, user_id: int, source: str, ref_id: str) -> int:
    return (
        await session.scalar(
            select(func.count(PointsLog.id)).where(
                PointsLog.user_id == user_id, PointsLog.source == source, PointsLog.ref_id == ref_id
            )
        )
        or 0
    )


# --- 적립 지점 ---


async def award_chunk(
    session: AsyncSession, user_id: int, book_id: int, chunk_index: int
) -> PointsLog | None:
    """서버 체류시간 판정을 통과한 쪽 — 한 책의 같은 쪽은 한 번만."""
    return await award(session, user_id, CHUNK, None, f"{book_id}:{chunk_index}")


async def award_progress(
    session: AsyncSession, user_id: int, book: Book, before: int, after: int
) -> list[PointsLog]:
    """검증 위치가 before → after 로 올라갔을 때 한 번에: 쪽 + 완주 + 추월 + 연속 읽기."""
    out: list[PointsLog] = []
    if after <= before:
        return out
    row = await award_chunk(session, user_id, book.id, after)
    if row is None:
        return out
    out.append(row)
    rooms = list(
        await session.scalars(
            select(Room)
            .join(RoomMember, RoomMember.room_id == Room.id)
            .where(RoomMember.user_id == user_id, Room.book_id == book.id)
        )
    )
    if rooms and after >= book.total_chunks - 1:
        for room in rooms:
            if r := await award(session, user_id, RACE_FINISH, None, str(room.id)):
                out.append(r)
    for room in rooms:
        # 아까는 뒤(또는 같은 자리)였고 지금은 앞선 사람 — 같은 방 안에서 사람마다 한 번
        rows = await session.execute(
            select(RoomMember.user_id, LibraryEntry.verified_chunk)
            .outerjoin(
                LibraryEntry,
                (LibraryEntry.user_id == RoomMember.user_id) & (LibraryEntry.book_id == book.id),
            )
            .where(RoomMember.room_id == room.id, RoomMember.user_id != user_id)
        )
        for other_id, v in rows.all():
            v = -1 if v is None else v
            if before <= v < after:
                if r := await award(session, user_id, OVERTAKE, None, f"{room.id}:{other_id}"):
                    out.append(r)
    if r := await check_streak(session, user_id):
        out.append(r)
    return out


async def award_reaction(
    session: AsyncSession, user_id: int, book_id: int, chunk_index: int, anchor: int
) -> PointsLog | None:
    """문단에 처음 남긴 반응만, 하루 20번까지."""
    now = clock()
    today = await session.scalar(
        select(func.count(PointsLog.id)).where(
            PointsLog.user_id == user_id, PointsLog.source == REACTION, PointsLog.created_at >= day_start(now)
        )
    )
    if (today or 0) >= REACTION_DAILY_CAP:
        return None
    return await award(session, user_id, REACTION, None, f"{book_id}:{chunk_index}:{anchor}")


async def award_quote(session: AsyncSession, user_id: int, quote_id: int) -> PointsLog | None:
    return await award(session, user_id, QUOTE, None, str(quote_id))


async def _active_on(session: AsyncSession, user_id: int, now: dt.datetime, days_ago: int) -> bool:
    """그날(KST) 검증된 쪽이 하나라도 있나."""
    start, end = day_start(now, days_ago), day_start(now, days_ago - 1)
    return (
        await session.scalar(
            select(PointsLog.id)
            .where(
                PointsLog.user_id == user_id,
                PointsLog.source == CHUNK,
                PointsLog.created_at >= start,
                PointsLog.created_at < end,
            )
            .limit(1)
        )
    ) is not None


async def streak_days(session: AsyncSession, user_id: int, now: dt.datetime | None = None) -> int:
    """오늘(아직 안 읽었으면 어제)부터 거슬러 올라가며 하루도 빠지지 않은 날 수."""
    now = now or clock()
    offset = 0 if await _active_on(session, user_id, now, 0) else 1
    n = 0
    while n < STREAK_MAX and await _active_on(session, user_id, now, offset + n):
        n += 1
    return n


async def check_streak(session: AsyncSession, user_id: int) -> PointsLog | None:
    """오늘로 7일(14일, 21일…)이 꽉 찼으면 보너스 — 날짜를 ref 로 써서 하루 한 번만."""
    now = clock()
    if await _count_on(session, user_id, now) > 1:
        return None  # 오늘 첫 쪽에서만 계산한다(쪽마다 7번 묻지 않도록)
    days = 0
    while days < STREAK_MAX and await _active_on(session, user_id, now, days):
        days += 1
    if days < STREAK_DAYS or days % STREAK_DAYS:
        return None
    return await award(session, user_id, STREAK, None, now.astimezone(KST).date().isoformat())


async def _count_on(session: AsyncSession, user_id: int, now: dt.datetime) -> int:
    return (
        await session.scalar(
            select(func.count(PointsLog.id)).where(
                PointsLog.user_id == user_id,
                PointsLog.source == CHUNK,
                PointsLog.created_at >= day_start(now),
            )
        )
        or 0
    )


# --- 집계 ---


@dataclass
class Summary:
    total: int
    week: int
    streak: int


async def summary(session: AsyncSession, user_id: int) -> Summary:
    now = clock()
    total = await session.scalar(select(func.sum(PointsLog.amount)).where(PointsLog.user_id == user_id))
    week = await session.scalar(
        select(func.sum(PointsLog.amount)).where(
            PointsLog.user_id == user_id, PointsLog.created_at >= week_start(now)
        )
    )
    return Summary(total=total or 0, week=week or 0, streak=await streak_days(session, user_id, now))


async def ledger(session: AsyncSession, user_id: int, n: int = LEDGER_SIZE) -> list[dict]:
    rows = await session.scalars(
        select(PointsLog).where(PointsLog.user_id == user_id).order_by(PointsLog.id.desc()).limit(n)
    )
    return [
        {
            "id": r.id,
            "source": r.source,
            "label": LABELS.get(r.source, r.source),
            "amount": r.amount,
            "ref_id": r.ref_id,
            "created_at": _utc(r.created_at).isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@dataclass
class Entry:
    rank: int
    user: User
    points: int
    is_me: bool = False
    is_friend: bool = False


@dataclass
class Board:
    scope: str  # "week" | "all"
    entries: list[Entry]
    me: Entry | None  # 50위 밖이어도 내 순위
    since: dt.datetime | None


async def friends_of(session: AsyncSession, user_id: int) -> set[int]:
    """나와 같은 대결방에 있는 사람들(본인 제외)."""
    mine = select(RoomMember.room_id).where(RoomMember.user_id == user_id)
    rows = await session.scalars(
        select(RoomMember.user_id)
        .where(RoomMember.room_id.in_(mine), RoomMember.user_id != user_id)
        .distinct()
    )
    return set(rows)


async def leaderboard(
    session: AsyncSession, me_id: int, scope: str = "week", limit: int = LEADERBOARD_SIZE
) -> Board:
    """포인트 합계 순위. 동점은 같은 등수, 그다음 등수는 건너뛴다(1, 1, 3)."""
    scope = "all" if scope == "all" else "week"
    since = week_start(clock()) if scope == "week" else None
    totals = select(PointsLog.user_id, func.sum(PointsLog.amount).label("pts"))
    if since is not None:
        totals = totals.where(PointsLog.created_at >= since)
    totals = totals.group_by(PointsLog.user_id).subquery()
    rows = await session.execute(
        select(User, totals.c.pts)
        .join(totals, totals.c.user_id == User.id)
        .order_by(totals.c.pts.desc(), User.id)
        .limit(limit)
    )
    friends = await friends_of(session, me_id)
    entries: list[Entry] = []
    rank, last = 0, None
    for i, (u, pts) in enumerate(rows.all(), start=1):
        if pts != last:
            rank, last = i, pts
        entries.append(Entry(rank, u, int(pts), u.id == me_id, u.id in friends))
    me = next((e for e in entries if e.is_me), None)
    if me is None:
        mine = await session.scalar(select(totals.c.pts).where(totals.c.user_id == me_id))
        if mine:
            above = await session.scalar(select(func.count()).select_from(totals).where(totals.c.pts > mine))
            user = await session.get(User, me_id)
            me = Entry((above or 0) + 1, user, int(mine), True, False)
    return Board(scope=scope, entries=entries, me=me, since=since)
