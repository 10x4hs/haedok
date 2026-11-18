"""같이 읽을 사람 모집(/together) — 글이 대결방을 만들고, 참가는 초대 코드와 같은 길로 들어간다."""

import asyncio
import re
from urllib.parse import unquote

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.base import Base
from app.models import Book, Room, RoomMember, User
from app.models_board import RecruitPost, RecruitReport
from app.services import board as board_service
from tests.conftest import signup
from tests.test_api import upstream


def csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


async def token(c) -> str:
    return csrf((await c.get("/together/new")).text)


async def make_post(c, book="c:wikisource:긴글", pitch="퇴근길에 조금씩 같이 읽어요", pace=5, capacity=4):
    r = await c.post(
        "/together",
        data={
            "csrf": await token(c),
            "book": book,
            "pitch": pitch,
            "pace": str(pace),
            "capacity": str(capacity),
        },
    )
    return r


async def post_id_of(r) -> int:
    assert r.status_code == 303, r.text
    return int(re.match(r"/together/(\d+)", r.headers["location"]).group(1))


async def members(session_factory, post_id: int) -> list[str]:
    async with session_factory() as s:
        post = await s.get(RecruitPost, post_id)
        rows = await s.scalars(
            select(User.nickname)
            .join(RoomMember, RoomMember.user_id == User.id)
            .where(RoomMember.room_id == post.room_id)
            .order_by(User.id)
        )
        return list(rows)


@pytest.fixture
def board_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "feature_board", True)


async def test_create_post_makes_room_and_author_is_member(api, session_factory, board_on):
    async with api(upstream) as host:
        await signup(host, email="host@example.com", nickname="호스트")
        r = await make_post(host, pitch="  퇴근길에\n조금씩 <b>같이</b>  ")
        pid = await post_id_of(r)
        page = await host.get(f"/together/{pid}")
    async with session_factory() as s:
        post = await s.get(RecruitPost, pid)
        room = await s.get(Room, post.room_id)
        assert room is not None and room.owner_id == post.author_id and room.book_id == post.book_id
        assert (post.status, post.pace, post.capacity, post.seats) == ("open", 5, 4, 1)
        assert post.pitch == "퇴근길에 조금씩 <b>같이</b>"  # 줄바꿈은 한 칸, 글은 그대로 저장
    assert await members(session_factory, pid) == ["호스트"]
    # 한 줄 소개는 자동 이스케이프돼 보인다
    assert "&lt;b&gt;같이&lt;/b&gt;" in page.text and "<b>같이</b>" not in page.text
    assert "하루 5쪽 · 약 5분" in page.text and "1/4명" in page.text


async def test_only_readable_public_domain_books(api, session_factory, board_on, monkeypatch):
    from app import board as board_pages

    monkeypatch.setattr(board_pages, "POST_LIMIT", 100)  # 실패한 시도도 한도에 센다
    async with api(upstream) as c:
        await signup(c)
        page = await c.get("/upload")
        up = await c.post(
            "/upload",
            files={"file": ("나만.txt", ("가나다라 " * 40).encode(), "text/plain")},
            data={"csrf": csrf(page.text)},
        )
        upload_id = up.headers["location"].rsplit("/", 1)[1]
        async with session_factory() as s:
            store = Book(source="kakao", external_id="x1", title="서점에만 있는 책", has_fulltext=False)
            s.add(store)
            await s.commit()
            store_id = store.id
        bad = [
            await make_post(c, book=f"b:{upload_id}"),  # 내가 올린 파일
            await make_post(c, book=f"b:{store_id}"),  # 서점 책(본문 없음)
            await make_post(c, book="c:kakao:x1"),  # 전문 소스가 아님
            await make_post(c, book="b:99999"),
            await make_post(c, book=""),
        ]
        bad_pitch = await make_post(c, pitch="   ")
        bad_pace = await make_post(c, pace=7)
        bad_cap = await make_post(c, capacity=9)
    assert all(r.status_code == 400 for r in bad)
    assert "고전만 모집할 수 있어요" in bad[0].text
    assert bad_pitch.status_code == bad_pace.status_code == bad_cap.status_code == 400
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(RecruitPost)) == 0


async def test_join_lands_in_room_and_double_join_is_noop(api, session_factory, board_on):
    async with api(upstream) as host, api(upstream) as friend:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(friend, email="friend@example.com", nickname="친구")
        pid = await post_id_of(await make_post(host))
        page = await friend.get(f"/together/{pid}")
        assert "참가하기" in page.text
        r1 = await friend.post(f"/together/{pid}/join", data={"csrf": csrf(page.text)})
        r2 = await friend.post(f"/together/{pid}/join", data={"csrf": csrf(page.text)})
        async with session_factory() as s:
            post = await s.get(RecruitPost, pid)
        assert r1.headers["location"] == r2.headers["location"] == f"/races/{post.room_id}"
        assert (await friend.get(f"/races/{post.room_id}")).status_code == 200  # 방에 들어갔다
        assert "방으로 가기" in (await friend.get(f"/together/{pid}")).text
    assert await members(session_factory, pid) == ["호스트", "친구"]
    async with session_factory() as s:
        assert (await s.get(RecruitPost, pid)).seats == 2


async def test_last_seat_race_lets_exactly_one_in(api, session_factory, board_on):
    async with api(upstream) as host, api(upstream) as a, api(upstream) as b:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(a, email="a@example.com", nickname="가")
        await signup(b, email="b@example.com", nickname="나")
        pid = await post_id_of(await make_post(host, capacity=2))
        ta, tb = csrf((await a.get(f"/together/{pid}")).text), csrf((await b.get(f"/together/{pid}")).text)
        ra, rb = await asyncio.gather(
            a.post(f"/together/{pid}/join", data={"csrf": ta}),
            b.post(f"/together/{pid}/join", data={"csrf": tb}),
        )
    landed = [r.headers["location"].startswith("/races/") for r in (ra, rb)]
    assert sorted(landed) == [False, True]
    loser = ra if not landed[0] else rb
    assert "자리가 다 찼어요" in unquote(loser.headers["location"])
    assert len(await members(session_factory, pid)) == 2


async def test_service_last_seat_separate_connections(tmp_path):
    """서비스 수준: 따로 연결한 다섯 명이 마지막 한 자리를 동시에 잡아도 한 명만 들어간다.

    테스트 기본 DB는 연결 하나를 나눠 쓰므로, 여기서는 파일 SQLite에 요청마다 새 연결을 맺어
    실제 잠금을 거친다. 조건부 UPDATE(seats < capacity)를 빼면 여섯 명이 모두 들어간다."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/race.db", poolclass=NullPool, connect_args={"timeout": 10}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        users = [User(email=f"u{i}@example.com", nickname=f"u{i}", password_hash="x") for i in range(6)]
        book = Book(source="wikisource", external_id="책", title="책", has_fulltext=True, total_chunks=3)
        s.add_all([*users, book])
        await s.commit()
        post = await board_service.create_post(s, users[0], book, "같이 읽어요", 5, 2)
        pid, uids = post.id, [u.id for u in users]

    async def attempt(uid: int) -> str | None:
        async with factory() as s:
            return await board_service.join(s, await s.get(RecruitPost, pid), await s.get(User, uid))

    results = await asyncio.gather(*(attempt(uid) for uid in uids[1:]))
    async with factory() as s:
        post = await s.get(RecruitPost, pid)
        count = await s.scalar(select(func.count()).where(RoomMember.room_id == post.room_id))
    await engine.dispose()
    assert results.count(None) == 1 and results.count("자리가 다 찼어요") == 4
    assert count == 2 and post.seats == 2


async def test_closed_and_full_posts_cannot_be_joined(api, session_factory, board_on):
    async with api(upstream) as host, api(upstream) as a, api(upstream) as b:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(a, email="a@example.com", nickname="가")
        await signup(b, email="b@example.com", nickname="나")
        full = await post_id_of(await make_post(host, capacity=2))
        closed = await post_id_of(await make_post(host))
        t = csrf((await a.get(f"/together/{full}")).text)
        await a.post(f"/together/{full}/join", data={"csrf": t})
        page = await b.get(f"/together/{full}")
        assert "자리가 다 찼어요" in page.text and "참가하기" not in page.text
        r = await b.post(f"/together/{full}/join", data={"csrf": csrf(page.text)})
        assert "자리가 다 찼어요" in unquote(r.headers["location"])

        ht = csrf((await host.get(f"/together/{closed}")).text)
        assert (await host.post(f"/together/{closed}/close", data={"csrf": ht})).status_code == 303
        page = await b.get(f"/together/{closed}")
        assert "모집 마감" in page.text and "참가하기" not in page.text
        r = await b.post(f"/together/{closed}/join", data={"csrf": csrf(page.text)})
        assert "마감" in unquote(r.headers["location"])
    assert await members(session_factory, closed) == ["호스트"]
    assert await members(session_factory, full) == ["호스트", "가"]


async def test_leave_frees_the_seat_and_host_cannot_leave(api, session_factory, board_on):
    async with api(upstream) as host, api(upstream) as a, api(upstream) as b:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(a, email="a@example.com", nickname="가")
        await signup(b, email="b@example.com", nickname="나")
        pid = await post_id_of(await make_post(host, capacity=2))
        t = csrf((await a.get(f"/together/{pid}")).text)
        await a.post(f"/together/{pid}/join", data={"csrf": t})
        assert "나가기" in (await a.get(f"/together/{pid}")).text
        r = await a.post(f"/together/{pid}/leave", data={"csrf": t})
        assert r.status_code == 303 and "msg=" in r.headers["location"]
        ht = csrf((await host.get(f"/together/{pid}")).text)
        r = await host.post(f"/together/{pid}/leave", data={"csrf": ht})
        assert "error=" in r.headers["location"]
        # 빈 자리에 다른 사람이 들어온다
        tb = csrf((await b.get(f"/together/{pid}")).text)
        r = await b.post(f"/together/{pid}/join", data={"csrf": tb})
        assert r.headers["location"].startswith("/races/")
    assert await members(session_factory, pid) == ["호스트", "나"]


async def test_host_close_and_delete_and_others_cannot(api, session_factory, board_on):
    async with api(upstream) as host, api(upstream) as a:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(a, email="a@example.com", nickname="가")
        alone = await post_id_of(await make_post(host))
        shared = await post_id_of(await make_post(host))
        t = csrf((await a.get(f"/together/{shared}")).text)
        await a.post(f"/together/{shared}/join", data={"csrf": t})
        # 방장이 아닌 사람은 마감·삭제를 못 한다
        for action in ("close", "delete"):
            r = await a.post(f"/together/{shared}/{action}", data={"csrf": t})
            assert "error=" in r.headers["location"]
        async with session_factory() as s:
            alone_room = (await s.get(RecruitPost, alone)).room_id
            shared_room = (await s.get(RecruitPost, shared)).room_id
        ht = csrf((await host.get(f"/together/{alone}")).text)
        assert (
            (await host.post(f"/together/{alone}/delete", data={"csrf": ht}))
            .headers["location"]
            .startswith("/together?")
        )
        await host.post(f"/together/{shared}/delete", data={"csrf": ht})
        assert (await host.get(f"/together/{alone}")).status_code == 404
        # 이미 들어온 사람이 있으면 방은 남는다
        assert (await a.get(f"/races/{shared_room}")).status_code == 200
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(RecruitPost)) == 0
        assert await s.get(Room, alone_room) is None
        assert await s.get(Room, shared_room) is not None


async def test_three_open_posts_limit(api, session_factory, board_on):
    async with api(upstream) as c:
        await signup(c)
        ids = [await post_id_of(await make_post(c)) for _ in range(3)]
        fourth = await make_post(c)
        assert fourth.status_code == 400 and "3개까지" in fourth.text
        t = csrf((await c.get(f"/together/{ids[0]}")).text)
        await c.post(f"/together/{ids[0]}/close", data={"csrf": t})
        assert (await make_post(c)).status_code == 303  # 하나 마감하면 다시 올릴 수 있다


async def test_posting_is_rate_limited(api, board_on, monkeypatch):
    from app import board as board_pages

    monkeypatch.setattr(board_pages, "POST_LIMIT", 1)
    async with api(upstream) as c:
        await signup(c)
        assert (await make_post(c)).status_code == 303
        r = await make_post(c)
    assert r.status_code == 429 and "너무 자주" in r.text


async def test_anonymous_is_refused(api, session_factory, board_on):
    async with api(upstream) as host, api(upstream) as anon:
        await signup(host)
        pid = await post_id_of(await make_post(host))
        for path in ("/together", "/together/new", f"/together/{pid}"):
            r = await anon.get(path)
            assert r.status_code == 303 and "/login" in r.headers["location"]
        for action in ("join", "leave", "close", "delete", "report"):
            r = await anon.post(f"/together/{pid}/{action}", data={"csrf": "x"})
            assert r.status_code in (303, 403) and not r.headers.get("location", "").startswith("/races/")
        r = await anon.post("/together", data={"csrf": "x", "book": "c:wikisource:긴글", "pitch": "a"})
        assert r.status_code in (303, 403)
    assert await members(session_factory, pid) == ["독자"]


async def test_csrf_is_required(api, board_on):
    async with api(upstream) as host, api(upstream) as a:
        await signup(host, email="host@example.com")
        await signup(a, email="a@example.com")
        pid = await post_id_of(await make_post(host))
        assert (await a.post(f"/together/{pid}/join", data={"csrf": "nope"})).status_code == 403


async def test_board_lists_only_open_posts_newest_first(api, board_on):
    async with api(upstream) as host, api(upstream) as viewer:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(viewer, email="v@example.com")
        first = await post_id_of(await make_post(host, pitch="첫 번째 글"))
        second = await post_id_of(await make_post(host, pitch="두 번째 글"))
        closed = await post_id_of(await make_post(host, pitch="마감한 글"))
        t = csrf((await host.get(f"/together/{closed}")).text)
        await host.post(f"/together/{closed}/close", data={"csrf": t})
        page = (await viewer.get("/together")).text
    assert "마감한 글" not in page
    assert page.index("두 번째 글") < page.index("첫 번째 글")
    assert f'href="/together/{first}"' in page and f'href="/together/{second}"' in page
    assert "하루 5쪽 · 약 5분" in page and "1/4명" in page and "호스트" in page
    assert page.count('class="rl-join"') == 2  # 같은 줄 모양, 오른쪽 끝에 참가 표시


async def test_empty_board_invites_first_post(api, board_on):
    async with api(upstream) as c:
        await signup(c)
        page = (await c.get("/together")).text
    assert "아직 모집 중인 글이 없어요" in page and 'href="/together/new"' in page


async def test_known_people_shown_on_card(api, board_on):
    """다른 방에서 같이 읽어 본 사람이 들어가 있으면 '아는 사람 n명'."""
    async with api(upstream) as host, api(upstream) as a:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(a, email="a@example.com", nickname="가")
        pid = await post_id_of(await make_post(host))
        assert "아는 사람" not in (await a.get("/together")).text
        # 초대 코드로 호스트와 다른 방을 한 번 같이 했다
        page = await host.get("/races")
        r = await host.post("/races/start/wikisource/긴글", data={"csrf": csrf(page.text), "days": "3"})
        code = re.search(r'id="code">([A-Z0-9]{6})<', (await host.get(r.headers["location"])).text).group(1)
        prev = await a.get(f"/join/{code}")
        await a.post(f"/join/{code}", data={"csrf": csrf(prev.text)})
        assert "아는 사람 1명" in (await a.get("/together")).text
        assert f'href="/together/{pid}"' in (await a.get("/together")).text


async def test_report_creates_row_and_admin_lists_it(api, session_factory, board_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "admin@example.com")
    async with api(upstream) as host, api(upstream) as a, api(upstream) as adm:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(a, email="a@example.com")
        await signup(adm, email="admin@example.com")
        pid = await post_id_of(await make_post(host, pitch="광고 글"))
        t = csrf((await a.get(f"/together/{pid}")).text)
        r = await a.post(f"/together/{pid}/report", data={"csrf": t, "reason": "spam"})
        assert "msg=" in r.headers["location"]
        await a.post(f"/together/{pid}/report", data={"csrf": t, "reason": "spam"})  # 두 번째는 무시
        bad = await a.post(f"/together/{pid}/report", data={"csrf": t, "reason": "<x>"})
        assert "error=" in bad.headers["location"]
        ht = csrf((await host.get(f"/together/{pid}")).text)
        own = await host.post(f"/together/{pid}/report", data={"csrf": ht, "reason": "spam"})
        assert "error=" in own.headers["location"]
        assert (await a.get("/admin/together")).status_code == 404  # 일반 사용자는 못 본다
        listing = await adm.get("/admin/together")
        assert listing.status_code == 200 and "광고 글" in listing.text and "신고 1건" in listing.text
        assert "모집 신고" in (await adm.get("/admin")).text
        at = csrf(listing.text)
        await adm.post(f"/admin/together/{pid}/close", data={"csrf": at})
        assert "광고 글" not in (await a.get("/together")).text
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(RecruitReport)) == 1
        assert (await s.get(RecruitPost, pid)).status == "closed"


async def test_races_page_shows_section_and_action(api, board_on):
    async with api(upstream) as host, api(upstream) as viewer:
        await signup(host, email="host@example.com", nickname="호스트")
        await signup(viewer, email="v@example.com")
        empty = (await viewer.get("/races")).text
        assert 'id="recruitH"' in empty and 'href="/together/new"' in empty
        for i in range(3):
            await make_post(host, pitch=f"글 {i}")
        # 네 번째 글은 다른 사람이 올린다
        await make_post(viewer, pitch="내 글")
        page = (await viewer.get("/races")).text
    assert page.index('id="mineH"') < page.index('id="recruitH"')  # 내 방이 먼저, 모집 중은 그 아래
    assert page.count('class="rl-row rl-post"') == 3  # 많아야 3개
    assert "내 글" not in page  # 이미 들어가 있는 내 글은 빼고 보여 준다
    assert 'href="/together"' in page


async def test_flag_off_404_and_no_section(api, monkeypatch):
    async with api(upstream) as c:
        await signup(c)
        pid = await post_id_of(await make_post(c))
        monkeypatch.setattr(get_settings(), "feature_board", False)
        for path in ("/together", "/together/new", f"/together/{pid}"):
            assert (await c.get(path)).status_code == 404
        t = csrf((await c.get("/races")).text)
        assert (await c.post(f"/together/{pid}/join", data={"csrf": t})).status_code == 404
        assert (await c.post("/together", data={"csrf": t})).status_code == 404
        races = (await c.get("/races")).text
    assert 'id="recruitH"' not in races and "/together" not in races


async def test_flag_off_404_for_anonymous_too(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "feature_board", False)
    async with api(upstream) as anon:
        assert (await anon.get("/together")).status_code == 404
