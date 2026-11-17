"""포인트 원장 — 적립 멱등성, KST 주간 경계, 순위 정렬, 연속 일수."""

import datetime as dt
import re

import pytest

from app.models import Book, Room, RoomMember, User
from app.services import points
from tests.conftest import read_chunk, signup
from tests.test_api import upstream

KST = points.KST


@pytest.fixture
def pclock(monkeypatch):
    """포인트 서비스의 시계를 돌린다: pclock.set(datetime) / pclock.advance(days=)."""
    state = {"now": dt.datetime(2026, 11, 11, 3, 0, tzinfo=dt.UTC)}  # 수요일 12:00 KST
    monkeypatch.setattr(points, "clock", lambda: state["now"])

    class C:
        @staticmethod
        def set(t: dt.datetime) -> None:
            state["now"] = t

        @staticmethod
        def advance(**kw) -> None:
            state["now"] += dt.timedelta(**kw)

        @property
        def now(self) -> dt.datetime:
            return state["now"]

    return C()


async def _user(session, nickname="독자", email=None) -> User:
    u = User(nickname=nickname, email=email or f"{nickname}@example.com", password_hash=None)
    session.add(u)
    await session.commit()
    return u


# --- 멱등성 ---


async def test_award_is_idempotent_on_user_source_ref(session_factory, pclock):
    async with session_factory() as s:
        u = await _user(s)
        first = await points.award(s, u.id, points.CHUNK, None, "1:0")
        again = await points.award(s, u.id, points.CHUNK, None, "1:0")
        other_ref = await points.award(s, u.id, points.CHUNK, None, "1:1")
        other_source = await points.award(s, u.id, points.QUOTE, None, "1:0")
        assert first is not None and first.amount == 1
        assert again is None
        assert other_ref is not None and other_source is not None and other_source.amount == 3
        assert (await points.summary(s, u.id)).total == 5


async def test_reaction_first_per_paragraph_and_daily_cap(session_factory, pclock):
    async with session_factory() as s:
        u = await _user(s)
        assert await points.award_reaction(s, u.id, 1, 0, 0)
        assert await points.award_reaction(s, u.id, 1, 0, 0) is None  # 같은 문단은 한 번만
        for i in range(1, points.REACTION_DAILY_CAP):
            assert await points.award_reaction(s, u.id, 1, i, 0)
        assert await points.award_reaction(s, u.id, 1, 99, 0) is None  # 하루 20번
        pclock.advance(days=1)
        assert await points.award_reaction(s, u.id, 1, 99, 0)  # 다음 날(KST)엔 다시


# --- 주간 경계(월요일 0시 KST) ---


def test_week_start_is_monday_midnight_kst():
    # 2026-11-16(월) 00:00 KST = 11-15(일) 15:00 UTC
    sunday_night = dt.datetime(2026, 11, 15, 14, 59, tzinfo=dt.UTC)  # 월 23:59 KST → 아직 지난주
    monday = dt.datetime(2026, 11, 15, 15, 0, tzinfo=dt.UTC)  # 월 00:00 KST
    assert points.week_start(sunday_night) == dt.datetime(2026, 11, 8, 15, 0, tzinfo=dt.UTC)
    assert points.week_start(monday) == dt.datetime(2026, 11, 15, 15, 0, tzinfo=dt.UTC)
    assert points.week_start(monday).astimezone(KST).weekday() == 0


async def test_weekly_total_resets_at_kst_monday(session_factory, pclock):
    async with session_factory() as s:
        u = await _user(s)
        pclock.set(dt.datetime(2026, 11, 15, 14, 30, tzinfo=dt.UTC))  # 일요일 23:30 KST
        await points.award(s, u.id, points.QUOTE, None, "q1")  # 지난주 3점
        pclock.set(dt.datetime(2026, 11, 15, 15, 30, tzinfo=dt.UTC))  # 월요일 00:30 KST
        await points.award(s, u.id, points.CHUNK, None, "1:0")  # 이번 주 1점
        summ = await points.summary(s, u.id)
        assert (summ.total, summ.week) == (4, 1)
        week = await points.leaderboard(s, u.id, "week")
        alltime = await points.leaderboard(s, u.id, "all")
        assert week.entries[0].points == 1 and alltime.entries[0].points == 4


# --- 순위 ---


async def test_leaderboard_orders_ties_and_marks_friends(session_factory, pclock):
    async with session_factory() as s:
        me = await _user(s, "나")
        friend = await _user(s, "친구")
        rival = await _user(s, "라이벌")
        stranger = await _user(s, "남")
        nobody = await _user(s, "구경꾼")
        book = Book(source="wikisource", external_id="x", title="책", total_chunks=3)
        s.add(book)
        await s.flush()
        room = Room(book_id=book.id, owner_id=me.id, name="방", invite_code="ABC123")
        s.add(room)
        await s.flush()
        s.add_all(
            [RoomMember(room_id=room.id, user_id=me.id), RoomMember(room_id=room.id, user_id=friend.id)]
        )
        await s.commit()
        await points.award(s, rival.id, points.RACE_FINISH, None, "r")  # 20
        await points.award(s, friend.id, points.OVERTAKE, None, "o")  # 5
        await points.award(s, stranger.id, points.OVERTAKE, None, "o")  # 5
        await points.award(s, me.id, points.CHUNK, None, "1:0")  # 1
        board = await points.leaderboard(s, me.id, "all")
        rows = [(e.rank, e.user.nickname, e.points, e.is_me, e.is_friend) for e in board.entries]
        assert rows == [
            (1, "라이벌", 20, False, False),
            (2, "친구", 5, False, True),
            (2, "남", 5, False, False),  # 동점은 같은 등수, 가입 순
            (4, "나", 1, True, False),
        ]
        assert board.me and board.me.rank == 4
        assert nobody.id not in {e.user.id for e in board.entries}  # 0점은 순위에 없다
        # 50위 밖이어도 내 순위는 따로 계산한다
        board = await points.leaderboard(s, me.id, "all", limit=2)
        assert [e.user.nickname for e in board.entries] == ["라이벌", "친구"]
        assert board.me and board.me.rank == 4 and board.me.points == 1
        assert (await points.leaderboard(s, nobody.id, "week")).me is None


# --- 연속 일수 ---


async def test_streak_counts_consecutive_kst_days_and_awards_bonus(session_factory, pclock):
    async with session_factory() as s:
        u = await _user(s)
        start = dt.datetime(2026, 11, 2, 14, 50, tzinfo=dt.UTC)  # 11-02 23:50 KST(월)
        for d in range(6):
            pclock.set(start + dt.timedelta(days=d))
            assert await points.award_progress(s, u.id, Book(id=1, total_chunks=99), d - 1, d)
        assert await points.streak_days(s, u.id) == 6
        assert not [r for r in await points.ledger(s, u.id) if r["source"] == points.STREAK]
        # 7일째 — 자정 직후에 읽어도 KST 날짜가 바뀌었으니 이어진다
        pclock.set(start + dt.timedelta(days=5, minutes=20))  # 11-08 00:10 KST
        rows = await points.award_progress(s, u.id, Book(id=1, total_chunks=99), 5, 6)
        assert [r.source for r in rows] == [points.CHUNK, points.STREAK]
        assert rows[-1].amount == 30 and rows[-1].ref_id == "2026-11-08"
        # 같은 날 더 읽어도 보너스는 한 번
        rows = await points.award_progress(s, u.id, Book(id=1, total_chunks=99), 6, 7)
        assert [r.source for r in rows] == [points.CHUNK]
        summ = await points.summary(s, u.id)
        assert (summ.streak, summ.total) == (7, 8 + 30)
        # 하루 건너뛰면 끊긴다; 다음 날 아직 안 읽었으면 어제까지를 센다
        pclock.set(start + dt.timedelta(days=6, minutes=20))  # 11-09, 아직 안 읽음
        assert await points.streak_days(s, u.id) == 7
        pclock.set(start + dt.timedelta(days=7, minutes=20))  # 11-10, 하루 건너뜀
        assert await points.streak_days(s, u.id) == 0


# --- 실제 경로에 붙어 있나 ---


async def test_verified_chunk_reaction_and_pages_award_points(api, clock):
    async with api(upstream) as host, api(upstream) as friend:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(friend, email="friend@example.com", nickname="친구")
        page = await host.get("/races")
        token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        r = await host.post("/races/start/wikisource/긴글", data={"csrf": token, "days": "3"})
        race_url = r.headers["location"]
        race = await host.get(race_url)
        code = re.search(r'id="code">([A-Z0-9]{6})<', race.text).group(1)
        preview = await friend.get(f"/join/{code}")
        await friend.post(
            f"/join/{code}", data={"csrf": re.search(r'name="csrf" value="([^"]+)"', preview.text).group(1)}
        )
        book_id = re.search(r'href="/books/(\d+)"', race.text).group(1)
        reader = await friend.get(f"/books/{book_id}")
        ftoken = reader.text.split('data-csrf="')[1].split('"')[0]
        headers = {"X-CSRF-Token": ftoken}

        # 아무도 점수가 없을 때 — 빈 상태
        empty = await host.get("/leaderboard")
        assert empty.status_code == 200 and "아직 아무도 점수가 없어요" in empty.text

        # 펼치기만 하면 0점, 체류시간을 채워야 1점
        await friend.put(f"/api/v1/books/{book_id}/progress", json={"chunk_index": 0}, headers=headers)
        assert (await friend.get("/api/me/points")).json()["total"] == 0
        out = await read_chunk(friend, book_id, 0, ftoken, clock)
        assert out["verified_chunk"] == 0
        me = (await friend.get("/api/me/points")).json()
        # 쪽 +1, 그리고 아직 0쪽인 호스트를 추월 +5
        assert me["total"] == 6 and me["week"] == 6
        assert [r["source"] for r in me["ledger"]] == ["overtake", "chunk"]
        # 같은 쪽을 다시 보내도 한 번만
        await friend.put(
            f"/api/v1/books/{book_id}/progress", json={"chunk_index": 0, "verified": True}, headers=headers
        )
        assert (await friend.get("/api/v1/me/points")).json()["total"] == 6

        # 반응: 같은 문단 두 번째는 0점
        for _ in range(2):
            r = await friend.post(
                f"/api/v1/books/{book_id}/reactions",
                json={"chunk_index": 0, "anchor": 0, "emoji": "👏"},
                headers=headers,
            )
            assert r.status_code == 201
        # 문장 공유 +3
        room_id = race_url.rsplit("/", 1)[1]
        r = await friend.post(
            f"/api/v1/races/{room_id}/quotes", json={"chunk_index": 0, "text": "좋은 문장"}, headers=headers
        )
        assert r.status_code == 201
        me = (await friend.get("/api/me/points")).json()
        assert me["total"] == 6 + 1 + 3 and me["streak_days"] == 1

        # 순위표: 친구가 1위, 호스트 화면에서 '같은 방 친구' 표시
        board = (await host.get("/leaderboard?range=week")).text
        assert "순위표" in board and "같은 방 친구" in board and "10<small>P</small>" in board
        mine = (await friend.get("/leaderboard")).text
        assert 'class="lb-me' in mine and "1<small>위</small>" in mine
        # /me 요약 카드
        me_page = (await friend.get("/me")).text
        assert "points-card" in me_page and "1</b>일 연속" in me_page
        # /races 헤더에서 순위로 가는 링크
        assert 'href="/leaderboard"' in (await host.get("/races")).text
