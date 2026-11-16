"""hardening: 포인트 원장 (user_id, source, ref_id) unique 제약 + 연속 일수는 쿼리 하나로."""

import datetime as dt
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.models import PointsLog, User
from app.services import points
from tests.test_points import pclock  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


async def _user(session, nickname="독자") -> User:
    u = User(nickname=nickname, email=f"{nickname}@example.com", password_hash=None)
    session.add(u)
    await session.commit()
    return u


class Statements:
    """엔진에 나가는 SQL을 센다."""

    def __init__(self, engine) -> None:
        self.sql: list[str] = []
        self._engine = engine.sync_engine
        event.listen(self._engine, "before_cursor_execute", self._on)

    def _on(self, conn, cursor, statement, params, context, executemany):
        self.sql.append(statement)

    def close(self) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on)

    def touching(self, word: str) -> list[str]:
        return [s for s in self.sql if "points_log" in s and s.lstrip().upper().startswith(word)]


def test_model_declares_unique_user_source_ref():
    uniques = [
        tuple(c.name for c in con.columns)
        for con in PointsLog.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("user_id", "source", "ref_id") in uniques


async def test_database_rejects_duplicate_rows(session_factory):
    async with session_factory() as s:
        u = await _user(s)
        s.add_all([PointsLog(user_id=u.id, source="chunk", amount=1, ref_id="1:0") for _ in range(2)])
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_award_inserts_without_select_first_and_survives_duplicates(session_factory, pclock):  # noqa: F811
    async with session_factory() as s:
        u = await _user(s)
        engine = s.bind
        seen = Statements(engine)
        try:
            first = await points.award(s, u.id, points.CHUNK, None, "7:3")
            dupe = await points.award(s, u.id, points.CHUNK, None, "7:3")
        finally:
            seen.close()
        assert first is not None and dupe is None
        assert seen.touching("SELECT") == []  # 넣기 전에 찾아보지 않는다 — 제약이 지킨다
        assert len(seen.touching("INSERT")) == 2
        # 중복 뒤에도 세션은 계속 쓸 수 있고, 이전에 넣은 것은 그대로다
        assert await points.award(s, u.id, points.CHUNK, None, "7:4") is not None
        rows = (await s.scalars(select(PointsLog.ref_id).where(PointsLog.user_id == u.id))).all()
        assert sorted(rows) == ["7:3", "7:4"]


async def test_duplicate_award_keeps_callers_pending_work(session_factory, pclock):  # noqa: F811
    """중복은 SAVEPOINT 안에서만 되돌린다 — 호출한 쪽이 같은 트랜잭션에 올려 둔 변경은 살아남는다."""
    async with session_factory() as s:
        u = await _user(s)
        await points.award(s, u.id, points.QUOTE, None, "q1")
        u.nickname = "바뀐이름"  # 아직 commit 전
        assert await points.award(s, u.id, points.QUOTE, None, "q1") is None
        await s.commit()
    async with session_factory() as s:
        assert (await s.get(User, u.id)).nickname == "바뀐이름"


async def test_concurrent_award_from_two_sessions_gives_points_once(session_factory, pclock):  # noqa: F811
    async with session_factory() as a, session_factory() as b:
        u = await _user(a)
        got = [await points.award(a, u.id, points.QUOTE, None, "same"), await points.award(b, u.id, points.QUOTE, None, "same")]
        assert sum(r is not None for r in got) == 1
        assert (await points.summary(b, u.id)).total == points.RULES[points.QUOTE]


# --- 연속 일수: 하루마다 쿼리 하나가 아니라 KST 날짜 집계 쿼리 하나 ---


async def _read_on(s, user_id: int, when: dt.datetime, ref: str) -> None:
    s.add(PointsLog(user_id=user_id, source=points.CHUNK, amount=1, ref_id=ref, created_at=when))
    await s.commit()


async def test_streak_is_one_query_and_respects_kst_days(session_factory, pclock):  # noqa: F811
    async with session_factory() as s:
        u = await _user(s)
        now = dt.datetime(2026, 11, 20, 3, 0, tzinfo=dt.UTC)  # 11-20 12:00 KST
        pclock.set(now)
        # 30일 연속 + 하루에 여러 쪽, 그리고 그 전에 빈 날 하나
        for d in range(30):
            day = now - dt.timedelta(days=d)
            await _read_on(s, u.id, day, f"a{d}")
            await _read_on(s, u.id, day - dt.timedelta(hours=1), f"b{d}")
        await _read_on(s, u.id, now - dt.timedelta(days=32), "old")
        # 다른 사람·다른 source 는 세지 않는다
        other = await _user(s, "남")
        await _read_on(s, other.id, now - dt.timedelta(days=30), "x")
        s.add(PointsLog(user_id=u.id, source=points.QUOTE, amount=3, ref_id="q", created_at=now - dt.timedelta(days=30)))
        await s.commit()

        seen = Statements(s.bind)
        try:
            n = await points.streak_days(s, u.id)
        finally:
            seen.close()
        assert n == 30
        assert len(seen.touching("SELECT")) == 1


async def test_streak_kst_midnight_boundary(session_factory, pclock):  # noqa: F811
    async with session_factory() as s:
        u = await _user(s)
        # 11-19 23:50 KST 와 11-20 00:10 KST — UTC로는 같은 날(11-19)이지만 KST로는 이틀
        await _read_on(s, u.id, dt.datetime(2026, 11, 19, 14, 50, tzinfo=dt.UTC), "1")
        await _read_on(s, u.id, dt.datetime(2026, 11, 19, 15, 10, tzinfo=dt.UTC), "2")
        pclock.set(dt.datetime(2026, 11, 20, 5, 0, tzinfo=dt.UTC))
        assert await points.streak_days(s, u.id) == 2
        # 하루 비면 오늘 아직 안 읽은 것으로 보고 어제까지만 센다; 이틀 비면 0
        pclock.set(dt.datetime(2026, 11, 21, 5, 0, tzinfo=dt.UTC))
        assert await points.streak_days(s, u.id) == 2
        pclock.set(dt.datetime(2026, 11, 22, 5, 0, tzinfo=dt.UTC))
        assert await points.streak_days(s, u.id) == 0


async def test_streak_with_no_reading_is_zero(session_factory, pclock):  # noqa: F811
    async with session_factory() as s:
        u = await _user(s)
        assert await points.streak_days(s, u.id) == 0


# --- 마이그레이션: 기존 중복을 지운 뒤 제약을 건다 ---


def _hardening_migration():
    files = sorted(MIGRATIONS.glob("c2*.py"))
    assert len(files) == 1, files
    spec = importlib.util.spec_from_file_location("hardening_migration", files[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_metadata():
    mod = _hardening_migration()
    assert mod.revision.startswith("c2") and mod.down_revision == "c1a4e8d2f305"


async def test_migration_dedupes_then_adds_constraint(session_factory):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    mod = _hardening_migration()
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        # 제약이 없던 예전 테이블 모양
        await conn.execute(
            text(
                "CREATE TABLE points_log (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, source VARCHAR(30) NOT NULL,"
                " amount INTEGER NOT NULL, ref_id VARCHAR(64), created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        await conn.execute(text("CREATE INDEX ix_points_log_user_id ON points_log (user_id)"))
        rows = [
            (1, 1, "chunk", "1:0"),
            (2, 1, "chunk", "1:0"),  # 중복 — 지운다
            (3, 1, "chunk", "1:1"),
            (4, 2, "chunk", "1:0"),
            (5, 1, "quote", "1:0"),
            (6, 1, "chunk", "1:0"),  # 중복 — 지운다
            (7, 1, "chunk", None),
            (8, 1, "chunk", None),  # ref 없는 행은 건드리지 않는다
        ]
        for rid, uid, src, ref in rows:
            await conn.execute(
                text("INSERT INTO points_log (id, user_id, source, amount, ref_id) VALUES (:i, :u, :s, 1, :r)"),
                {"i": rid, "u": uid, "s": src, "r": ref},
            )

        def upgrade(sync_conn):
            ctx = MigrationContext.configure(sync_conn)
            with Operations.context(ctx):
                mod.upgrade()
            return [r[0] for r in sync_conn.execute(text("SELECT id FROM points_log ORDER BY id"))], [
                tuple(u["column_names"]) for u in inspect(sync_conn).get_unique_constraints("points_log")
            ]

        ids, uniques = await conn.run_sync(upgrade)
    assert ids == [1, 3, 4, 5, 7, 8]
    assert ("user_id", "source", "ref_id") in uniques

    async with engine.begin() as conn:

        def downgrade(sync_conn):
            ctx = MigrationContext.configure(sync_conn)
            with Operations.context(ctx):
                mod.downgrade()
            return inspect(sync_conn).get_unique_constraints("points_log")

        assert await conn.run_sync(downgrade) == []
    await engine.dispose()
