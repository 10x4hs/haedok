"""라운드 5 — 인스타그램처럼: 사진 1~5장 올리기(EXIF·GPS 제거) · 사진 주소의 공개 범위 · 저장(책갈피) ·
댓글 좋아요 · 답글 · 신고 · 친구에게 보내기(DM) · 소개 · 탐색 격자 · 하루 사진 한도."""

import re

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import undefer

from app.core.config import get_settings
from app.models_chat import ChatMessage
from app.models_posts import (
    PostComment,
    PostCommentLike,
    PostCommentReport,
    PostPhoto,
    PostSave,
    ReadingPost,
)
from app.services import post_photos as pp
from app.services import posts as svc
from tests.conftest import signup
from tests.test_api import upstream
from tests.test_post_photos import TINY, with_metadata
from tests.test_posts import done, pid_of, relate, three, token, uid


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    s = get_settings()
    for name in ("feature_posts", "feature_chat"):
        monkeypatch.setattr(s, name, True)


def book(i: int | str = 1) -> str:
    return svc.sign_pick(svc.BookPick("store", f"ig-{i}", f"사진책{i}", "지은이"))


async def upload(c, photos: list[bytes], **fields):
    data = {"csrf": await token(c), "book": book(fields.pop("n", 1)), "status": "done", "rating": "5"}
    data.update({"caption": "카페에서", "visibility": "all", "aspect": "4:5"})
    data.update(fields)
    files = [("photos", (f"p{i}.jpg", b, "image/jpeg")) for i, b in enumerate(photos)]
    return await c.post("/posts", data=data, files=files or None)


def variant(k: int) -> bytes:
    """같은 그림이지만 서로 다른 바이트(순서 확인용) — COM 조각은 저장 전에 걷혀서 APP0 의 밀도로 구분한다."""
    raw = bytearray(TINY)
    raw[14:16] = k.to_bytes(2, "big")  # JFIF X density
    return bytes(raw)


async def photos_in_db(session_factory, pid: int) -> list[PostPhoto]:
    async with session_factory() as s:
        rows = await s.scalars(
            select(PostPhoto)
            .options(undefer(PostPhoto.data))
            .where(PostPhoto.post_id == pid)
            .order_by(PostPhoto.idx)
        )
        return list(rows)


# ---------------------------------------------------------------- 올리기


async def test_upload_three_photos_in_order_and_strip_exif(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        shots = [with_metadata(), variant(2), variant(3)]
        pid = pid_of(await upload(c, shots))
        rows = await photos_in_db(session_factory, pid)
        assert [r.idx for r in rows] == [0, 1, 2]
        assert [(r.width, r.height) for r in rows] == [(16, 12)] * 3
        for r in rows:
            assert b"\xff\xe1" not in r.data and b"GPS" not in r.data and b"Exif" not in r.data
            assert r.data.startswith(b"\xff\xd8") and r.data.endswith(b"\xff\xd9")
        assert rows[1].data[14:16] == (2).to_bytes(2, "big") and rows[2].data[14:16] == (3).to_bytes(2, "big")
        # 피드 · 게시물 · 프로필 격자에 사진 주소(내용 해시 ?v=)가 순서대로 보인다
        html = (await c.get(f"/p/{pid}")).text
        urls = re.findall(rf"/media/p/{pid}/(\d)\.jpg\?v=([0-9a-f]{{16}})", html)
        assert [u[0] for u in urls[:3]] == ["0", "1", "2"]
        assert urls[0][1] == rows[0].digest
        assert "<span data-car-now>1</span>/3" in html and 'aria-label="다음 사진"' in html  # 넘김 표시
        grid = (await c.get("/profile")).text
        assert f"/media/p/{pid}/0.jpg" in grid and "ps-tile-mark" in grid  # 첫 사진 + 여러 장 표시
        # 받은 바이트 = 걷어 낸 바이트, 오래 캐시
        r = await c.get(f"/media/p/{pid}/0.jpg?v={rows[0].digest}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
        assert r.content == rows[0].data and b"GPS" not in r.content
        assert (await c.get(f"/media/p/{pid}/3.jpg")).status_code == 404
        assert (await c.get(f"/media/p/{pid}/9.jpg")).status_code == 404


async def test_upload_without_photos_uses_cover_and_book_is_required(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        pid = pid_of(await upload(c, []))
        assert await photos_in_db(session_factory, pid) == []
        assert "ps-art" in (await c.get(f"/p/{pid}")).text  # 표지 그림
        r = await upload(c, [TINY], book="", n=2)
        assert r.status_code == 400 and "올릴 책을 골라 주세요" in r.text


async def test_upload_rejections(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        r = await upload(c, [png])
        assert r.status_code == 400 and "JPEG 사진만" in r.text
        big = TINY[:2] + (b"\xff\xe1\xff\xff" + b"x" * 65533) * 7 + TINY[2:]
        assert len(big) > pp.MAX_BYTES
        r = await upload(c, [big])
        assert r.status_code == 400 and "400 KB" in r.text
        r = await upload(c, [variant(i) for i in range(6)])
        assert r.status_code == 400 and "5장까지" in r.text
        r = await upload(c, [TINY[:300]])
        assert r.status_code == 400 and "읽지 못했어요" in r.text
        # JSON 으로 물으면(JS 의 올리기) JSON 으로 답한다
        r = await c.post(
            "/posts",
            data={"csrf": await token(c), "book": book(), "visibility": "all"},
            files=[("photos", ("x.png", png, "image/png"))],
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 400 and r.json()["detail"].startswith("JPEG")
        async with session_factory() as s:
            assert await s.scalar(select(func.count()).select_from(ReadingPost)) == 0
            assert await s.scalar(select(func.count()).select_from(PostPhoto)) == 0
        r = await c.post(
            "/posts",
            data={"csrf": await token(c), "book": book(), "visibility": "all"},
            files=[("photos", ("a.jpg", TINY, "image/jpeg"))],
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200 and re.match(r"/p/\d+\?msg=", r.json()["url"])


async def test_caption_up_to_2200(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        pid = pid_of(await upload(c, [], caption="가" * 2200))
        async with session_factory() as s:
            assert len((await s.get(ReadingPost, pid)).caption) == 2200
        r = await upload(c, [], caption="가" * 2201, n=2)
        assert r.status_code == 400 and "2200자까지" in r.text


async def test_daily_photo_cap_counts_deleted_posts(api, session_factory, monkeypatch):
    monkeypatch.setattr(pp, "DAILY_PHOTOS", 7)
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        first = pid_of(await upload(c, [variant(i) for i in range(5)], n=1))
        # 지워도 오늘 올린 수는 남는다(지우고 다시 올리기로 한도를 넘지 못하게)
        await c.post(f"/p/{first}/delete", data={"csrf": await token(c)})
        r = await upload(c, [variant(i) for i in range(3)], n=2)
        assert r.status_code == 400 and "하루에" in r.text
        pid_of(await upload(c, [variant(1), variant(2)], n=3))  # 5 + 2 = 7 까지는 된다
        assert (await upload(c, [variant(1)], n=4)).status_code == 400
        assert pid_of(await upload(c, [], n=5))  # 사진 없는 게시물은 상관없다
        # 다음 날(한국 날짜)이면 다시 올릴 수 있다
        import datetime as dt

        later = svc.clock() + dt.timedelta(days=1)
        monkeypatch.setattr(svc, "clock", lambda: later)
        pid_of(await upload(c, [variant(1)], n=6))


async def test_photos_deleted_with_the_post(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        pid = pid_of(await upload(c, [TINY, variant(2)]))
        assert len(await photos_in_db(session_factory, pid)) == 2
        await c.post(f"/p/{pid}/delete", data={"csrf": await token(c)})
        assert await photos_in_db(session_factory, pid) == []
        assert (await c.get(f"/media/p/{pid}/0.jpg")).status_code == 404


async def test_admin_hide_drops_photos(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        pid = pid_of(await upload(c, [TINY]))
        me = await uid(session_factory, "harin@example.com")
        async with session_factory() as s:
            from app.models import User

            admin = await s.get(User, me)
            await svc.admin_act(s, admin, await s.get(ReadingPost, pid), "hide")
        assert await photos_in_db(session_factory, pid) == []


# ---------------------------------------------------------------- 사진 주소의 공개 범위 = 게시물과 같다


async def test_media_visibility_mirrors_the_post(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        closed = pid_of(await upload(a, [TINY], visibility="friends", n=1))
        open_ = pid_of(await upload(a, [TINY], visibility="all", n=2))
        url = lambda pid: f"/media/p/{pid}/0.jpg"  # noqa: E731
        assert (await a.get(url(closed))).status_code == 200
        assert (await f.get(url(closed))).status_code == 200  # 친구
        assert (await x.get(url(closed))).status_code == 404  # 모르는 사람
        assert (await x.get(url(open_))).status_code == 200
        await relate(session_factory, ix, ia, status="blocked", blocker=ia)  # 하린이 서연을 막았다
        assert (await x.get(url(open_))).status_code == 404
        async with api(upstream) as anon:
            r = await anon.get(url(open_))
            assert r.status_code in (303, 401, 404) and r.content != TINY


async def test_flag_off_404(api, session_factory, monkeypatch):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        pid = pid_of(await upload(c, [TINY]))
        t = await token(c)
        monkeypatch.setattr(get_settings(), "feature_posts", False)
        for path in (f"/media/p/{pid}/0.jpg", "/profile/edit", f"/p/{pid}/comments", "/posts/books?q=a"):
            assert (await c.get(path)).status_code == 404, path
        for path in (f"/p/{pid}/save", f"/p/{pid}/send", "/profile/bio"):
            assert (await c.post(path, data={"csrf": t})).status_code == 404, path


# ---------------------------------------------------------------- 저장


async def test_save_unsave_and_saved_tab_privacy(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        open_ = pid_of(await upload(a, [TINY], n=1))
        closed = pid_of(await upload(a, [], visibility="friends", n=2))
        t = await token(f)
        r = await f.post(f"/p/{open_}/save", data={"csrf": t}, headers={"Accept": "application/json"})
        assert r.json() == {"saved": True}
        await f.post(f"/p/{closed}/save", data={"csrf": t})
        saved = (await f.get("/profile?tab=saved")).text
        ids = [int(i) for i in re.findall(r'class="ps-tile" href="/p/(\d+)"', saved)]
        assert ids == [closed, open_]  # 저장한 순서, 최신이 먼저
        assert "나만 볼 수 있어요" in saved
        assert 'aria-pressed="true" aria-label="저장 취소"' in (await f.get(f"/p/{open_}")).text
        # 남의 프로필에는 저장됨 탭이 없고, ?tab=saved 는 그 사람의 게시물 격자다
        other = (await a.get(f"/u/{i_f}?tab=saved")).text
        assert "저장됨" not in other and "나만 볼 수 있어요" not in other
        assert 'href="/profile?tab=saved"' in (await f.get("/profile")).text
        # 친구를 끊으면 친구만 게시물은 저장됨에서도 사라진다
        async with session_factory() as s:
            from app.models_chat import Friendship

            fr = await s.scalar(select(Friendship))
            await s.delete(fr)
            await s.commit()
        ids = [
            int(i)
            for i in re.findall(r'class="ps-tile" href="/p/(\d+)"', (await f.get("/profile?tab=saved")).text)
        ]
        assert ids == [open_]
        # 다시 누르면 저장 취소
        r = await f.post(f"/p/{open_}/save", data={"csrf": t}, headers={"Accept": "application/json"})
        assert r.json() == {"saved": False}
        async with session_factory() as s:
            assert (
                await s.scalar(select(func.count()).select_from(PostSave).where(PostSave.post_id == open_))
                == 0
            )
        # 볼 수 없는 게시물은 저장도 못 한다
        assert (await x.post(f"/p/{closed}/save", data={"csrf": await token(x)})).status_code == 404


# ---------------------------------------------------------------- 댓글: 좋아요 · 답글 · 지우기 · 신고


async def test_comment_like_reply_delete_report(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        pid = pid_of(await upload(a, [], n=1))
        ta, tf, tx = await token(a), await token(f), await token(x)
        J = {"Accept": "application/json"}
        top = (await f.post(f"/p/{pid}/comments", data={"csrf": tf, "body": "좋아요"}, headers=J)).json()[
            "id"
        ]
        r1 = (
            await a.post(
                f"/p/{pid}/comments", data={"csrf": ta, "body": "@민준 고마워요", "parent_id": top}, headers=J
            )
        ).json()["id"]
        # 답글에 답하면 같은 윗댓글 묶음에 붙는다(한 단계만)
        r2 = (
            await x.post(
                f"/p/{pid}/comments", data={"csrf": tx, "body": "저도요", "parent_id": r1}, headers=J
            )
        ).json()["id"]
        async with session_factory() as s:
            assert (await s.get(PostComment, r1)).parent_id == top
            assert (await s.get(PostComment, r2)).parent_id == top
        html = (await a.get(f"/p/{pid}")).text
        assert "답글 2개 보기" in html
        assert html.index(f'id="c{top}"') < html.index(f'id="c{r1}"') < html.index(f'id="c{r2}"')
        assert "댓글 3개 모두 보기" in (await a.get("/feed")).text
        # 댓글 좋아요: 켜고 끈다
        r = await a.post(f"/p/{pid}/comments/{top}/like", data={"csrf": ta}, headers=J)
        assert r.json() == {"liked": True, "count": 1}
        r = await x.post(f"/p/{pid}/comments/{top}/like", data={"csrf": tx}, headers=J)
        assert r.json() == {"liked": True, "count": 2}
        r = await a.post(f"/p/{pid}/comments/{top}/like", data={"csrf": ta}, headers=J)
        assert r.json() == {"liked": False, "count": 1}
        assert "좋아요 <b>1</b>개" in (await f.get(f"/p/{pid}")).text
        # 신고: 남의 댓글만, 한 번만 남는다
        await a.post(f"/p/{pid}/comments/{top}/report", data={"csrf": ta, "reason": "spam"})
        await a.post(f"/p/{pid}/comments/{top}/report", data={"csrf": ta, "reason": "abuse"})
        r = await f.post(f"/p/{pid}/comments/{top}/report", data={"csrf": tf, "reason": "spam"})
        assert "내 댓글은 신고할 수 없어요" in r.headers["location"] or "%EB%82%B4" in r.headers["location"]
        async with session_factory() as s:
            assert await s.scalar(select(func.count()).select_from(PostCommentReport)) == 1
        # 지우기: 남의 댓글은 404, 윗댓글을 지우면 답글도 보이지 않는다
        assert (await x.post(f"/p/{pid}/comments/{top}/delete", data={"csrf": tx})).status_code == 404
        await f.post(f"/p/{pid}/comments/{top}/delete", data={"csrf": tf})
        html = (await a.get(f"/p/{pid}")).text
        assert f'id="c{r1}"' not in html and f'id="c{r2}"' not in html
        # 지운 댓글엔 답글 · 좋아요를 못 한다
        r = await x.post(
            f"/p/{pid}/comments", data={"csrf": tx, "body": "늦었다", "parent_id": top}, headers=J
        )
        assert r.status_code == 400
        assert (await x.post(f"/p/{pid}/comments/{r1}/like", data={"csrf": tx})).status_code == 404
        async with session_factory() as s:
            assert await s.scalar(select(func.count()).select_from(PostCommentLike)) == 1


async def test_comment_on_other_post_or_blocked_cannot_be_targeted(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        p1 = pid_of(await upload(a, [], n=1))
        p2 = pid_of(await upload(a, [], n=2))
        J = {"Accept": "application/json"}
        cid = (
            await x.post(f"/p/{p1}/comments", data={"csrf": await token(x), "body": "안녕"}, headers=J)
        ).json()["id"]
        tf = await token(f)
        # 다른 게시물의 댓글 id 로는 답글 · 좋아요가 안 된다
        assert (
            await f.post(f"/p/{p2}/comments", data={"csrf": tf, "body": "a", "parent_id": cid}, headers=J)
        ).status_code == 400
        assert (await f.post(f"/p/{p2}/comments/{cid}/like", data={"csrf": tf})).status_code == 404
        # 차단 관계면 그 사람 댓글이 보이지 않고 좋아요도 못 한다
        await relate(session_factory, i_f, ix, status="blocked", blocker=i_f)
        assert f'id="c{cid}"' not in (await f.get(f"/p/{p1}")).text
        assert (await f.post(f"/p/{p1}/comments/{cid}/like", data={"csrf": tf})).status_code == 404


async def test_comments_sheet_fragment(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        pid = pid_of(await upload(a, [], visibility="friends", n=1))
        await f.post(f"/p/{pid}/comments", data={"csrf": await token(f), "body": "시트에서 보여요"})
        r = await f.get(f"/p/{pid}/comments")
        assert r.status_code == 200 and "시트에서 보여요" in r.text and "<html" not in r.text
        assert f'action="/p/{pid}/comments"' in r.text
        assert (await x.get(f"/p/{pid}/comments")).status_code == 404


# ---------------------------------------------------------------- 보내기(DM)


async def test_send_to_friend_creates_one_dm_only_for_friends(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        pid = pid_of(await upload(a, [TINY], n=1))
        ta = await token(a)
        page = (await a.get("/feed")).text
        assert (
            'id="psSend"' in page and f'value="{i_f}"' in page and f'value="{ix}"' not in page
        )  # 시트엔 친구만
        r = await a.post(
            f"/p/{pid}/send", data={"csrf": ta, "friend_id": i_f}, headers={"Accept": "application/json"}
        )
        assert r.json() == {"sent": True}
        async with session_factory() as s:
            msgs = list(await s.scalars(select(ChatMessage)))
            assert len(msgs) == 1 and msgs[0].body == f"/p/{pid}" and msgs[0].user_id == ia
        # 친구가 아니면 404, 메시지는 늘지 않는다
        assert (await a.post(f"/p/{pid}/send", data={"csrf": ta, "friend_id": ix})).status_code == 404
        assert (await a.post(f"/p/{pid}/send", data={"csrf": ta, "friend_id": ia})).status_code == 404
        async with session_factory() as s:
            assert await s.scalar(select(func.count()).select_from(ChatMessage)) == 1


async def test_send_off_when_chat_off(api, session_factory, monkeypatch):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        pid = pid_of(await upload(a, [], n=1))
        monkeypatch.setattr(get_settings(), "feature_chat", False)
        assert (
            await a.post(f"/p/{pid}/send", data={"csrf": await token(a), "friend_id": i_f})
        ).status_code == 404


# ---------------------------------------------------------------- 프로필: 소개 · 숫자 · 고리


async def test_bio_length_and_display(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        assert "소개 한 줄을 적어 보세요" in (await a.get("/profile")).text
        edit = (await a.get("/profile/edit")).text
        assert 'maxlength="150"' in edit
        t = await token(a)
        r = await a.post("/profile/bio", data={"csrf": t, "bio": "가" * 151})
        assert r.status_code == 303 and r.headers["location"].startswith("/profile/edit?error=")
        r = await a.post("/profile/bio", data={"csrf": t, "bio": "  고전을 천천히 읽어요 <b>책</b>  "})
        assert r.headers["location"].startswith("/profile?msg=")
        assert (
            "고전을 천천히 읽어요 &lt;b&gt;책&lt;/b&gt;" in (await x.get(f"/u/{ia}")).text
        )  # 남에게도 보인다(이스케이프)
        assert (await a.post("/profile/bio", data={"csrf": "bad", "bio": "x"})).status_code in (400, 403)


async def test_profile_header_buttons_and_counts(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        pid_of(await upload(a, [TINY], n=1))
        pid_of(await upload(a, [], visibility="friends", n=2))
        mine = (await a.get("/profile")).text
        assert (
            'href="/profile/edit"' in mine
            and "프로필 편집" in mine
            and 'aria-label="새 게시물 올리기"' in mine
        )
        assert "ps-ringed" in mine  # 24시간 안에 올렸으면 고리
        friend = (await f.get(f"/u/{ia}")).text
        assert (
            "친구 <span" in friend and f'href="/chat/dm/{ia}"' in friend and 'data-count="posts">2<' in friend
        )
        stranger = (await x.get(f"/u/{ia}")).text
        assert "친구 신청" in stranger and 'data-count="posts">1<' in stranger and "저장됨" not in stranger
        # 하루가 지나면 고리가 사라진다
        import datetime as dt

        later = svc.clock() + dt.timedelta(hours=25)
        orig = svc.clock
        svc.clock = lambda: later
        try:
            assert "ps-ringed" not in (await a.get("/profile")).text
        finally:
            svc.clock = orig


async def test_like_line_names_a_friend(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        pid = pid_of(await upload(a, [], n=1))
        await x.post(f"/p/{pid}/like", data={"csrf": await token(x)})
        assert "좋아요 <b data-like-count>1</b>개" in (await a.get(f"/p/{pid}")).text
        await f.post(f"/p/{pid}/like", data={"csrf": await token(f)})
        html = (await a.get(f"/p/{pid}")).text
        assert "<b>민준</b>님 외 <b data-like-count>1</b>명이 좋아합니다" in html


# ---------------------------------------------------------------- 탐색


async def test_explore_shows_only_everyone_visible_posts_with_big_tiles(api, session_factory):
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        open_ids = [pid_of(await upload(a, [TINY] if i % 2 else [], n=i)) for i in range(8)]
        closed = pid_of(await upload(a, [], visibility="friends", n=99))
        page = (await f.get("/feed?tab=all")).text  # 친구여도 '모두'에는 친구만 글이 없다
        html = page.split('class="ps-explore"', 1)[1].split("</ul>", 1)[0]  # 오른쪽 칸의 인기 게시물은 빼고
        ids = [int(i) for i in re.findall(r'class="ps-tile" href="/p/(\d+)"', html)]
        assert ids == sorted(open_ids, reverse=True) and closed not in ids
        assert html.count('class="ps-big l"') == 1 and html.count('class="ps-big r"') == 1  # 0번 칸 · 7번 칸
        assert 'class="ps-explore"' in page
        await relate(session_factory, ix, ia, status="blocked", blocker=ix)
        assert "ps-tile" not in (await x.get("/feed?tab=all")).text


async def test_book_search_json(api, session_factory):
    async with api(upstream) as c:
        await signup(c, "harin@example.com", "하린")
        r = await c.get("/posts/books?q=")
        assert r.json() == {"items": [], "partial": False}


def test_storage_estimate_constants():
    """보고서의 저장 공간 계산에 쓴 값 — 바뀌면 보고서도 다시 계산해야 한다."""
    assert (pp.MAX_BYTES, pp.MAX_PHOTOS, pp.DAILY_PHOTOS) == (400_000, 5, 30)
    assert pp.MAX_BYTES * pp.MAX_PHOTOS < 3_500_000  # 한 요청에 다 실어도 Vercel 4.5 MB 한도 아래


async def test_admin_sees_and_hides_reported_comment(api, session_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "seoyeon@example.com")
    (a, f, x), (ia, i_f, ix) = await three(api, session_factory)
    async with done(a, f, x):
        pid = pid_of(await upload(a, [], n=1))
        J = {"Accept": "application/json"}
        cid = (
            await f.post(f"/p/{pid}/comments", data={"csrf": await token(f), "body": "광고 링크"}, headers=J)
        ).json()["id"]
        await a.post(f"/p/{pid}/comments/{cid}/report", data={"csrf": await token(a), "reason": "spam"})
        assert (await f.get("/admin/posts")).status_code == 404
        html = (await x.get("/admin/posts")).text
        assert "신고된 댓글" in html and "광고 링크" in html and f"/admin/posts/comments/{cid}/hide" in html
        t = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
        r = await x.post(f"/admin/posts/comments/{cid}/hide", data={"csrf": t})
        assert r.status_code == 303
        assert f'id="c{cid}"' not in (await a.get(f"/p/{pid}")).text
        async with session_factory() as s:
            assert (await s.scalar(select(PostCommentReport))).resolved is True
        assert (await x.post("/admin/posts/comments/999/hide", data={"csrf": t})).status_code == 404
