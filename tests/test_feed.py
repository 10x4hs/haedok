"""라운드 6 — 피드(/feed)가 앱의 소셜 홈: 동그라미 줄 · 작성 칸 · 전체/친구/탐색 탭 · 활동 소식 ·
새 게시물 알약(/feed/new) · 이어 읽기(/feed/more) · 모두 확인했어요 · 빈 화면."""

import datetime as dt
import re

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Book, LibraryEntry, PointsLog, Room, RoomMember, Shelf
from app.models_posts import ReadingPost
from app.services import feed as feed_svc
from app.services import posts as psvc
from tests.conftest import signup
from tests.test_api import upstream
from tests.test_posts import done, relate, three


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    s = get_settings()
    for name in ("feature_posts", "feature_chat", "feature_goals", "feature_board"):
        monkeypatch.setattr(s, name, True)


NOW = dt.datetime.now(dt.UTC)


async def posts_at(session_factory, author: int, hours: list[float], visibility="all", tag="p") -> list[int]:
    """hours 시간 전에 올린 게시물들(순서대로 id 가 커지게 오래된 것부터 넣는다)."""
    ids = []
    async with session_factory() as s:
        for i, h in enumerate(sorted(hours, reverse=True)):
            at = NOW - dt.timedelta(hours=h)
            p = ReadingPost(
                author_id=author,
                book_source="store",
                external_id=f"{tag}{author}-{i}",
                book_title=f"책 {tag}{i}",
                status="done",
                caption="",
                visibility=visibility,
                post_day=at.date().isoformat(),
                created_at=at,
            )
            s.add(p)
            await s.flush()
            ids.append(p.id)
        await s.commit()
    return ids


async def finished(session_factory, user: int, title: str, hours: float, *, upload=False, owner=None) -> int:
    async with session_factory() as s:
        b = Book(
            source="upload" if upload else "store",
            external_id=f"x-{title}",
            title=title,
            author="누군가",
            owner_id=owner,
        )
        s.add(b)
        await s.flush()
        s.add(
            LibraryEntry(
                user_id=user, book_id=b.id, shelf=Shelf.DONE, updated_at=NOW - dt.timedelta(hours=hours)
            )
        )
        await s.commit()
        return b.id


async def acts_html(c) -> list[str]:
    return re.findall(r'data-act="(\w+)"', (await c.get("/community")).text)


# ---------------------------------------------------------------- 머리 · 동그라미 · 작성 칸


async def test_community_header_stories_and_no_composer(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        await posts_at(session_factory, i_f, [2])  # 친구 민준: 2시간 전 → 고리
        async with session_factory() as s:
            b = Book(source="store", external_id="r1", title="메밀꽃 필 무렵", author="이효석")
            s.add(b)
            await s.flush()
            s.add(LibraryEntry(user_id=ia, book_id=b.id, shelf=Shelf.READING))
            await s.commit()
            bid = b.id
        html = (await a.get("/community")).text
        assert '<h1 class="top-title">커뮤니티</h1>' in html and "‹ 내 프로필" not in html
        stories = html.split("data-stories", 1)[1].split("</ul>", 1)[0]
        assert stories.index("내 기록") < stories.index("민준")
        assert re.search(rf'data-story="{i_f}"[^>]*data-ring', stories)
        assert f'data-story="{ix}"' not in stories  # 친구가 아니면 동그라미도 없다
        # '내 기록 +' 동그라미가 새 게시물 — 따로 만드는 칸(작성 칸 · 책 칩 · 머리 단추)은 없다
        me = stories.split('class="ps-story-me"', 1)[1].split("</li>", 1)[0]
        assert 'href="/posts/new"' in me and "ps-story-plus" in me
        assert "무엇을 읽고 있나요?" not in html and "ps-composer" not in html and "ps-chip" not in html
        assert f"/posts/new?book=b%3A{bid}" not in html  # 책 칩도 없다
        top = html.split('<header class="top">', 1)[1].split("</header>", 1)[0]
        assert "/posts/new" not in top and 'href="/chat' not in top and 'href="/together' not in top  # 갈래 아이콘뿐
        assert "ps-top-plus" not in html and "data-together" not in html and 'id="psTogether"' not in html
        assert html.count('href="/posts/new"') == 1  # 만들기 입구는 '내 기록 +' 하나
        # 책을 고른 채로 여는 주소는 그대로 동작한다
        page = (await a.get(f"/posts/new?book=b:{bid}&status=reading")).text
        assert re.search(rf'value="b:{bid}"[^>]*checked', page)


async def test_new_user_empty_state(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "new@example.com", "새사람")
        html = (await c.get("/community")).text
        assert "친구가 올린 읽은 책이 여기에 모여요" in html
        for href, label in (
            ("/friends", "친구 찾기"),
            ("/posts/new", "첫 기록 올리기"),
            ("/community/together", "모집 글 보기"),
        ):
            assert f'href="{href}">{label}<' in html


# ---------------------------------------------------------------- 활동 소식


async def test_activity_privacy_and_flags(api, session_factory, monkeypatch):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        await posts_at(session_factory, ia, [1, 2, 3, 4, 5, 6, 7, 8])
        await finished(session_factory, i_f, "메밀꽃 필 무렵", 3.5)  # 친구 · 공개 책 → 보인다
        await finished(
            session_factory, i_f, "비밀 원고", 2.5, upload=True, owner=i_f
        )  # 개인 파일 → 절대 아님
        await finished(session_factory, ix, "모르는 사람 책", 1.5)  # 친구가 아님
        async with session_factory() as s:
            s.add(
                PointsLog(
                    user_id=i_f,
                    source="streak",
                    amount=30,
                    ref_id=NOW.date().isoformat(),
                    created_at=NOW - dt.timedelta(hours=6.5),
                )
            )
            await s.commit()
        async with session_factory() as s:
            acts = await feed_svc.activities(
                s, ia, NOW - dt.timedelta(days=1), NOW + dt.timedelta(minutes=1), 10
            )
        kinds = [(a_.kind, a_.book) for a_ in acts]
        assert ("done", "메밀꽃 필 무렵") in kinds and ("streak", None) in kinds
        assert all(b not in ("비밀 원고", "모르는 사람 책") for _, b in kinds)
        html = (await a.get("/community")).text
        assert (
            "「메밀꽃 필 무렵」을 다 읽었어요" in html
            and "비밀 원고" not in html
            and "모르는 사람 책" not in html
        )
        # 목표 기능이 꺼지면 연속 읽기 소식도 없다
        monkeypatch.setattr(get_settings(), "feature_goals", False)
        async with session_factory() as s:
            acts = await feed_svc.activities(
                s, ia, NOW - dt.timedelta(days=1), NOW + dt.timedelta(minutes=1), 10
            )
        assert "streak" not in [a_.kind for a_ in acts]
        # 차단하면(누가 막았든) 그 친구의 소식은 사라진다
        async with session_factory() as s:
            from app.models_chat import Friendship

            fr = await s.scalar(select(Friendship))
            fr.status, fr.high_blocks = "blocked", True
            await s.commit()
        async with session_factory() as s:
            assert await feed_svc.activities(s, ia, NOW - dt.timedelta(days=1), NOW, 10) == []


async def test_race_first_finisher_and_recruit(api, session_factory, monkeypatch):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    await relate(session_factory, ia, ix)  # 서연도 친구
    async with done(a, f, x):
        async with session_factory() as s:
            book = Book(
                source="store", external_id="rb", title="운수 좋은 날", author="현진건", total_chunks=3
            )
            s.add(book)
            await s.flush()
            room = Room(book_id=book.id, owner_id=i_f, name="함께 방", invite_code="FEED01")
            s.add(room)
            await s.flush()
            s.add(RoomMember(room_id=room.id, user_id=i_f))
            s.add(
                PointsLog(
                    user_id=ix,
                    source="race_finish",
                    amount=20,
                    ref_id=str(room.id),
                    created_at=NOW - dt.timedelta(hours=5),
                )
            )
            s.add(
                PointsLog(
                    user_id=i_f,
                    source="race_finish",
                    amount=20,
                    ref_id=str(room.id),
                    created_at=NOW - dt.timedelta(hours=4),
                )
            )
            from app.models_board import RecruitPost

            room2 = Room(book_id=book.id, owner_id=ix, name="모집 방", invite_code="FEED02")
            s.add(room2)
            await s.flush()
            s.add(
                RecruitPost(
                    room_id=room2.id,
                    author_id=ix,
                    book_id=book.id,
                    pitch="같이 읽어요",
                    created_at=NOW - dt.timedelta(hours=3),
                )
            )
            await s.commit()
        async with session_factory() as s:
            acts = await feed_svc.activities(s, ia, NOW - dt.timedelta(days=1), NOW, 10)
        race = [a_ for a_ in acts if a_.kind == "race"]
        assert [a_.user.id for a_ in race] == [ix]  # 가장 먼저 완주한 사람만 1등
        assert race[0].href == f"/u/{ix}"  # 나는 그 방 사람이 아니니 방으로 보내지 않는다
        rec = [a_ for a_ in acts if a_.kind == "recruit"]
        assert rec and rec[0].href.startswith("/together/") and rec[0].action == "참가"
        monkeypatch.setattr(get_settings(), "feature_board", False)
        async with session_factory() as s:
            acts = await feed_svc.activities(s, ia, NOW - dt.timedelta(days=1), NOW, 10)
        assert "recruit" not in [a_.kind for a_ in acts]


async def test_at_most_one_activity_per_four_posts(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        await posts_at(session_factory, i_f, [1 + i for i in range(6)])  # 6개
        for i in range(5):
            await finished(session_factory, i_f, f"다 읽은 {i}", 1.5 + i)
        acts = await acts_html(a)
        assert len(acts) == 1  # 6 // 4
        html = (await a.get("/community")).text
        order = re.findall(r'id="post-\d+"|data-act="', html)
        assert order.index('data-act="') == 4  # 네 번째 게시물 뒤
        await posts_at(session_factory, i_f, [0.5, 0.6], tag="q")  # 8개 → 2개
        assert len(await acts_html(a)) == 2


async def test_activities_do_not_repeat_across_pages(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        await posts_at(session_factory, i_f, [i * 2 + 1 for i in range(psvc.FEED_PAGE + 8)])
        for i in range(8):
            await finished(session_factory, i_f, f"책장 {i}", i * 4 + 2)
        first = (await a.get("/community?tab=friends")).text
        nxt = re.search(r'data-next-page="([^"]+)"', first).group(1).replace("&amp;", "&")
        assert nxt.startswith("/feed/more?tab=friends&before=")
        more = (await a.get(nxt)).text
        assert "<html" not in more and 'id="post-' in more
        seen1 = re.findall(r"「(책장 \d)」", first)
        seen2 = re.findall(r"「(책장 \d)」", more)
        assert seen1 and not set(seen1) & set(seen2)
        ids1 = re.findall(r'id="post-(\d+)"', first)
        ids2 = re.findall(r'id="post-(\d+)"', more)
        assert not set(ids1) & set(ids2) and len(ids1) == psvc.FEED_PAGE


# ---------------------------------------------------------------- 새 게시물 알약


async def test_new_count_and_fresh(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        first = (await posts_at(session_factory, i_f, [5]))[0]
        assert (await a.get(f"/feed/new?after={first}")).json() == {"count": 0}
        await posts_at(session_factory, i_f, [0.2, 0.1], tag="n")  # 친구 둘
        await posts_at(
            session_factory, i_f, [0.15], visibility="friends", tag="nf"
        )  # 친구만 — 친구니까 보인다
        await posts_at(session_factory, ia, [0.05], tag="me")  # 내 글은 세지 않는다
        await posts_at(session_factory, ix, [0.05], tag="xx")  # 친구가 아님 — 전체 탭에서만 센다
        await posts_at(session_factory, ix, [0.04], visibility="friends", tag="xf")  # 친구가 아닌 사람의 친구만 글
        r = await a.get(f"/feed/new?after={first}&tab=friends")
        assert r.json() == {"count": 3} and r.headers["cache-control"] == "no-store"
        assert (await a.get(f"/feed/new?after={first}")).json() == {"count": 4}  # 전체(기본)
        assert (await x.get(f"/feed/new?after={first}&tab=friends")).json() == {"count": 0}
        assert (await a.get("/feed/new?after=0")).json() == {"count": 0}
        frag = (await a.get(f"/feed/fresh?after={first}&tab=friends")).text
        assert "<html" not in frag and len(re.findall(r'id="post-(\d+)"', frag)) == 4  # 친구 셋 + 내 글
        assert "추천 게시물" not in frag
        mixed = (await a.get(f"/feed/fresh?after={first}")).text
        ids = [int(i) for i in re.findall(r'id="post-(\d+)"', mixed)]
        assert len(ids) == 5 and len(set(ids)) == 5  # 친구 글 먼저, 새로운 사람 글은 뒤에
        assert mixed.index("추천 게시물") > mixed.index(f'id="post-{ids[3]}"')
        page = (await a.get("/community")).text
        assert "data-new-pill" in page and re.search(r'data-newest="\d+"', page)
        assert 'data-tab="mix"' in page


# ---------------------------------------------------------------- 모두 확인했어요 · 얇은 피드


async def test_caught_up_divider_before_first_old_post(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        await posts_at(session_factory, i_f, [5, 20, 100, 200])  # 둘은 3일 안, 둘은 그 전
        html = (await a.get("/community")).text
        order = re.findall(r'id="post-\d+"|모두 확인했어요', html.split("data-stream", 1)[1])
        assert order.index("모두 확인했어요") == 2 and order.count("모두 확인했어요") == 1
        assert "최근 3일 게시물을 다 봤어요" in html and "지난 게시물" in html


async def test_caught_up_at_end_when_all_recent(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        await posts_at(session_factory, i_f, [1, 2])
        html = (await a.get("/community")).text.split("data-stream", 1)[1]
        assert html.index("모두 확인했어요") > html.rindex('id="post-')


async def test_thin_feed_suggests_roommates_and_popular(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        async with session_factory() as s:
            book = Book(source="store", external_id="tb", title="같이 읽는 책")
            s.add(book)
            await s.flush()
            room = Room(book_id=book.id, owner_id=ia, name="토요일 방", invite_code="THIN01")
            s.add(room)
            await s.flush()
            s.add_all([RoomMember(room_id=room.id, user_id=ia), RoomMember(room_id=room.id, user_id=ix)])
            await s.commit()
        await posts_at(session_factory, ix, [2], tag="pop")
        html = (await a.get("/community?tab=friends")).text
        assert "함께 읽은 사람" in html and f'action="/friends/{ix}/request"' in html and "토요일 방" in html
        assert "요즘 인기 있는 게시물" in html


async def test_feed_requires_login_and_flag(api, session_factory, monkeypatch):
    async with api(upstream) as c:
        r = await c.get("/feed/new?after=1")
        assert r.status_code in (303, 401)
        await signup(c, "harin@example.com", "하린")
        monkeypatch.setattr(get_settings(), "feature_posts", False)
        for path in ("/feed", "/feed/new?after=1", "/feed/more", "/feed/fresh?after=1"):
            assert (await c.get(path)).status_code == 404, path


def test_josa():
    assert feed_svc.josa("메밀꽃 필 무렵") == "을" and feed_svc.josa("데미안") == "을"
    assert feed_svc.josa("어린 왕자") == "를" and feed_svc.josa("1984") == "을(를)"
