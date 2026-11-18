"""하루 목표 · 잔디(히트맵) · 연속 읽기 (feature "goals").

규칙 (docs: research 기능 2 + 5, 운영자가 기본값 그대로 승인)
- 읽기 날짜는 한국 시간 **새벽 4시**에 바뀐다. 밤늦게 읽은 쪽은 그 전날로 친다.
- 한 쪽은 체류시간 검증을 통과한(ReadingEvent.verified) 때에만 센다. 같은 (책, 쪽)은 한 번만,
  처음 검증된 날에 센다.
- 하루 목표는 검증된 쪽 3 / 5 / 10 / 20 중 하나(기본 5). "약 n분"은 내 쪽당 중앙값 시간
  (15~120초로 자름, 기록이 없으면 60초) × 쪽수로 낸 추정치다.
- 잔디는 하루 1쪽 이상이면 생긴다. 진하기 5단계는 아래 level() 참고.
- 연속 = 오늘(아직 안 읽었으면 어제)에서 거슬러 올라가며 이어진 읽은 날 수. 보호권으로 메운 날과
  복구한 날은 사슬을 잇기만 하고 날 수에는 더하지 않는다.
- 보호권은 최대 2개. 연속이 살아 있을 때 읽지 않은 날이 지나가면 하나가 자동으로 쓰인다.
- 무료 복구: 달력 한 달에 한 번, 딱 하루만 빠졌을 때 다음 날 목표의 2배를 읽으면 그 빠진 날을 잇는다.
- 보호권·복구는 화면을 볼 때(GET) 지난날을 한꺼번에 정산한다(lazy). 정산은 settled_through 로
  한 번만 하고, (user, day) unique 로 같은 날을 두 번 메우지 않는다.

참고: app/services/points.py 의 7일 연속 보너스는 자정(KST) 경계 그대로 둔다(적립 규칙은 바꾸지 않는다).
"""

import datetime as dt
import statistics
from dataclasses import dataclass, field

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ReadingEvent
from app.models_goals import ReadingDayMark, ReadingGoal

KST = dt.timezone(dt.timedelta(hours=9), "KST")
DAY_SHIFT = dt.timedelta(hours=4)  # 새벽 4시에 날짜가 바뀐다
ONE = dt.timedelta(days=1)

TIERS = (3, 5, 10, 20)
TIER_NAMES = {3: "가볍게", 5: "보통", 10: "열심", 20: "진심"}
DEFAULT_GOAL = 5
FREEZE_CAP = 2
FREEZE, REPAIR = "freeze", "repair"

SEC_PER_PAGE_DEFAULT = 60
SEC_PER_PAGE_MIN, SEC_PER_PAGE_MAX = 15, 120

WEEKS_PHONE, WEEKS_MAX = 17, 26
HISTORY_DAYS = WEEKS_MAX * 7 + 7  # 한 번의 쿼리로 읽어 오는 범위(잔디 최대 폭 + 이번 주)


def clock() -> dt.datetime:
    """현재 시각(UTC) — 테스트에서 바꿔 끼운다."""
    return dt.datetime.now(dt.UTC)


def _utc(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)  # SQLite는 시간대를 버린다


def reading_day(t: dt.datetime) -> dt.date:
    """이 시각이 속한 읽기 날짜 — 한국 시간 새벽 4시 경계."""
    return (_utc(t).astimezone(KST) - DAY_SHIFT).date()


def day_start(day: dt.date) -> dt.datetime:
    """읽기 날짜가 시작하는 순간(그날 04:00 KST)을 UTC 로."""
    return (dt.datetime.combine(day, dt.time.min, KST) + DAY_SHIFT).astimezone(dt.UTC)


def level(pages: int, goal: int) -> int:
    """잔디 진하기 0~4.

    0 안 읽음 · 1 한 쪽 이상 · 2 목표의 50% 이상 · 3 목표 달성 · 4 목표의 2배 이상.
    지난날도 **지금의 목표**를 기준으로 잰다(목표를 바꾸면 예전 칸의 진하기도 함께 바뀐다).
    날마다 목표를 따로 저장하지 않아 설명이 쉽고, 목표를 높인 사람에게 예전 기록이 연해 보일 뿐이다.
    """
    if pages <= 0:
        return 0
    if pages >= 2 * goal:
        return 4
    if pages >= goal:
        return 3
    if pages * 2 >= goal:
        return 2
    return 1


def seconds_per_page(dwells_ms: list[int]) -> int:
    """내 쪽당 시간(초)의 중앙값, 15~120초로 자른다. 기록이 없으면 60초."""
    if not dwells_ms:
        return SEC_PER_PAGE_DEFAULT
    sec = statistics.median(dwells_ms) / 1000
    return int(round(min(max(sec, SEC_PER_PAGE_MIN), SEC_PER_PAGE_MAX)))


def estimate_minutes(pages: int, sec_per_page: int) -> int:
    """쪽수를 분으로 어림한다(최소 1분)."""
    return max(1, round(pages * sec_per_page / 60))


def validate_goal(pages: int) -> int:
    if pages not in TIERS:
        raise ValueError(f"goal must be one of {TIERS}")
    return pages


# --- 읽은 기록 ---


@dataclass
class History:
    pages: dict[dt.date, int] = field(default_factory=dict)
    dwells_ms: list[int] = field(default_factory=list)


async def history(session: AsyncSession, user_id: int, since: dt.date) -> History:
    """since(읽기 날짜)부터 검증된 쪽을 날짜별로 센다 — 쿼리 하나, 묶음은 파이썬에서."""
    rows = await session.execute(
        select(
            ReadingEvent.book_id, ReadingEvent.chunk_index, ReadingEvent.occurred_at, ReadingEvent.dwell_ms
        )
        .where(
            ReadingEvent.user_id == user_id,
            ReadingEvent.verified.is_(True),
            ReadingEvent.occurred_at >= day_start(since),
        )
        .order_by(ReadingEvent.occurred_at, ReadingEvent.id)
    )
    out = History()
    seen: set[tuple[int, int]] = set()
    for book_id, chunk, at, dwell in rows.all():
        if (book_id, chunk) in seen:  # 같은 쪽을 두 번 검증받아도 한 번만
            continue
        seen.add((book_id, chunk))
        day = reading_day(at)
        out.pages[day] = out.pages.get(day, 0) + 1
        if dwell and dwell > 0:
            out.dwells_ms.append(dwell)
    return out


# --- 목표 행 ---


async def get_goal(session: AsyncSession, user_id: int) -> ReadingGoal:
    """내 목표 행 — 없으면 기본값(5쪽, 보호권 0)으로 만든다. 정산은 오늘 전날부터 시작한다."""
    goal = await session.get(ReadingGoal, user_id)
    if goal is not None:
        return goal
    goal = ReadingGoal(
        user_id=user_id,
        daily_pages=DEFAULT_GOAL,
        freezes=0,
        settled_through=reading_day(clock()) - ONE * 2,
        updated_at=clock(),
    )
    try:
        async with session.begin_nested():
            session.add(goal)
    except IntegrityError:  # 동시 요청이 먼저 만들었다
        goal = await session.get(ReadingGoal, user_id, populate_existing=True)
    return goal


async def set_goal(session: AsyncSession, user_id: int, pages: int) -> ReadingGoal:
    validate_goal(pages)
    goal = await get_goal(session, user_id)
    goal.daily_pages = pages
    goal.updated_at = clock()
    await session.commit()
    return goal


async def freeze_count(session: AsyncSession, user_id: int) -> int:
    """지금 가진 보호권 수 — 마일리지 상점이 살 수 있는지 볼 때 읽는다."""
    return (await get_goal(session, user_id)).freezes


async def grant_freezes(session: AsyncSession, user_id: int, n: int) -> int:
    """보호권을 n개 더한다(최대 2개까지). 새 개수를 돌려주고 커밋한다.

    마일리지 상점이 부른다. 이미 2개면 그대로 2를 돌려주므로, 상점은 호출 전 개수와 비교해
    실제로 늘었는지 확인할 수 있다. 한 문장 UPDATE 라 동시에 불러도 상한을 넘지 않는다.
    """
    await get_goal(session, user_id)
    n = max(0, int(n))
    total = ReadingGoal.freezes + n
    await session.execute(
        update(ReadingGoal)
        .where(ReadingGoal.user_id == user_id)
        .values(freezes=case((total > FREEZE_CAP, FREEZE_CAP), else_=total), updated_at=clock())
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    goal = await session.get(ReadingGoal, user_id, populate_existing=True)
    return goal.freezes


# --- 연속 · 보호권 · 복구 (순수 함수) ---


def _counts(day: dt.date, pages: dict[dt.date, int], marks: dict[dt.date, str]) -> bool:
    return pages.get(day, 0) > 0 or day in marks


def _repair_used(marks: dict[dt.date, str], on: dt.date) -> bool:
    """on(목표 2배를 읽은 날)이 속한 달에 이미 복구했는지. 복구 표시는 빠진 날(on 전날)에 남는다."""
    return any(
        k == REPAIR and (d + ONE).year == on.year and (d + ONE).month == on.month for d, k in marks.items()
    )


def _can_repair(day: dt.date, pages, marks, goal: int, today: dt.date) -> bool:
    """빠진 날 하나(day)를 다음 날 목표 2배로 메울 수 있는지 — 전날이 이어져 있어야 '딱 하루'다."""
    nxt = day + ONE
    return (
        nxt <= today
        and _counts(day - ONE, pages, marks)
        and pages.get(nxt, 0) >= 2 * goal
        and not _repair_used(marks, nxt)
    )


def plan_settlement(
    pages: dict[dt.date, int],
    marks: dict[dt.date, str],
    start: dt.date,
    today: dt.date,
    freezes: int,
    goal: int,
) -> list[tuple[dt.date, str]]:
    """start ~ 어제 사이 끝난 날들을 정산해 새로 남길 (날짜, 보호권|복구) 목록을 낸다.

    - 읽은 날이나 이미 메운 날은 건너뛴다.
    - 전날이 이어져 있지 않으면(지킬 연속이 없으면) 보호권을 쓰지 않는다.
    - 보호권이 있으면 먼저 쓴다. 없으면 다음 날 목표 2배를 읽었을 때 무료 복구(월 1회).
    - 어제는 이미 정산했더라도 오늘 2배를 읽으면 복구될 수 있어 한 번 더 본다.
    """
    marks = dict(marks)
    new: list[tuple[dt.date, str]] = []
    yesterday = today - ONE
    d = start
    while d <= yesterday:
        if not _counts(d, pages, marks) and _counts(d - ONE, pages, marks):
            if freezes > 0:
                freezes -= 1
                marks[d] = FREEZE
                new.append((d, FREEZE))
            elif _can_repair(d, pages, marks, goal, today):
                marks[d] = REPAIR
                new.append((d, REPAIR))
        d += ONE
    if start > yesterday and not _counts(yesterday, pages, marks):
        if _can_repair(yesterday, pages, marks, goal, today):
            new.append((yesterday, REPAIR))
    return new


def streak_of(pages: dict[dt.date, int], marks: dict[dt.date, str], today: dt.date, floor: dt.date) -> int:
    """오늘(안 읽었으면 어제)부터 거슬러 이어진 읽은 날 수. 메운 날은 잇기만 한다."""
    d = today if pages.get(today, 0) > 0 else today - ONE
    n = 0
    while d >= floor and _counts(d, pages, marks):
        if pages.get(d, 0) > 0:
            n += 1
        d -= ONE
    return n


# --- 정산 + 화면 데이터 ---


async def _marks(session: AsyncSession, user_id: int, since: dt.date) -> dict[dt.date, str]:
    rows = await session.execute(
        select(ReadingDayMark.day, ReadingDayMark.kind).where(
            ReadingDayMark.user_id == user_id, ReadingDayMark.day >= since
        )
    )
    return {d: k for d, k in rows.all()}


async def settle(
    session: AsyncSession,
    goal: ReadingGoal,
    pages: dict[dt.date, int],
    marks: dict[dt.date, str],
    today: dt.date,
) -> dict[dt.date, str]:
    """지난날 정산을 DB 에 남긴다. 같은 날은 unique 제약이 한 번만 받아 준다."""
    floor = today - dt.timedelta(days=HISTORY_DAYS)
    start = max((goal.settled_through or today - ONE * 2) + ONE, floor + ONE)
    plan = plan_settlement(pages, marks, start, today, goal.freezes, goal.daily_pages)
    marks = dict(marks)
    for day, kind in plan:
        try:
            async with session.begin_nested():
                session.add(ReadingDayMark(user_id=goal.user_id, day=day, kind=kind, created_at=clock()))
        except IntegrityError:
            continue  # 다른 요청이 이미 정산했다
        if kind == FREEZE:
            res = await session.execute(
                update(ReadingGoal)
                .where(ReadingGoal.user_id == goal.user_id, ReadingGoal.freezes > 0)
                .values(freezes=ReadingGoal.freezes - 1)
                .execution_options(synchronize_session=False)
            )
            if res.rowcount == 0:  # 그 사이 보호권이 없어졌다 — 표시도 되돌린다
                await session.execute(
                    ReadingDayMark.__table__.delete().where(
                        ReadingDayMark.user_id == goal.user_id, ReadingDayMark.day == day
                    )
                )
                continue
        marks[day] = kind
    goal.settled_through = max(goal.settled_through or floor, today - ONE)
    await session.commit()
    await session.refresh(goal)
    return marks


def _label(day: dt.date, pages: int, kind: str, today: dt.date) -> str:
    head = f"{day.month}월 {day.day}일"
    if kind == FREEZE:
        return f"{head} · 보호권으로 이어졌어요"
    if kind == REPAIR:
        return f"{head} · 다음 날 많이 읽어서 이어졌어요"
    if pages:
        return f"{head} · {pages}쪽"
    return f"{head} · 아직 안 읽었어요" if day == today else f"{head} · 쉬었어요"


async def status(session: AsyncSession, user_id: int, weeks: int = WEEKS_PHONE) -> dict:
    """/me 의 목표 칸과 홈 칩이 쓰는 데이터. 지난날 정산도 여기서 한다."""
    weeks = max(1, min(int(weeks), WEEKS_MAX))
    today = reading_day(clock())
    floor = today - dt.timedelta(days=HISTORY_DAYS)
    goal = await get_goal(session, user_id)
    hist = await history(session, user_id, floor)
    marks = await settle(session, goal, hist.pages, await _marks(session, user_id, floor), today)
    target = goal.daily_pages
    sec = seconds_per_page(hist.dwells_ms)
    done = hist.pages.get(today, 0)

    # 잔디: 주가 열, 월~일이 행. 맨 오른쪽 열이 이번 주다
    monday = today - dt.timedelta(days=today.weekday())
    start = monday - dt.timedelta(weeks=weeks - 1)
    cells = []
    d = start
    while d <= today:
        p = hist.pages.get(d, 0)
        kind = "read" if p else marks.get(d, "none")
        cells.append(
            {
                "date": d.isoformat(),
                "pages": p,
                "level": level(p, target),
                "kind": kind,
                "label": _label(d, p, kind, today),
            }
        )
        d += ONE

    yesterday = today - ONE
    rested = not _counts(yesterday, hist.pages, marks)
    repair_open = (
        rested
        and done < 2 * target
        and _counts(yesterday - ONE, hist.pages, marks)
        and not _repair_used(marks, today)
    )
    return {
        "today": {"date": today.isoformat(), "pages": done, "level": level(done, target)},
        "goal": {
            "pages": target,
            "tiers": [
                {"pages": t, "name": TIER_NAMES[t], "minutes": estimate_minutes(t, sec)} for t in TIERS
            ],
            "minutes": estimate_minutes(target, sec),
            "seconds_per_page": sec,
            "estimated": True,
        },
        "week": {"read_days": sum(hist.pages.get(monday + ONE * i, 0) > 0 for i in range(7)), "total": 7},
        "streak": {
            "days": streak_of(hist.pages, marks, today, floor),
            "freezes": goal.freezes,
            "freeze_cap": FREEZE_CAP,
            "rested_yesterday": rested and done == 0,
            "repair": {"open": repair_open, "pages": 2 * target},
        },
        "heatmap": {"weeks": weeks, "start": start.isoformat(), "days": cells},
    }
