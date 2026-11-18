"""피드(/feed) — 앱의 소셜 홈. Instagram · X · Facebook · Threads 의 홈 피드를 우리 규칙으로 섞는다.

- 동그라미 줄(Instagram): 나 → 친구(24시간 안에 올렸거나 지금 읽는 중이면 고리).
- 친구 탭(X 의 Following): 나와 친구의 게시물, 최신순 + 게시물 4개마다 최대 1개의 활동 소식.
- 추천 탭(X 의 For you): '가입한 모두' 공개 게시물을 최근 + 좋아요만으로 점수 매겨(행동 추적 없음) 보여 준다.
- 활동 소식(Facebook/X 의 친구 소식): 이미 있는 표에서만 만든다 — 서재 '다 읽음', 대결 1등 완주(points_log),
  7일 연속 읽기(points_log, 목표 기능), 모집 글(게시판 기능). 친구만 · 차단 제외 · 개인 파일 책은 절대 아님.
- 모두 확인했어요(Instagram): 3일보다 오래된 첫 게시물 앞에 줄 하나와 추천 게시물 몇 칸.

새 테이블은 없다. 공개 범위 · 차단 판정은 app/services/posts.py 의 것을 그대로 쓴다.
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Book, LibraryEntry, PointsLog, Room, RoomMember, Shelf, User
from app.models_posts import PostLike, ReadingPost
from app.services import posts as psvc

ACT_EVERY = 4  # 게시물 4개마다 활동 소식 최대 1개
ACT_LOOKBACK = dt.timedelta(days=14)  # 마지막 쪽에서 볼 활동 소식의 범위
CAUGHT_UP = dt.timedelta(days=3)  # Instagram: '최근 3일 게시물을 다 봤어요'
REC_PAGE, REC_POOL, REC_DAYS = 10, 300, 30
NEW_MAX = 20  # 새 게시물 알약을 눌렀을 때 끼워 넣는 최대 수
THIN = 4  # 친구 피드 첫 쪽이 이보다 적으면 추천 사람 · 인기 게시물을 같이 보여 준다


def clock() -> dt.datetime:  # 테스트에서 바꾼다
    return psvc.clock()


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return psvc._aware(t)


def _public_book(book: Book | None) -> bool:
    """활동 소식에 제목을 써도 되는 책 — 개인이 올린 파일은 절대 아니다."""
    return book is not None and not psvc._private(book.source, book.owner_id)


# ---------------------------------------------------------------- 활동 소식


@dataclass
class Activity:
    kind: str  # done · race · streak · recruit
    user: User
    at: dt.datetime
    href: str
    book: str | None = None
    days: int = 0  # 연속 읽기 일수
    action: str | None = None  # '참가' 같은 버튼 글


async def _streak_days(session: AsyncSession, user_id: int, ref_day: str) -> int:
    """7일마다 받는 연속 읽기 적립을 거꾸로 이어 세어 몇 일째인지 — 7 · 14 · 21…"""
    refs = set(
        await session.scalars(
            select(PointsLog.ref_id).where(PointsLog.user_id == user_id, PointsLog.source == "streak")
        )
    )
    try:
        d = dt.date.fromisoformat(ref_day)
    except (TypeError, ValueError):
        return 7
    n = 1
    while (d - dt.timedelta(days=7 * n)).isoformat() in refs and n < 60:
        n += 1
    return 7 * n


async def activities(
    session: AsyncSession, viewer_id: int, since: dt.datetime, until: dt.datetime, limit: int
) -> list[Activity]:
    """[since, until) 동안 친구들의 활동 소식(최신이 먼저), 최대 limit 개."""
    if limit <= 0:
        return []
    friends, blocked = await psvc.circle(session, viewer_id)
    ids = friends - blocked
    if not ids:
        return []
    settings = get_settings()
    users = {u.id: u for u in await session.scalars(select(User).where(User.id.in_(ids)))}
    out: list[Activity] = []

    # 1) 서재에서 '다 읽었어요'로 옮긴 책(개인 파일 제외)
    rows = await session.execute(
        select(LibraryEntry.user_id, LibraryEntry.updated_at, Book)
        .join(Book, Book.id == LibraryEntry.book_id)
        .where(
            LibraryEntry.user_id.in_(ids),
            LibraryEntry.shelf == Shelf.DONE,
            LibraryEntry.updated_at >= since,
            LibraryEntry.updated_at < until,
            Book.source != "upload",
            Book.owner_id.is_(None),
        )
        .order_by(LibraryEntry.updated_at.desc())
        .limit(limit * 3)
    )
    for uid, at, book in rows.all():
        if _public_book(book):
            out.append(Activity("done", users[uid], _aware(at), f"/u/{uid}", book=book.title))

    # 2) 대결방에서 가장 먼저 완주(같은 방의 race_finish 가운데 가장 이른 것)
    finish = await session.execute(
        select(PointsLog.user_id, PointsLog.created_at, PointsLog.ref_id).where(
            PointsLog.user_id.in_(ids),
            PointsLog.source == "race_finish",
            PointsLog.created_at >= since,
            PointsLog.created_at < until,
        )
    )
    mine_rooms = set(await session.scalars(select(RoomMember.room_id).where(RoomMember.user_id == viewer_id)))
    for uid, at, ref in finish.all():
        if not (ref or "").isdigit():
            continue
        first = await session.scalar(
            select(func.min(PointsLog.created_at)).where(
                PointsLog.source == "race_finish", PointsLog.ref_id == ref
            )
        )
        if first is None or _aware(first) < _aware(at):
            continue
        room = await session.get(Room, int(ref))
        book = await session.get(Book, room.book_id) if room is not None else None
        if not _public_book(book):
            continue
        href = f"/races/{room.id}" if room.id in mine_rooms else f"/u/{uid}"
        out.append(Activity("race", users[uid], _aware(at), href, book=book.title))

    # 3) 7일 연속 읽기(목표 기능이 켜져 있을 때만)
    if settings.feature_goals:
        streaks = await session.execute(
            select(PointsLog.user_id, PointsLog.created_at, PointsLog.ref_id).where(
                PointsLog.user_id.in_(ids),
                PointsLog.source == "streak",
                PointsLog.created_at >= since,
                PointsLog.created_at < until,
            )
        )
        for uid, at, ref in streaks.all():
            days = await _streak_days(session, uid, ref)
            out.append(Activity("streak", users[uid], _aware(at), f"/u/{uid}", days=days))

    # 4) 같이 읽을 사람 모집 글(게시판 기능이 켜져 있을 때만, 아직 모집 중인 것)
    if settings.feature_board:
        from app.models_board import RecruitPost

        recruits = await session.execute(
            select(RecruitPost, Book)
            .join(Book, Book.id == RecruitPost.book_id)
            .where(
                RecruitPost.author_id.in_(ids),
                RecruitPost.status == "open",
                RecruitPost.created_at >= since,
                RecruitPost.created_at < until,
            )
        )
        for rp, book in recruits.all():
            if _public_book(book):
                out.append(
                    Activity(
                        "recruit",
                        users[rp.author_id],
                        _aware(rp.created_at),
                        f"/together/{rp.id}",
                        book=book.title,
                        action="참가",
                    )
                )

    out.sort(key=lambda a: a.at, reverse=True)
    return out[:limit]


# ---------------------------------------------------------------- 피드 한 쪽(게시물 + 활동 소식 + 줄)


@dataclass
class Item:
    kind: str  # post · act · caught
    card: psvc.Card | None = None
    act: Activity | None = None


async def _created(session: AsyncSession, post_id: int | None) -> dt.datetime | None:
    if not post_id:
        return None
    return _aware(await session.scalar(select(ReadingPost.created_at).where(ReadingPost.id == post_id)))


async def friends_page(
    session: AsyncSession, viewer_id: int, before: int | None = None
) -> tuple[list[Item], int | None, int]:
    """친구 탭 한 쪽: (줄줄이 칸, 다음 커서, 이 쪽 게시물 수).

    활동 소식은 이 쪽 게시물들의 시간 범위 안에서만 고른다 — 쪽을 넘겨도 같은 소식이 두 번 나오지 않는다.
    """
    cards, cursor = await psvc.feed(session, viewer_id, "friends", before)
    now = clock()
    until = await _created(session, before) or now
    if cards:
        since = (
            _aware(cards[-1].post.created_at)
            if cursor
            else min(_aware(cards[-1].post.created_at), now - ACT_LOOKBACK)
        )
    else:
        since = now - ACT_LOOKBACK
    acts = await activities(session, viewer_id, since, until, len(cards) // ACT_EVERY)
    cutoff = now - CAUGHT_UP
    prev_recent = until >= cutoff if before else None  # 앞 쪽의 마지막 게시물이 최근이었나
    items: list[Item] = []
    shown_caught = False
    for i, c in enumerate(cards):
        recent = _aware(c.post.created_at) >= cutoff
        if prev_recent and not recent and not shown_caught:
            items.append(Item("caught"))
            shown_caught = True
        prev_recent = recent
        items.append(Item("post", card=c))
        if (i + 1) % ACT_EVERY == 0 and acts:
            items.append(Item("act", act=acts.pop(0)))
    if cards and cursor is None and prev_recent and not shown_caught:
        items.append(Item("caught"))  # 마지막 쪽이 최근 글로 끝났다
    return items, cursor, len(cards)


async def new_count(session: AsyncSession, viewer_id: int, after: int) -> int:
    """친구 탭에서 after 보다 새로 올라온(보이는) 친구 게시물 수 — 내 글은 세지 않는다."""
    friends, blocked = await psvc.circle(session, viewer_id)
    return (
        await session.scalar(
            select(func.count())
            .select_from(ReadingPost)
            .where(
                ReadingPost.id > after,
                ReadingPost.author_id.in_(friends or {0}),
                psvc._visible(viewer_id, friends, blocked),
            )
        )
        or 0
    )


async def fresh(session: AsyncSession, viewer_id: int, after: int) -> list[psvc.Card]:
    """새 게시물 알약을 눌렀을 때 끼워 넣을 게시물(최신이 먼저, 최대 NEW_MAX) — 나와 친구."""
    friends, blocked = await psvc.circle(session, viewer_id)
    posts = list(
        await session.scalars(
            select(ReadingPost)
            .where(
                ReadingPost.id > after,
                ReadingPost.author_id.in_(friends | {viewer_id}),
                psvc._visible(viewer_id, friends, blocked),
            )
            .order_by(ReadingPost.id.desc())
            .limit(NEW_MAX)
        )
    )
    return await psvc._cards(session, viewer_id, posts)


# ---------------------------------------------------------------- 추천


async def _ranked_ids(session: AsyncSession, viewer_id: int) -> list[int]:
    """'가입한 모두' 공개 게시물(내 글 · 친구만 글 · 차단 제외)을 최근 + 좋아요로 점수 매긴 순서."""
    friends, blocked = await psvc.circle(session, viewer_id)
    now = clock()
    likes = select(PostLike.post_id, func.count().label("n")).group_by(PostLike.post_id).subquery()
    rows = await session.execute(
        select(ReadingPost.id, ReadingPost.created_at, func.coalesce(likes.c.n, 0))
        .outerjoin(likes, likes.c.post_id == ReadingPost.id)
        .where(
            psvc._visible(viewer_id, friends, blocked),
            ReadingPost.visibility == "all",
            ReadingPost.author_id != viewer_id,
            ReadingPost.created_at >= now - dt.timedelta(days=REC_DAYS),
        )
        .order_by(ReadingPost.id.desc())
        .limit(REC_POOL)
    )

    def score(row) -> float:
        _, at, n = row
        hours = max(0.0, (now - _aware(at)).total_seconds() / 3600)
        return (n + 1) / (hours + 2) ** 1.4

    return [r[0] for r in sorted(rows.all(), key=lambda r: (score(r), r[0]), reverse=True)]


async def recommended(
    session: AsyncSession, viewer_id: int, page: int = 0, size: int | None = None
) -> tuple[list[psvc.Card], bool]:
    size = size or REC_PAGE
    ids = await _ranked_ids(session, viewer_id)
    chunk = ids[page * size : (page + 1) * size]
    if not chunk:
        return [], False
    posts = {p.id: p for p in await session.scalars(select(ReadingPost).where(ReadingPost.id.in_(chunk)))}
    cards = await psvc._cards(session, viewer_id, [posts[i] for i in chunk if i in posts])
    return cards, len(ids) > (page + 1) * size


async def popular_tiles(session: AsyncSession, viewer_id: int, n: int = 3, skip_friends: bool = True) -> list:
    """인기 공개 게시물 칸 몇 개 — '모두 확인했어요' 아래 · 얇은 피드 · 오른쪽 칸."""
    ids = await _ranked_ids(session, viewer_id)
    if skip_friends:
        friends, _ = await psvc.circle(session, viewer_id)
        authors = dict(
            (
                await session.execute(
                    select(ReadingPost.id, ReadingPost.author_id).where(ReadingPost.id.in_(ids))
                )
            ).all()
        )
        ids = [i for i in ids if authors.get(i) not in friends] or ids
    posts = {p.id: p for p in await session.scalars(select(ReadingPost).where(ReadingPost.id.in_(ids[:n])))}
    return await psvc.tiles_of(session, [posts[i] for i in ids[:n] if i in posts])


# ---------------------------------------------------------------- 동그라미 줄 · 작성 칸 · 추천 사람


@dataclass
class Story:
    user: User
    ring: bool = False  # 24시간 안에 올렸거나 지금 읽는 중
    reading: bool = False
    href: str = ""
    newest: int = 0  # 가장 최근 게시물 id(이 브라우저가 '봤음'을 기억하는 데 쓴다)
    me: bool = False


async def stories(session: AsyncSession, viewer: User, limit: int = 20) -> list[Story]:
    from app.services import chat as chat_service

    friends, blocked = await psvc.circle(session, viewer.id)
    people = friends - blocked
    newest: dict[int, tuple[int, dt.datetime]] = {}
    if people | {viewer.id}:
        rows = await session.execute(
            select(ReadingPost.author_id, func.max(ReadingPost.id), func.max(ReadingPost.created_at))
            .where(
                ReadingPost.author_id.in_(people | {viewer.id}), psvc._visible(viewer.id, friends, blocked)
            )
            .group_by(ReadingPost.author_id)
        )
        newest = {a: (pid, _aware(at)) for a, pid, at in rows.all()}
    day_ago = clock() - dt.timedelta(hours=24)
    mine = newest.get(viewer.id)
    out = [
        Story(
            viewer,
            ring=bool(mine and mine[1] >= day_ago),
            href=f"/p/{mine[0]}" if mine else "/posts/new",
            newest=mine[0] if mine else 0,
            me=True,
        )
    ]
    others: list[Story] = []
    for card in await chat_service.friend_cards(session, viewer.id):
        uid = card.user.id
        if uid not in people:
            continue
        post = newest.get(uid)
        recent = bool(post and post[1] >= day_ago)
        others.append(
            Story(
                card.user,
                ring=recent or card.active,
                reading=card.active,
                href=f"/p/{post[0]}" if post else f"/u/{uid}",
                newest=post[0] if post else 0,
            )
        )
    epoch = dt.datetime.min.replace(tzinfo=dt.UTC)
    others.sort(key=lambda s: (s.ring, newest.get(s.user.id, (0, epoch))[1]), reverse=True)
    return out + others[: limit - 1]


async def reading_now(session: AsyncSession, user_id: int, n: int = 3) -> list[tuple[str, str]]:
    """작성 칸의 칩 — 지금 읽는 책(개인 파일 제외): (폼 값, 제목)."""
    picks = await psvc.library_picks(session, user_id)
    return [(value, pick.title) for value, pick, shelf in picks if shelf == "reading"][:n]


async def people(session: AsyncSession, viewer_id: int, n: int = 5) -> list[tuple[User, list[str]]]:
    """같은 대결방 사람 가운데 아직 관계가 없는 사람 — 친구 신청 후보(친구 기능이 켜져 있을 때)."""
    if not get_settings().feature_chat:
        return []
    from app.services import chat as chat_service

    return [(u, rooms) for u, _, rooms in (await chat_service.roommates(session, viewer_id))[:n]]


@dataclass
class Side:
    """넓은 화면의 오른쪽 칸 — 내 작은 프로필 · 추천 사람 · 인기 게시물."""

    me: User
    posts: int = 0
    friends: int = 0
    people: list = field(default_factory=list)
    popular: list = field(default_factory=list)


async def side(session: AsyncSession, viewer: User) -> Side:
    count = await session.scalar(
        select(func.count())
        .select_from(ReadingPost)
        .where(and_(ReadingPost.author_id == viewer.id, ReadingPost.deleted_at.is_(None)))
    )
    return Side(
        viewer,
        posts=count or 0,
        friends=await psvc.friend_count(session, viewer.id),
        people=await people(session, viewer.id),
        popular=await popular_tiles(session, viewer.id, 3),
    )


def josa(word: str | None, pair: str = "을를") -> str:
    """받침이 있으면 앞 글자(을 · 이 · 은), 없으면 뒤 글자(를 · 가 · 는). 한글이 아니면 '을(를)'."""
    last = (word or "").strip()[-1:]
    if "가" <= last <= "힣":
        return pair[0] if (ord(last) - 0xAC00) % 28 else pair[1]
    return f"{pair[0]}({pair[1]})"
