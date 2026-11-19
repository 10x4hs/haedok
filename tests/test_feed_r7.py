"""라운드 7 — 피드 = 소셜 허브.

- 전체 탭(기본): 친구 글 먼저(최신순), 친구 3개마다 '새로운 사람'의 공개 글 1개, 친구 글이 끝나면 그 뒤로.
  차단 · 친구가 아닌 사람의 친구만 글 · 내 글은 새로운 사람 갈래에 없다. 쪽 · 알약 사이에 겹침 없음.
- (머리 단추는 라운드 8에서 커뮤니티 갈래로 바뀌었다 — tests/test_community.py)
- 제안 모듈: 플래그 · 공개 범위 · 하루 숨김(쿠키) · 비면 그리지 않음 · 활동 소식과 붙지 않음.
"""

import re

import pytest

from app.core.config import get_settings
from app.models import Book, LibraryEntry, Room, RoomMember, Shelf
from app.models_posts import PostLike
from app.services import feed as feed_svc
from app.services import posts as psvc
from tests.conftest import signup
from tests.test_api import upstream
from tests.test_feed import finished, posts_at
from tests.test_posts import csrf, done, relate, three, uid


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    s = get_settings()
    for name in ("feature_posts", "feature_chat", "feature_goals", "feature_board"):
        monkeypatch.setattr(s, name, True)


def stream_ids(html: str) -> list[int]:
    return [int(i) for i in re.findall(r'id="post-(\d+)"', html)]


def article(html: str, pid: int) -> str:
    return html.split(f'id="post-{pid}"', 1)[1].split("</article>", 1)[0]


def stream_kinds(html: str) -> list[str]:
    """흐름 안 칸의 차례 — post · act · mod."""
    body = html.split("data-stream", 1)[1]
    return [
        "post" if m.startswith("id=") else "act" if m.startswith("data-act") else "mod"
        for m in re.findall(r'id="post-\d+"|data-act="\w+"|data-mod="\w+"', body)
    ]


async def fourth(api, session_factory, email="dohyun@example.com", nick="도현"):
    c = api(upstream)
    await signup(c, email, nick)
    return c, await uid(session_factory, email)


async def recruit_room(session_factory, host: int, title="같이 읽는 책", members=(), *, upload_owner=None):
    """host 가 연 모집 중인 방(공개 모집 글) — members 도 그 방에 들어 있다."""
    from app.models_board import RecruitPost

    async with session_factory() as s:
        book = Book(
            source="upload" if upload_owner else "store",
            external_id=f"rc-{title}",
            title=title,
            author="누군가",
            owner_id=upload_owner,
        )
        s.add(book)
        await s.flush()
        room = Room(book_id=book.id, owner_id=host, name=f"{title} 방", invite_code=f"R{book.id:05d}")
        s.add(room)
        await s.flush()
        for m in (host, *members):
            s.add(RoomMember(room_id=room.id, user_id=m))
        s.add(RecruitPost(room_id=room.id, author_id=host, book_id=book.id, pitch="같이 읽어요", capacity=6))
        await s.commit()
        return book.id


async def reading(session_factory, user: int, book_id: int, shelf=Shelf.READING):
    async with session_factory() as s:
        s.add(LibraryEntry(user_id=user, book_id=book_id, shelf=shelf))
        await s.commit()


# ---------------------------------------------------------------- 전체 탭: 차례 · 제외


async def test_default_tab_friends_first_then_new_people(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    b, ib = await fourth(api, session_factory)
    await relate(session_factory, ia, ib, status="blocked", blocker=ib)  # 도현이 하린을 막았다
    async with done(a, f, x, b):
        friend = await posts_at(session_factory, i_f, [30, 20, 10, 5, 4], tag="f")
        friend_closed = await posts_at(session_factory, i_f, [3], visibility="friends", tag="fc")
        other = await posts_at(session_factory, ix, [2, 1], tag="o")
        other_closed = await posts_at(session_factory, ix, [0.5], visibility="friends", tag="oc")
        blocked = await posts_at(session_factory, ib, [0.4], tag="b")
        mine = await posts_at(session_factory, ia, [0.3], tag="m")
        html = (await a.get("/community")).text
        assert 'aria-current="page">전체<' in html
        ids = stream_ids(html)
        friend_lane = sorted(friend + friend_closed + mine, reverse=True)
        # 친구(와 내) 글 3개 → 새로운 사람 1개 → 친구 3개 → 새로운 사람 1개(남은 것) → 남은 친구 글
        assert ids[:3] == friend_lane[:3] and ids[4:7] == friend_lane[3:6]
        assert {ids[3], ids[7]} == set(other) and ids[8:] == friend_lane[6:]
        assert not set(ids) & set(other_closed + blocked)  # 친구가 아닌 사람의 친구만 글 · 차단
        assert ids.count(mine[0]) == 1  # 내 글은 친구 갈래에서 한 번만(새로운 사람 갈래에는 없다)
        for pid in other:
            head = article(html, pid)
            assert "새로운 사람" in head and f'action="/friends/{ix}/request"' in head
        for pid in friend + mine:
            head = article(html, pid)
            assert "새로운 사람" not in head and "/request" not in head  # 이미 친구 · 나에게는 친구 신청 없음
        # 친구 탭 = 친구(와 나)만
        only = stream_ids((await a.get("/community?tab=friends")).text)
        assert only == friend_lane[: psvc.FEED_PAGE]
        # 탐색 격자는 그대로
        assert 'class="ps-explore"' in (await a.get("/community?tab=all")).text


async def test_new_people_liked_first_within_batch_and_request_state(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        old, new = await posts_at(session_factory, ix, [5, 1], tag="o")
        async with session_factory() as s:
            s.add(PostLike(post_id=old, user_id=i_f))
            await s.commit()
        ids = stream_ids((await a.get("/community")).text)
        assert ids == [old, new]  # 친구 글이 없으면 새로운 사람 글만 — 같은 묶음에선 좋아요가 먼저
        # 이미 신청했으면 버튼 대신 '신청함'
        await relate(session_factory, ia, ix, status="pending")
        html = (await a.get("/community")).text
        assert "신청함" in article(html, old) and "/request" not in article(html, old)
        # 오래된 주소 ?tab=rec 은 전체로
        assert stream_ids((await a.get("/community?tab=rec")).text) == [old, new]


async def test_mixed_paging_and_pill_never_duplicate(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        friend = await posts_at(session_factory, i_f, [i + 1 for i in range(13)], tag="f")
        other = await posts_at(session_factory, ix, [i + 0.5 for i in range(9)], tag="o")
        async with session_factory() as s:  # 좋아요로 묶음 안의 차례를 흔든다
            for pid in other[::3]:
                s.add(PostLike(post_id=pid, user_id=i_f))
            await s.commit()
        html = (await a.get("/community")).text
        newest = int(re.search(r'data-tab="mix" data-newest="(\d+)"', html).group(1))
        seen = stream_ids(html)
        assert len(seen) == psvc.FEED_PAGE
        pages = 1
        nxt = re.search(r'data-next-page="([^"]+)"', html)
        while nxt:
            frag = (await a.get(nxt.group(1).replace("&amp;", "&"))).text
            assert "<html" not in frag
            seen += stream_ids(frag)
            nxt = re.search(r'data-next-page="([^"]+)"', frag)
            pages += 1
            assert pages < 10
        assert len(seen) == len(set(seen))  # 쪽 사이에 겹침 없음
        assert set(seen) == set(friend + other)  # 빠진 것도 없음
        # 없는 쪽 주소를 손으로 넣어도 깨지지 않는다
        assert (await a.get("/feed/more?cur=garbage")).status_code == 200
        # 알약: 새로 올라온 것만, 이미 본 것과 겹치지 않게
        fresh_f = await posts_at(session_factory, i_f, [0.1], tag="nf")
        fresh_o = await posts_at(session_factory, ix, [0.05], tag="no")
        assert (await a.get(f"/feed/new?after={newest}")).json() == {"count": 2}
        got = stream_ids((await a.get(f"/feed/fresh?after={newest}")).text)
        assert got == fresh_f + fresh_o and not set(got) & set(seen)


# ---------------------------------------------------------------- 제안 모듈


async def test_recruit_module_flags_dismiss_and_spacing(api, session_factory, monkeypatch):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        await posts_at(session_factory, i_f, [i + 1 for i in range(psvc.FEED_PAGE)])
        for i in range(4):
            await finished(session_factory, i_f, f"다 읽은 {i}", i + 1.5)
        await recruit_room(session_factory, ix, "모두의 책")
        await recruit_room(session_factory, ix, "비밀 원고", upload_owner=ix)  # 개인 파일 책 → 절대 아님
        html = (await a.get("/community")).text
        kinds = stream_kinds(html)
        assert kinds.count("mod") == 1 and kinds.index("mod") == 7  # 6번째 게시물 뒤(4번째 뒤엔 활동 소식)
        assert all(not (p != "post" and q != "post") for p, q in zip(kinds, kinds[1:], strict=False))  # 둘이 붙지 않는다
        mod = html.split('data-mod="recruit"', 1)[1].split("</section>", 1)[0]
        assert "같이 읽을 사람 찾기" in html and "모두의 책" in mod and "비밀 원고" not in mod
        assert 'href="/together/new"' in mod
        # × : 하루 숨김(쿠키)
        token = csrf(html)
        r = await a.post("/feed/hide/recruit", data={"csrf": token}, headers={"Accept": "application/json"})
        assert r.status_code == 200 and "feed_hide_recruit=1" in r.headers["set-cookie"]
        assert "Max-Age=86400" in r.headers["set-cookie"]
        assert 'data-mod="recruit"' not in (await a.get("/community")).text
        assert (await a.post("/feed/hide/nope", data={"csrf": token})).status_code == 404
        assert (await a.post("/feed/hide/people", data={"csrf": "bad"})).status_code in (400, 403)
        a.cookies.delete("feed_hide_recruit")
        assert 'data-mod="recruit"' in (await a.get("/community")).text
        # 게시판 기능이 꺼지면 모듈도 없다(다른 모듈이 비었으면 아무것도 그리지 않는다)
        monkeypatch.setattr(get_settings(), "feature_board", False)
        assert "data-mod=" not in (await a.get("/community")).text


async def test_readers_module_privacy(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    b, ib = await fourth(api, session_factory)
    async with done(a, f, x, b):
        await posts_at(session_factory, i_f, [i + 1 for i in range(8)])
        async with session_factory() as s:
            book = Book(source="store", external_id="rd", title="소년이 온다", author="한강")
            secret = Book(source="upload", external_id="sec", title="비밀 일기", owner_id=ia)
            s.add_all([book, secret])
            await s.flush()
            bid, sid = book.id, secret.id
            await s.commit()
        await reading(session_factory, ia, bid, Shelf.DONE)  # 내가 다 읽은 책
        await reading(session_factory, ia, sid)  # 개인 파일 — 절대 쓰지 않는다
        await reading(session_factory, i_f, bid)  # 친구 → 메시지
        await reading(session_factory, ix, bid)  # 친구가 아니고 공개 모집 방에도 없다 → 안 보인다
        await reading(session_factory, i_f, sid)
        html = (await a.get("/community")).text
        mod = html.split('data-mod="readers"', 1)[1].split("</section>", 1)[0]
        assert "이 책 읽는 사람들" in html and "소년이 온다" in mod and "비밀 일기" not in html
        assert f'href="/chat/dm/{i_f}">메시지<' in mod  # 친구에게만 메시지
        assert f"/u/{ix}" not in mod
        # 공개 모집 방에 들어 있으면 보인다 — 친구가 아니니 메시지 대신 친구 신청
        await recruit_room(session_factory, ib, "다른 책", members=(ix,))
        html = (await a.get("/community", cookies={"feed_hide_recruit": "1"})).text
        mod = html.split('data-mod="readers"', 1)[1].split("</section>", 1)[0]
        assert f"/u/{ix}" in mod and f"/chat/dm/{ix}" not in mod and f'action="/friends/{ix}/request"' in mod
        assert f'action="/friends/{i_f}/request"' not in mod  # 이미 친구에게는 친구 신청 없음
        # 모집 모듈이 고른 자리라면 넓은 화면용 대신 모듈로 같이 그린다(오른쪽 칸과 겹치지 않게, CSS 로 하나만 보임)
        both = (await a.get("/community")).text
        assert 'class="ps-mod k-recruit"' in both and 'class="ps-mod k-readers ps-mod-alt"' in both
        # 차단하면 빠진다
        await relate(session_factory, ia, ix, status="blocked", blocker=ia)
        html = (await a.get("/community", cookies={"feed_hide_recruit": "1"})).text
        mod = html.split('data-mod="readers"', 1)[1].split("</section>", 1)[0]
        assert f"/u/{ix}" not in mod


async def test_people_module_from_my_rooms(api, session_factory, monkeypatch):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        await posts_at(session_factory, i_f, [i + 1 for i in range(8)])
        async with session_factory() as s:
            book = Book(source="store", external_id="pp", title="같이 읽은 책")
            s.add(book)
            await s.flush()
            room = Room(book_id=book.id, owner_id=ia, name="토요일 방", invite_code="PPL001")
            s.add(room)
            await s.flush()
            for m in (ia, ix, i_f):
                s.add(RoomMember(room_id=room.id, user_id=m))
            await s.commit()
        html = (await a.get("/community", cookies={"feed_hide_recruit": "1", "feed_hide_readers": "1"})).text
        mod = html.split('data-mod="people"', 1)[1].split("</section>", 1)[0]
        assert "알 수도 있는 사람" in html and "토요일 방에서 함께" in mod
        assert f'action="/friends/{ix}/request"' in mod and f"/u/{i_f}" not in mod  # 친구는 후보가 아니다
        # 친구 기능이 꺼지면 사람 모듈은 없다
        monkeypatch.setattr(get_settings(), "feature_chat", False)
        html = (await a.get("/community", cookies={"feed_hide_recruit": "1", "feed_hide_readers": "1"})).text
        assert 'data-mod="people"' not in html


async def test_modules_absent_when_empty_and_side_column(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        await posts_at(session_factory, i_f, [i + 1 for i in range(8)])
        html = (await a.get("/community")).text
        assert "data-mod=" not in html  # 보여 줄 것이 없으면 그리지 않는다
        side = html.split('class="ps-home-side"', 1)[1].split("</aside>", 1)[0]
        assert 'href="/profile"' in side and "ps-side-rc" not in side and "바닥글" in side
        for i in range(4):
            await recruit_room(session_factory, ix, f"모집 책 {i}")
        html = (await a.get("/community")).text
        side = html.split('class="ps-home-side"', 1)[1].split("</aside>", 1)[0]
        assert side.count('class="ps-side-rc"') == 3  # 같이 읽을 사람 셋
        assert "has-side-rc" in html  # 넓은 화면에선 흐름 안의 같은 모듈을 숨긴다(CSS)
        assert "알 수도 있는 사람" not in side and "인기" not in side  # 오른쪽 칸에 다른 모듈은 없다


def test_cursor_round_trip():
    assert feed_svc.parse_cursor(feed_svc.make_cursor(12, 7, False, False, 2)) == (12, 7, False, False, 2)
    assert feed_svc.parse_cursor(feed_svc.make_cursor(None, 7, True, False, 1)) == (None, 7, True, False, 1)
    assert feed_svc.parse_cursor("junk") == (None, None, False, False, 0)
