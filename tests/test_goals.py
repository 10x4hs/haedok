"""하루 목표 · 잔디 · 연속 읽기(feature goals) — 새벽 4시 경계, 진하기, 보호권, 복구, API."""

import datetime as dt
import re

import pytest

from app.core.config import get_settings
from app.models import Book, BookChunk, ReadingEvent, User
from app.models_goals import ReadingDayMark, ReadingGoal
from app.services import goals
from tests.conftest import csrf_of, read_chunk, signup
from tests.test_api import upstream

KST = goals.KST
D = dt.date


def kst(y, m, d, hh=12, mm=0) -> dt.datetime:
    return dt.datetime(y, m, d, hh, mm, tzinfo=KST).astimezone(dt.UTC)


@pytest.fixture
def gclock(monkeypatch):
    """목표 서비스의 시계: gclock.set(datetime)."""
    state = {"now": kst(2026, 11, 18, 12)}
    monkeypatch.setattr(goals, "clock", lambda: state["now"])

    class C:
        @staticmethod
        def set(t: dt.datetime) -> None:
            state["now"] = t

    return C()


async def _user(s, nickname="독자") -> User:
    u = User(nickname=nickname, email=f"{nickname}@example.com", password_hash=None)
    s.add(u)
    await s.commit()
    return u


async def _book(s, n=200) -> Book:
    b = Book(source="wikisource", external_id=f"g{n}", title="책", total_chunks=n, has_fulltext=True)
    s.add(b)
    await s.commit()
    return b


class Reader:
    """검증된 쪽을 DB 에 바로 남긴다 — 쪽 번호는 자동으로 하나씩 늘어난다."""

    def __init__(self, s, user: User, book: Book):
        self.s, self.user, self.book, self.next = s, user, book, 0

    async def read(self, at: dt.datetime, pages: int = 1, dwell_ms: int = 30_000) -> None:
        for _ in range(pages):
            self.s.add(
                ReadingEvent(
                    user_id=self.user.id,
                    book_id=self.book.id,
                    chunk_index=self.next,
                    dwell_ms=dwell_ms,
                    verified=True,
                    occurred_at=at,
                )
            )
            self.next += 1
        await self.s.commit()


async def _reader(s) -> Reader:
    return Reader(s, await _user(s), await _book(s))


# --- 순수 규칙 ---


def test_reading_day_changes_at_4am_kst():
    assert goals.reading_day(kst(2026, 11, 18, 3, 59)) == D(2026, 11, 17)
    assert goals.reading_day(kst(2026, 11, 18, 4, 0)) == D(2026, 11, 18)
    assert goals.reading_day(kst(2026, 11, 18, 0, 30)) == D(2026, 11, 17)  # 자정 넘어도 아직 어제
    assert goals.day_start(D(2026, 11, 18)) == dt.datetime(2026, 11, 17, 19, 0, tzinfo=dt.UTC)


def test_levels_against_current_goal():
    g = 10
    assert [goals.level(p, g) for p in (0, 1, 4, 5, 9, 10, 19, 20, 50)] == [0, 1, 1, 2, 2, 3, 3, 4, 4]
    assert [goals.level(p, 3) for p in (0, 1, 2, 3, 6)] == [0, 1, 2, 3, 4]  # 2*2 >= 3 → 50% 이상


def test_minutes_estimate_uses_median_clamped():
    assert goals.seconds_per_page([]) == 60
    assert goals.seconds_per_page([30_000, 40_000, 500_000]) == 40  # 중앙값
    assert goals.seconds_per_page([1_000, 2_000]) == 15  # 아래로 자름
    assert goals.seconds_per_page([900_000]) == 120  # 위로 자름
    assert goals.estimate_minutes(5, 60) == 5
    assert goals.estimate_minutes(3, 15) == 1


def test_goal_validation_only_accepts_tiers():
    for ok in (3, 5, 10, 20):
        assert goals.validate_goal(ok) == ok
    for bad in (0, 1, 4, 6, 15, 21, 100, -5):
        with pytest.raises(ValueError):
            goals.validate_goal(bad)


def _days(*ds) -> dict:
    return {D(2026, 11, d): p for d, p in ds}


def test_streak_counts_read_days_and_marks_only_bridge():
    pages = _days((14, 3), (15, 1), (17, 2))
    marks = {D(2026, 11, 16): goals.FREEZE}
    floor = D(2026, 1, 1)
    assert goals.streak_of(pages, marks, D(2026, 11, 17), floor) == 3  # 16일은 잇기만
    assert goals.streak_of(pages, marks, D(2026, 11, 18), floor) == 3  # 오늘 아직이면 어제부터
    assert goals.streak_of(pages, marks, D(2026, 11, 19), floor) == 0  # 어제도 쉬었다
    assert goals.streak_of(pages, {}, D(2026, 11, 17), floor) == 1


def test_settlement_uses_freezes_up_to_inventory():
    pages = _days((10, 5))
    plan = goals.plan_settlement(pages, {}, D(2026, 11, 11), D(2026, 11, 15), freezes=2, goal=5)
    assert plan == [(D(2026, 11, 11), goals.FREEZE), (D(2026, 11, 12), goals.FREEZE)]  # 13일에 끊김
    # 지킬 연속이 없으면 보호권을 쓰지 않는다
    assert goals.plan_settlement({}, {}, D(2026, 11, 11), D(2026, 11, 15), freezes=2, goal=5) == []


def test_repair_needs_exactly_one_missed_day_and_double_goal():
    today = D(2026, 11, 18)
    one_gap = _days((16, 5), (18, 10))  # 17일 하루만 빠지고 18일에 2배
    assert goals.plan_settlement(one_gap, {}, D(2026, 11, 17), today, 0, 5) == [
        (D(2026, 11, 17), goals.REPAIR)
    ]
    short = _days((16, 5), (18, 9))  # 2배에 못 미침
    assert goals.plan_settlement(short, {}, D(2026, 11, 17), today, 0, 5) == []
    two_gap = _days((15, 5), (18, 10))  # 16·17 이틀 빠짐
    assert goals.plan_settlement(two_gap, {}, D(2026, 11, 16), today, 0, 5) == []
    # 어제를 이미 정산했어도(시작이 오늘) 오늘 2배를 읽으면 복구된다
    assert goals.plan_settlement(one_gap, {}, today, today, 0, 5) == [(D(2026, 11, 17), goals.REPAIR)]


def test_repair_once_per_calendar_month():
    used = {D(2026, 11, 5): goals.REPAIR}
    pages = _days((4, 5), (6, 10), (16, 5), (18, 10))
    assert goals.plan_settlement(pages, used, D(2026, 11, 17), D(2026, 11, 18), 0, 5) == []
    # 다음 달에는 다시 된다
    dec = {D(2026, 11, 30): 5, D(2026, 12, 2): 10}
    assert goals.plan_settlement(dec, used, D(2026, 12, 1), D(2026, 12, 2), 0, 5) == [
        (D(2026, 12, 1), goals.REPAIR)
    ]


# --- DB: 날짜 경계 · 중복 · 상태 ---


async def test_verified_pages_bucket_at_4am_boundary(session_factory, gclock):
    async with session_factory() as s:
        r = await _reader(s)
        await r.read(kst(2026, 11, 18, 3, 59), 2)  # 17일로 센다
        await r.read(kst(2026, 11, 18, 4, 0), 3)  # 18일
        gclock.set(kst(2026, 11, 18, 22))
        st = await goals.status(s, r.user.id)
        by = {c["date"]: c["pages"] for c in st["heatmap"]["days"]}
        assert by["2026-11-17"] == 2 and by["2026-11-18"] == 3
        assert st["today"]["pages"] == 3


async def test_same_page_verified_twice_counts_once(session_factory, gclock):
    async with session_factory() as s:
        r = await _reader(s)
        await r.read(kst(2026, 11, 18, 9), 1)
        r.next = 0  # 같은 쪽을 또 검증받음(동시 요청 등)
        await r.read(kst(2026, 11, 18, 10), 1)
        r.next = 0
        await r.read(kst(2026, 11, 18, 11), 1)
        s.add(  # 검증 못 받은 기록은 세지 않는다
            ReadingEvent(
                user_id=r.user.id,
                book_id=r.book.id,
                chunk_index=5,
                dwell_ms=0,
                verified=False,
                occurred_at=kst(2026, 11, 18, 11),
            )
        )
        await s.commit()
        st = await goals.status(s, r.user.id)
        assert st["today"]["pages"] == 1


async def test_status_levels_week_and_minutes(session_factory, gclock):
    async with session_factory() as s:
        r = await _reader(s)
        # 목표 5쪽: 월 1쪽(1) · 화 3쪽(2) · 수 5쪽(3) · 목(오늘) 10쪽(4)
        for day, n in ((16, 1), (17, 3), (18, 5), (19, 10)):
            await r.read(kst(2026, 11, day, 9), n, dwell_ms=45_000)
        gclock.set(kst(2026, 11, 19, 20))
        st = await goals.status(s, r.user.id)
        lv = {c["date"]: c["level"] for c in st["heatmap"]["days"]}
        assert [lv[f"2026-11-{d}"] for d in (15, 16, 17, 18, 19)] == [0, 1, 2, 3, 4]
        assert st["week"] == {"read_days": 4, "total": 7}
        assert st["goal"]["pages"] == 5 and st["goal"]["seconds_per_page"] == 45
        assert st["goal"]["minutes"] == 4  # 5쪽 × 45초 ≈ 4분
        assert st["streak"]["days"] == 4
        cells = st["heatmap"]["days"]
        assert len(cells) == 16 * 7 + 4  # 17주, 이번 주는 목요일까지
        assert D.fromisoformat(cells[0]["date"]).weekday() == 0  # 열은 월요일에 시작
        label = {c["date"]: c["label"] for c in cells}
        assert label["2026-11-18"] == "11월 18일 · 5쪽"
        assert label["2026-11-15"] == "11월 15일 · 쉬었어요"


async def test_streak_across_days_through_status(session_factory, gclock):
    async with session_factory() as s:
        r = await _reader(s)
        for day in (14, 15, 16):
            await r.read(kst(2026, 11, day, 23, 30), 1)  # 밤 11시 반
        await r.read(kst(2026, 11, 18, 1, 0), 1)  # 18일 새벽 1시 → 17일로 센다
        gclock.set(kst(2026, 11, 18, 12))  # 오늘(18일)은 아직 안 읽음
        st = await goals.status(s, r.user.id)
        assert st["streak"]["days"] == 4 and not st["streak"]["rested_yesterday"]
        gclock.set(kst(2026, 11, 19, 12))  # 18일을 쉬었다
        st = await goals.status(s, r.user.id)
        assert st["streak"]["days"] == 0 and st["streak"]["rested_yesterday"]


async def test_freeze_auto_used_and_capped_at_two(session_factory, gclock):
    async with session_factory() as s:
        r = await _reader(s)
        gclock.set(kst(2026, 11, 10, 12))
        assert await goals.grant_freezes(s, r.user.id, 1) == 1
        assert await goals.grant_freezes(s, r.user.id, 5) == 2  # 상한 2개
        assert await goals.grant_freezes(s, r.user.id, 1) == 2
        assert await goals.grant_freezes(s, r.user.id, 0) == 2
        await r.read(kst(2026, 11, 10, 12), 5)
        await r.read(kst(2026, 11, 11, 12), 5)
        # 12·13 쉬고 14일에 읽음 — 보호권 2개가 자동으로 쓰인다
        await r.read(kst(2026, 11, 14, 12), 5)
        gclock.set(kst(2026, 11, 14, 20))
        st = await goals.status(s, r.user.id)
        kinds = {c["date"]: c["kind"] for c in st["heatmap"]["days"]}
        assert kinds["2026-11-12"] == "freeze" and kinds["2026-11-13"] == "freeze"
        assert st["streak"]["freezes"] == 0 and st["streak"]["days"] == 3
        assert "보호권" in {c["date"]: c["label"] for c in st["heatmap"]["days"]}["2026-11-12"]
        # 다시 봐도 두 번 쓰지 않는다
        st = await goals.status(s, r.user.id)
        assert st["streak"]["freezes"] == 0
        marks = (await s.execute(ReadingDayMark.__table__.select())).all()
        assert len(marks) == 2


async def test_freeze_not_spent_without_a_streak(session_factory, gclock):
    async with session_factory() as s:
        r = await _reader(s)
        gclock.set(kst(2026, 11, 10, 12))
        await goals.grant_freezes(s, r.user.id, 2)
        gclock.set(kst(2026, 11, 15, 12))  # 한 번도 읽지 않은 채 며칠이 지났다
        st = await goals.status(s, r.user.id)
        assert st["streak"]["freezes"] == 2 and st["streak"]["days"] == 0


async def test_repair_restores_streak_once_per_month(session_factory, gclock):
    async with session_factory() as s:
        r = await _reader(s)
        await r.read(kst(2026, 11, 2, 12), 5)
        gclock.set(kst(2026, 11, 3, 12))
        await goals.status(s, r.user.id)  # 행을 만든다
        # 3일 쉬고 4일에 2배(10쪽)
        gclock.set(kst(2026, 11, 4, 9))
        st = await goals.status(s, r.user.id)
        assert st["streak"]["days"] == 0 and st["streak"]["repair"] == {"open": True, "pages": 10}
        await r.read(kst(2026, 11, 4, 10), 10)
        gclock.set(kst(2026, 11, 4, 11))
        st = await goals.status(s, r.user.id)
        kinds = {c["date"]: c["kind"] for c in st["heatmap"]["days"]}
        assert kinds["2026-11-03"] == "repair" and st["streak"]["days"] == 2
        # 같은 달 두 번째 빈 날은 복구되지 않는다
        gclock.set(kst(2026, 11, 6, 9))  # 5일 쉼
        st = await goals.status(s, r.user.id)
        assert st["streak"]["repair"]["open"] is False
        await r.read(kst(2026, 11, 6, 10), 10)
        gclock.set(kst(2026, 11, 6, 11))
        st = await goals.status(s, r.user.id)
        kinds = {c["date"]: c["kind"] for c in st["heatmap"]["days"]}
        assert kinds["2026-11-05"] == "none" and st["streak"]["days"] == 1


async def test_repair_not_after_two_missed_days(session_factory, gclock):
    async with session_factory() as s:
        r = await _reader(s)
        await r.read(kst(2026, 11, 2, 12), 5)
        gclock.set(kst(2026, 11, 2, 13))
        await goals.status(s, r.user.id)
        await r.read(kst(2026, 11, 5, 10), 10)  # 3·4일 이틀 쉼
        gclock.set(kst(2026, 11, 5, 11))
        st = await goals.status(s, r.user.id)
        assert st["streak"]["days"] == 1
        assert {c["kind"] for c in st["heatmap"]["days"]} <= {"read", "none"}


async def test_set_goal_changes_levels(session_factory, gclock):
    async with session_factory() as s:
        r = await _reader(s)
        await r.read(kst(2026, 11, 18, 9), 6)
        assert (await goals.status(s, r.user.id))["today"]["level"] == 3
        await goals.set_goal(s, r.user.id, 3)
        assert (await goals.status(s, r.user.id))["today"]["level"] == 4
        with pytest.raises(ValueError):
            await goals.set_goal(s, r.user.id, 7)
        assert (await s.get(ReadingGoal, r.user.id)).daily_pages == 3


# --- API · 화면 ---


async def _book_via_db(session_factory) -> int:
    async with session_factory() as s:
        b = Book(
            source="wikisource", external_id="goal-e2e", title="목표 책", total_chunks=3, has_fulltext=True
        )
        b.chunks = [BookChunk(chunk_index=i, text=f"쪽 {i}", char_count=3) for i in range(3)]
        s.add(b)
        await s.commit()
        return b.id


async def test_api_get_put_and_real_reading_counts(api, session_factory, clock, monkeypatch):
    from app.services import books as book_service

    monkeypatch.setattr(goals, "clock", book_service.clock)  # 같은 시계로 날짜를 잰다
    book_id = await _book_via_db(session_factory)
    async with api(upstream) as c:
        await signup(c)
        token = await csrf_of(c, "/me")
        r = await c.get("/api/v1/me/goal")
        assert r.status_code == 200 and r.json()["goal"]["pages"] == 5 and r.json()["today"]["pages"] == 0
        await read_chunk(c, book_id, 0, token, clock)
        await read_chunk(c, book_id, 1, token, clock)
        r = await c.get("/api/v1/me/goal")
        assert r.json()["today"]["pages"] == 2  # 서버가 검증한 쪽만
        h = {"X-CSRF-Token": token}
        r = await c.put("/api/v1/me/goal", json={"pages": 10}, headers=h)
        assert r.status_code == 200 and r.json()["goal"]["pages"] == 10
        for bad in (4, 0, 25):
            r = await c.put("/api/v1/me/goal", json={"pages": bad}, headers=h)
            assert r.status_code == 422
        assert (await c.put("/api/v1/me/goal", json={"pages": 3})).status_code == 403  # CSRF 없음
        assert (await c.get("/api/v1/me/goal?weeks=99")).status_code == 422
        wide = (await c.get("/api/v1/me/goal?weeks=26")).json()
        assert wide["heatmap"]["weeks"] == 26


async def test_anonymous_is_refused(api):
    async with api(upstream) as c:
        assert (await c.get("/api/v1/me/goal")).status_code == 401
        assert (await c.put("/api/v1/me/goal", json={"pages": 3})).status_code == 401


async def test_me_and_home_show_goal_entry(api):
    async with api(upstream) as c:
        await signup(c)
        me = (await c.get("/me")).text
        assert 'id="goal"' in me and "/static/goals.js" in me
        home = (await c.get("/")).text
        assert re.search(r'href="/me#goal"', home)


async def test_flag_off_hides_everything(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "feature_goals", False)
    async with api(upstream) as c:
        assert (await c.get("/api/v1/me/goal")).status_code == 404  # 로그인 전에도 404
        await signup(c)
        assert (await c.get("/api/v1/me/goal")).status_code == 404
        token = await csrf_of(c, "/me")
        r = await c.put("/api/v1/me/goal", json={"pages": 3}, headers={"X-CSRF-Token": token})
        assert r.status_code == 404
        me = (await c.get("/me")).text
        assert 'id="goal"' not in me and "goals.js" not in me
        assert "/me#goal" not in (await c.get("/")).text
