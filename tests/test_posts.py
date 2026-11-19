"""읽은 책 게시물(/posts/new · /p/{id} · /u/{id} · /feed) — 공개 범위 · 차단 · 좋아요 · 댓글 · 하루 한 번 규칙."""

import datetime as dt
import re
from contextlib import asynccontextmanager
from urllib.parse import unquote

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models import Book, LibraryEntry, Shelf, User
from app.models_chat import Friendship
from app.models_posts import PostComment, PostLike, PostReport, ReadingPost
from app.services import posts as svc
from tests.conftest import signup
from tests.test_api import upstream


def csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


async def token(c) -> str:
    return csrf((await c.get("/posts/new")).text)


@pytest.fixture(autouse=True)
def posts_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "feature_posts", True)


async def uid(session_factory, email: str) -> int:
    async with session_factory() as s:
        return await s.scalar(select(User.id).where(User.email == email))


async def add_book(
    session_factory, title="봄봄", ext=None, *, source="wikisource", owner_id=None, shelf=None, user_id=None
):
    async with session_factory() as s:
        b = Book(source=source, external_id=ext or title, title=title, author="김유정", owner_id=owner_id)
        s.add(b)
        await s.flush()
        if shelf and user_id:
            s.add(LibraryEntry(user_id=user_id, book_id=b.id, shelf=shelf))
        await s.commit()
        return b.id


async def relate(session_factory, a: int, b: int, status="accepted", blocker: int | None = None):
    low, high = sorted((a, b))
    async with session_factory() as s:
        f = Friendship(user_low=low, user_high=high, requester_id=a, status=status)
        if blocker is not None:
            f.low_blocks, f.high_blocks = blocker == low, blocker == high
        s.add(f)
        await s.commit()


async def post(c, book: str, **fields):
    data = {"csrf": await token(c), "book": book, "status": "done", "rating": "4", "caption": "좋았어요"}
    data["visibility"] = "all"
    data.update(fields)
    return await c.post("/posts", data=data)


def pid_of(r) -> int:
    assert r.status_code == 303, r.text
    return int(re.match(r"/p/(\d+)", r.headers["location"]).group(1))


@asynccontextmanager
async def done(*clients):
    """이미 쓰기 시작한 클라이언트들을 끝에 닫는다."""
    try:
        yield
    finally:
        for c in clients:
            await c.aclose()


async def three(api, session_factory):
    """글쓴이(하린) · 친구(민준) · 모르는 사람(서연). 하린과 민준은 친구."""
    a, f, x = api(upstream), api(upstream), api(upstream)
    await signup(a, "harin@example.com", "하린")
    await signup(f, "minjun@example.com", "민준")
    await signup(x, "seoyeon@example.com", "서연")
    ids = [
        await uid(session_factory, e)
        for e in ("harin@example.com", "minjun@example.com", "seoyeon@example.com")
    ]
    await relate(session_factory, ids[0], ids[1])
    return (a, f, x), ids


# ---------------------------------------------------------------- 만들기 · 고치기 · 지우기


async def test_create_edit_delete(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        me = await uid(session_factory, "harin@example.com")
        bid = await add_book(session_factory, shelf=Shelf.DONE, user_id=me)
        page = (await c.get("/posts/new")).text
        assert f'value="b:{bid}"' in page and "다 읽음" in page  # 서재의 다 읽은 책이 먼저 보인다
        r = await post(
            c, f"b:{bid}", caption="  봄날의 <b>소동</b>\n\n\n\n끝 ", quote="장인님! 인제 저", rating="5"
        )
        pid = pid_of(r)
        async with session_factory() as s:
            p = await s.get(ReadingPost, pid)
            assert (p.book_title, p.book_author, p.book_source, p.external_id, p.book_id) == (
                "봄봄",
                "김유정",
                "wikisource",
                "봄봄",
                bid,
            )
            assert (p.status, p.rating, p.visibility) == ("done", 5, "all")
            assert p.caption == "봄날의 <b>소동</b>\n\n끝"  # 앞뒤 공백 · 세 줄 넘는 빈 줄만 정리
        html = (await c.get(f"/p/{pid}")).text
        assert "&lt;b&gt;소동&lt;/b&gt;" in html and "<b>소동</b>" not in html
        assert "장인님! 인제 저" in html and "별점 5점" in html

        t = csrf(html)
        r = await c.post(
            f"/p/{pid}/edit",
            data={
                "csrf": t,
                "caption": "다시 읽고 고침",
                "quote": "",
                "visibility": "friends",
                "status": "done",
                "rating": "5",
            },
        )
        assert r.status_code == 303 and "msg=" in r.headers["location"]
        async with session_factory() as s:
            p = await s.get(ReadingPost, pid)
            assert (p.caption, p.quote, p.visibility) == ("다시 읽고 고침", None, "friends") and p.updated_at

        r = await c.post(f"/p/{pid}/delete", data={"csrf": t})
        assert r.status_code == 303 and r.headers["location"].startswith("/profile")
        assert (await c.get(f"/p/{pid}")).status_code == 404
        async with session_factory() as s:
            assert (await s.get(ReadingPost, pid)).deleted_at is not None  # 소프트 삭제


async def test_only_author_edits_or_deletes(api, session_factory):
    (a, f, _), _ = await three(api, session_factory)
    async with done(a, f):
        pid = pid_of(await post(a, svc.sign_pick(svc.BookPick("store", "123", "소년이 온다", "한강"))))
        t = await token(f)
        assert (
            await f.post(
                f"/p/{pid}/edit", data={"csrf": t, "caption": "x", "visibility": "all", "status": "done"}
            )
        ).status_code == 404
        assert (await f.post(f"/p/{pid}/delete", data={"csrf": t})).status_code == 404
        assert (await a.post(f"/p/{pid}/delete", data={"csrf": "wrong"})).status_code == 403  # CSRF


async def test_validation(api, session_factory):
    async with api(upstream) as c:
        await signup(c)
        book = svc.sign_pick(svc.BookPick("store", "1", "작별하지 않는다", "한강"))
        for fields, msg in [
            ({"rating": "6"}, "별점은 1~5개로"),
            ({"status": "want"}, "읽었어요 · 읽는 중"),
            ({"visibility": "public"}, "누구에게 보일지"),
            ({"caption": "가" * 2201}, "2200자까지"),
            ({"quote": "가" * 201}, "200자까지"),
        ]:
            r = await post(c, book, **fields)
            assert r.status_code == 400 and msg in r.text, fields
        assert (await post(c, "")).status_code == 400
        r = await post(c, book, rating="", caption="", quote="")  # 별점 · 글 없이 표지만도 된다
        pid = pid_of(r)
        async with session_factory() as s:
            p = await s.get(ReadingPost, pid)
            assert p.rating is None and p.caption == "" and p.quote is None


# ---------------------------------------------------------------- 개인 파일은 안 된다


async def test_private_upload_book_refused(api, session_factory):
    async with api(upstream) as c:
        await signup(c)
        me = await uid(session_factory, "reader@example.com")
        up = await add_book(
            session_factory, "비밀 일기", "u1", source="upload", owner_id=me, shelf=Shelf.DONE, user_id=me
        )
        page = (await c.get("/posts/new")).text
        assert "비밀 일기" not in page  # 서재 목록에도 나오지 않는다
        r = await post(c, f"b:{up}")
        assert r.status_code == 400 and "내가 올린 파일은" in r.text
        # 서명을 흉내 내거나 upload 출처를 끼워 넣은 값도 거절
        forged = svc.sign_pick(svc.BookPick("upload", "u1", "비밀 일기"))
        assert (await post(c, forged)).status_code == 400
        good = svc.sign_pick(svc.BookPick("store", "9", "흰", "한강"))
        tampered = good[:-4] + ("0000" if not good.endswith("0000") else "1111")
        assert (await post(c, tampered)).status_code == 400
        async with session_factory() as s:
            assert await s.scalar(select(func.count()).select_from(ReadingPost)) == 0


async def test_search_results_are_signed_snapshots(api, session_factory):
    async with api(upstream) as c:
        await signup(c)
        page = (await c.get("/posts/new", params={"q": "봄봄"})).text
        values = re.findall(r'name="book" value="(h:[^"]+)"', page)
        assert values, page
        pick = svc.unsign_pick(values[0].replace("&#34;", '"'))
        assert pick is not None and pick.title
        pid = pid_of(await post(c, values[0]))
        async with session_factory() as s:
            p = await s.get(ReadingPost, pid)
            assert (p.book_source, p.external_id, p.book_title) == (pick.source, pick.external_id, pick.title)


def test_cover_urls_are_limited():
    assert svc.safe_cover("https://image.aladin.co.kr/a.jpg") == "https://image.aladin.co.kr/a.jpg"
    assert svc.safe_cover("/static/covers/a.jpg") == "/static/covers/a.jpg"
    for bad in ("javascript:alert(1)", "http://x/a.jpg", "/static/../secret", "//evil.com/a.jpg", None):
        assert svc.safe_cover(bad) is None


# ---------------------------------------------------------------- 공개 범위 · 차단


async def test_visibility_self_friend_stranger(api, session_factory):
    (a, f, x), (ia, _if, ix) = await three(api, session_factory)
    async with done(a, f, x):
        open_ = pid_of(await post(a, svc.sign_pick(svc.BookPick("store", "1", "모두에게"))))
        closed = pid_of(
            await post(a, svc.sign_pick(svc.BookPick("store", "2", "친구에게")), visibility="friends")
        )
        assert (await a.get(f"/p/{closed}")).status_code == 200
        assert (await f.get(f"/p/{closed}")).status_code == 200
        assert (await x.get(f"/p/{closed}")).status_code == 404  # 있는지조차 모른다
        assert (await x.get(f"/p/{open_}")).status_code == 200
        # 모르는 사람은 친구만 게시물에 좋아요 · 댓글 · 신고도 못 한다
        t = await token(x)
        for path, data in (("like", {}), ("comments", {"body": "안녕"}), ("report", {"reason": "spam"})):
            assert (await x.post(f"/p/{closed}/{path}", data={"csrf": t, **data})).status_code == 404
        # 프로필 격자와 수도 보이는 것만
        mine = (await a.get(f"/u/{ia}")).text
        stranger = (await x.get(f"/u/{ia}")).text
        assert f"/p/{closed}" in mine and f"/p/{closed}" not in stranger and f"/p/{open_}" in stranger
        assert 'data-count="posts">1<' in stranger
        # 피드: 친구 탭에는 친구의 친구만 글도, 모두 탭에는 '가입한 모두' 글만
        ff = (await f.get("/community")).text
        assert f"post-{closed}" in ff and f"post-{open_}" in ff
        fa = (await f.get("/community?tab=all")).text  # 모두 = 탐색 격자
        assert f'href="/p/{open_}"' in fa and f'href="/p/{closed}"' not in fa
        xf = (await x.get("/community?tab=friends")).text
        assert f"post-{open_}" not in xf  # 친구가 아니면 친구 탭에 없다
        assert f'href="/p/{open_}"' in (await x.get("/community?tab=all")).text
    assert ix


@pytest.mark.parametrize("who_blocks", ["author", "viewer"])
async def test_blocked_users_see_nothing_of_each_other(api, session_factory, who_blocks):
    (a, _f, x), (ia, _if, ix) = await three(api, session_factory)
    async with done(a, x):
        pid = pid_of(await post(a, svc.sign_pick(svc.BookPick("store", "1", "모두에게"))))
        mine = pid_of(await post(x, svc.sign_pick(svc.BookPick("store", "2", "서연 글"))))
        t = await token(x)
        await x.post(f"/p/{pid}/comments", data={"csrf": t, "body": "차단 전 댓글"})
        await relate(session_factory, ia, ix, status="blocked", blocker=ia if who_blocks == "author" else ix)
        for c, other, other_post in ((x, ia, pid), (a, ix, mine)):
            assert (await c.get(f"/p/{other_post}")).status_code == 404
            assert (await c.get(f"/u/{other}")).status_code == 404
            assert f"post-{other_post}" not in (await c.get("/community?tab=all")).text
        assert "차단 전 댓글" not in (await a.get(f"/p/{pid}")).text  # 막힌 사람의 댓글도 안 보인다


# ---------------------------------------------------------------- 좋아요


async def test_like_is_one_per_user_and_counted(api, session_factory):
    (a, f, x), (ia, _if, _ix) = await three(api, session_factory)
    async with done(a, f, x):
        pid = pid_of(await post(a, svc.sign_pick(svc.BookPick("store", "1", "책"))))
        h = {"Accept": "application/json"}
        r = await f.post(f"/p/{pid}/like", data={"csrf": await token(f)}, headers=h)
        assert r.json() == {"liked": True, "count": 1}
        r = await x.post(f"/p/{pid}/like", data={"csrf": await token(x)}, headers=h)
        assert r.json() == {"liked": True, "count": 2}
        r = await f.post(f"/p/{pid}/like", data={"csrf": await token(f)}, headers=h)
        assert r.json() == {"liked": False, "count": 1}  # 다시 누르면 꺼진다
        r = await x.post(f"/p/{pid}/like", data={"csrf": await token(x), "back": "/feed?tab=all"})
        assert r.status_code == 303 and r.headers["location"].startswith("/feed?tab=all")
        r = await x.post(f"/p/{pid}/like", data={"csrf": await token(x), "back": "https://evil.com"})
        assert r.headers["location"].startswith(f"/p/{pid}")
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(PostLike)) == 1
        s.add(PostLike(post_id=pid, user_id=ia))
        await s.commit()
        s.add(PostLike(post_id=pid, user_id=ia))
        with pytest.raises(IntegrityError):  # DB 수준에서 1인 1개
            await s.commit()


async def test_like_rate_limited(api, session_factory, monkeypatch):
    monkeypatch.setattr(svc, "LIKE_LIMIT", 2)
    async with api(upstream) as c:
        await signup(c)
        pid = pid_of(await post(c, svc.sign_pick(svc.BookPick("store", "1", "책"))))
        h = {"Accept": "application/json"}
        t = await token(c)
        for _ in range(2):
            assert (await c.post(f"/p/{pid}/like", data={"csrf": t}, headers=h)).status_code == 200
        assert (await c.post(f"/p/{pid}/like", data={"csrf": t}, headers=h)).status_code == 429


# ---------------------------------------------------------------- 댓글


async def test_comment_rules_and_delete(api, session_factory):
    (a, f, x), _ = await three(api, session_factory)
    async with done(a, f, x):
        pid = pid_of(await post(a, svc.sign_pick(svc.BookPick("store", "1", "책"))))
        tf, tx = await token(f), await token(x)
        r = await f.post(f"/p/{pid}/comments", data={"csrf": tf, "body": "   "})
        assert "error=" in r.headers["location"] and "댓글을 적어" in unquote(r.headers["location"])
        r = await f.post(f"/p/{pid}/comments", data={"csrf": tf, "body": "가" * 301})
        assert "300자까지" in unquote(r.headers["location"])
        await f.post(f"/p/{pid}/comments", data={"csrf": tf, "body": "첫 댓글 ||범인은 집사||"})
        await x.post(f"/p/{pid}/comments", data={"csrf": tx, "body": "두 번째"})
        html = (await a.get(f"/p/{pid}")).text
        assert html.index("첫 댓글") < html.index("두 번째")  # 최신이 아래
        assert (
            '<span class="ps-veil-t">범인은 집사</span>' in html
            and "||" not in html.split("ps-cm-list")[1].split("</ul>")[0]
        )
        async with session_factory() as s:
            first, second = (await s.scalars(select(PostComment.id).order_by(PostComment.id))).all()
        assert (
            await x.post(f"/p/{pid}/comments/{first}/delete", data={"csrf": tx})
        ).status_code == 404  # 남의 댓글
        assert (
            await f.post(f"/p/{pid}/comments/{first}/delete", data={"csrf": tf})
        ).status_code == 303  # 내 댓글
        assert (
            await a.post(f"/p/{pid}/comments/{second}/delete", data={"csrf": await token(a)})
        ).status_code == 303  # 내 게시물의 댓글
        html = (await a.get(f"/p/{pid}")).text
        assert "첫 댓글" not in html and "두 번째" not in html
        async with session_factory() as s:
            assert (
                await s.scalar(select(func.count()).where(PostComment.deleted_at.is_not(None))) == 2
            )  # 소프트 삭제


async def test_comment_rate_limited(api, session_factory, monkeypatch):
    monkeypatch.setattr(svc, "COMMENT_LIMIT", 1)
    async with api(upstream) as c:
        await signup(c)
        pid = pid_of(await post(c, svc.sign_pick(svc.BookPick("store", "1", "책"))))
        t = await token(c)
        await c.post(f"/p/{pid}/comments", data={"csrf": t, "body": "하나"})
        r = await c.post(f"/p/{pid}/comments", data={"csrf": t, "body": "둘"})
        assert "너무 빨리" in unquote(r.headers["location"])


# ---------------------------------------------------------------- 도배 방지


async def test_one_post_per_book_per_day(api, session_factory, monkeypatch):
    now = {"t": dt.datetime(2026, 11, 18, 3, 0, tzinfo=dt.UTC)}  # 한국 12시
    monkeypatch.setattr(svc, "clock", lambda: now["t"])
    async with api(upstream) as c:
        await signup(c)
        book = svc.sign_pick(svc.BookPick("store", "1", "책"))
        pid_of(await post(c, book))
        r = await post(c, book, caption="또")
        assert r.status_code == 400 and "오늘 이미 올렸어요" in r.text
        pid_of(await post(c, svc.sign_pick(svc.BookPick("store", "2", "다른 책"))))  # 다른 책은 된다
        now["t"] += dt.timedelta(hours=14)  # 한국 다음 날 2시
        pid_of(await post(c, book, caption="다시 읽었어요"))


async def test_posting_rate_limited(api, session_factory, monkeypatch):
    monkeypatch.setattr(svc, "POST_LIMIT", 2)
    async with api(upstream) as c:
        await signup(c)
        for i in range(2):
            pid_of(await post(c, svc.sign_pick(svc.BookPick("store", str(i), f"책{i}"))))
        r = await post(c, svc.sign_pick(svc.BookPick("store", "9", "책9")))
        assert r.status_code == 429 and "너무 자주" in r.text


# ---------------------------------------------------------------- 피드 · 프로필


async def _seed_posts(session_factory, author_id: int, n: int, visibility="all") -> list[int]:
    ids = []
    async with session_factory() as s:
        for i in range(n):
            p = ReadingPost(
                author_id=author_id,
                book_source="store",
                external_id=f"{visibility}{i}",
                book_title=f"책 {i}",
                status="done",
                caption=f"글 {i}",
                visibility=visibility,
                post_day="2026-11-18",
            )
            s.add(p)
            await s.flush()
            ids.append(p.id)
        await s.commit()
    return ids


async def test_feed_newest_first_with_cursor_paging(api, session_factory):
    (a, f, _x), (ia, i_f, _ix) = await three(api, session_factory)
    async with done(a, f):
        ids = await _seed_posts(session_factory, ia, 7) + await _seed_posts(
            session_factory, i_f, 6, "friends"
        )
        html = (await a.get("/community?tab=friends")).text
        shown = [int(x) for x in re.findall(r'id="post-(\d+)"', html)]
        assert shown == sorted(ids, reverse=True)[: svc.FEED_PAGE]
        more = re.search(r'href="/community\?tab=friends&amp;before=(\d+)"', html)
        assert more and int(more.group(1)) == shown[-1]
        rest = [
            int(x)
            for x in re.findall(r'id="post-(\d+)"', (await a.get(f"/community?tab=friends&before={more.group(1)}")).text)
        ]
        assert rest == sorted(ids, reverse=True)[svc.FEED_PAGE :]
        assert "wide ps-more" not in (await a.get(f"/community?tab=friends&before={more.group(1)}")).text  # 마지막 쪽엔 '더 보기' 없음
        # 모두 탭(탐색 격자): 친구만 글은 빠진다
        all_ids = [
            int(x) for x in re.findall(r'class="ps-tile" href="/p/(\d+)"', (await a.get("/community?tab=all")).text)
        ]
        assert all_ids == sorted(ids[:7], reverse=True)


async def test_profile_counts_and_grid(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        await _seed_posts(session_factory, ia, 2)
        await _seed_posts(session_factory, ia, 1, "friends")
        await add_book(session_factory, "다 읽은 1", shelf=Shelf.DONE, user_id=ia)
        await add_book(session_factory, "다 읽은 2", shelf=Shelf.DONE, user_id=ia)
        await add_book(session_factory, "읽는 중", shelf=Shelf.READING, user_id=ia)
        await relate(session_factory, ix, ia, status="pending")  # 서연 → 하린 신청

        def counts(html):
            return [int(n) for n in re.findall(r'data-count="\w+">(\d+)<', html)]

        mine = (await a.get(f"/u/{ia}")).text
        assert counts(mine) == [3, 1, 2]  # 게시물 · 친구(수락된 것만) · 다 읽은 책
        assert "게시물 올리기" in mine and 'href="/me"' in mine and "ps-hl" in mine  # 내 프로필: + · 설정 · 동그라미
        friend = (await f.get(f"/u/{ia}")).text
        assert counts(friend) == [3, 1, 2] and "메시지" in friend and "ps-hl" not in friend  # 남의 프로필엔 작은 칸 없음
        stranger = (await x.get(f"/u/{ia}")).text
        assert counts(stranger) == [2, 1, 2] and "ps-hl" not in stranger
        assert (
            "친구 신청 받기" not in stranger and "친구 신청을 보냈어요" in stranger
        )  # 서연이 보낸 신청(대기)
        assert len(re.findall(r'class="ps-tile"', stranger)) == 2
        assert (await a.get("/u/999999")).status_code == 404
        r = await a.get("/u")
        assert r.status_code == 303 and r.headers["location"] == "/profile"
    assert i_f


async def test_profile_hides_chat_and_goals_when_off(api, session_factory, monkeypatch):
    (a, f, _x), (ia, _if, _ix) = await three(api, session_factory)
    monkeypatch.setattr(get_settings(), "feature_chat", False)
    monkeypatch.setattr(get_settings(), "feature_goals", False)
    async with done(a, f):
        html = (await f.get(f"/u/{ia}")).text
        assert "메시지" not in html and "친구 신청" not in html


# ---------------------------------------------------------------- 신고 · 관리자


async def test_report_row_and_admin_page(api, session_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "seoyeon@example.com")
    (a, f, x), _ = await three(api, session_factory)
    async with done(a, f, x):
        pid = pid_of(
            await post(a, svc.sign_pick(svc.BookPick("store", "1", "신고될 책")), caption="광고 문구")
        )
        assert "내 게시물은 신고할 수 없어요" in unquote(
            (await a.post(f"/p/{pid}/report", data={"csrf": await token(a), "reason": "spam"})).headers[
                "location"
            ]
        )
        r = await f.post(f"/p/{pid}/report", data={"csrf": await token(f), "reason": "spam"})
        assert r.status_code == 303 and "신고를 받았어요" in unquote(r.headers["location"])
        await f.post(f"/p/{pid}/report", data={"csrf": await token(f), "reason": "abuse"})  # 두 번째는 무시
        async with session_factory() as s:
            reps = (await s.scalars(select(PostReport))).all()
            assert [(r.post_id, r.reason, r.resolved) for r in reps] == [(pid, "spam", False)]
        assert (await f.get("/admin/posts")).status_code == 404  # 관리자가 아니면 없는 화면
        html = (await x.get("/admin/posts")).text
        assert "신고될 책" in html and "광고 문구" in html and "광고·도배 1" in html
        r = await x.post(f"/admin/posts/{pid}/hide", data={"csrf": csrf(html)})
        assert r.status_code == 303
        assert (await a.get(f"/p/{pid}")).status_code == 404  # 모두에게서 내려갔다
        async with session_factory() as s:
            assert (await s.scalar(select(PostReport.resolved))) is True


# ---------------------------------------------------------------- 로그인 · 기능 스위치


async def test_anonymous_refused(api, session_factory):
    async with api(upstream) as c:
        for path in ("/community", "/posts/new", "/u/1", "/p/1"):
            r = await c.get(path)
            assert r.status_code == 303 and r.headers["location"].startswith("/login"), path
        r = await c.post("/posts", data={"book": "b:1", "csrf": "x"})
        assert r.status_code in (303, 403)
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(ReadingPost)) == 0


async def test_flag_off_hides_everything(api, session_factory, monkeypatch):
    async with api(upstream) as c:
        await signup(c)
        me = await uid(session_factory, "reader@example.com")
        assert f'href="/u/{me}"' in (await c.get("/me")).text  # 켜져 있으면 /me 에 '내 프로필' 한 줄
        monkeypatch.setattr(get_settings(), "feature_posts", False)
        for path in ("/feed", "/posts/new", f"/u/{me}", "/p/1", "/admin/posts"):
            assert (await c.get(path)).status_code == 404, path
        assert (await c.post("/posts", data={"book": "b:1", "csrf": "x"})).status_code == 404
        me_html = (await c.get("/me")).text
        assert f'href="/u/{me}"' not in me_html and "내 프로필" not in me_html


# ---------------------------------------------------------------- 조각


def test_segments_split_veils():
    assert svc.segments("앞 ||가림|| 뒤") == [(False, "앞 "), (True, "가림"), (False, " 뒤")]
    assert svc.segments("||a||||b||") == [(True, "a"), (True, "b")]
    assert svc.segments("|| 열린 채") == [(False, "|| 열린 채")]
    assert svc.segments(None) == []


def test_when_is_relative_korean():
    now = dt.datetime(2026, 11, 18, 3, 0, tzinfo=dt.UTC)
    import app.services.posts as m

    orig = m.clock
    m.clock = lambda: now
    try:
        assert m.when(now - dt.timedelta(seconds=10)) == "방금"
        assert m.when(now - dt.timedelta(minutes=5)) == "5분 전"
        assert m.when(now - dt.timedelta(hours=2)) == "2시간 전"
        assert m.when(now - dt.timedelta(days=1)) == "어제"
        assert m.when(dt.datetime(2026, 3, 2, tzinfo=dt.UTC)) == "3월 2일"
        assert m.when(dt.datetime(2025, 3, 2, tzinfo=dt.UTC)) == "2025년 3월 2일"
    finally:
        m.clock = orig


async def test_avatar_photo_only_where_avatars_route_allows(api, session_factory):
    """/avatars 는 본인 · 같은 대결방 사람에게만 사진을 준다 — 그 밖에는 깨진 그림 대신 이름 첫 글자."""
    (a, _f, x), (ia, _if, _ix) = await three(api, session_factory)
    async with session_factory() as s:
        (await s.get(User, ia)).avatar = b"\xff\xd8fake-jpeg"
        await s.commit()
    async with done(a, x):
        pid = pid_of(await post(a, svc.sign_pick(svc.BookPick("store", "1", "책"))))
        assert f"/avatars/{ia}?v=" in (await a.get(f"/p/{pid}")).text
        for path in (f"/p/{pid}", f"/u/{ia}", "/feed?tab=all"):
            assert f"/avatars/{ia}?v=" not in (await x.get(path)).text, path
