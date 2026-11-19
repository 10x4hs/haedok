"""커뮤니티의 피드(/community, 예전 /feed) — 앱의 소셜 홈.

Instagram · X · Facebook · Threads 의 홈 피드를 우리 규칙으로 섞는다.

- 동그라미 줄(Instagram): 나 → 친구(24시간 안에 올렸거나 지금 읽는 중이면 고리).
- 전체 탭(기본): 친구 게시물을 최신순으로 먼저, 친구 3개마다 '새로운 사람'(친구가 아닌 사람)의
  '가입한 모두' 공개 게시물 1개, 친구 글이 끝나면 새로운 사람 글만. 새로운 사람 글은 최신 묶음 안에서
  좋아요가 많은 것을 앞에 둔다(행동 추적 없음). 차단 관계 · 친구만 글 · 내 글은 그 갈래에 없다.
- 친구 탭(X 의 Following): 나와 친구의 게시물, 최신순.
- 활동 소식(Facebook/X 의 친구 소식): 게시물 4개마다 최대 1개. 이미 있는 표에서만 만든다 — 서재 '다 읽음',
  대결 1등 완주(points_log), 7일 연속 읽기(points_log, 목표 기능), 모집 글(게시판 기능).
  친구만 · 차단 제외 · 개인 파일 책은 절대 아님.
- 제안 모듈(같이 읽을 사람 찾기 · 이 책 읽는 사람들 · 알 수도 있는 사람): 쪽마다 최대 하나(6번째 게시물 뒤),
  활동 소식과 붙지 않고, 비었으면 그리지 않으며, × 로 하루 숨긴다(쿠키).
- 모두 확인했어요(Instagram): 3일보다 오래된 첫 친구 게시물 앞에 줄 하나.

새 테이블은 없다. 공개 범위 · 차단 판정은 app/services/posts.py 의 것을 그대로 쓴다.
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Book, LibraryEntry, PointsLog, Room, RoomMember, Shelf, User
from app.models_posts import PostLike, ReadingPost
from app.services import posts as psvc

ACT_EVERY = 4  # 게시물 4개마다 활동 소식 최대 1개
ACT_LOOKBACK = dt.timedelta(days=14)  # 마지막 쪽에서 볼 활동 소식의 범위
CAUGHT_UP = dt.timedelta(days=3)  # Instagram: '최근 3일 게시물을 다 봤어요'
REC_POOL, REC_DAYS = 300, 30
NEW_MAX = 20  # 새 게시물 알약을 눌렀을 때 끼워 넣는 최대 수
THIN = 4  # 친구 탭 첫 쪽이 이보다 적으면 함께 읽은 사람 · 인기 게시물을 보여 준다(모듈은 끝에 안 둠)


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


MIX_EVERY = 3  # 전체 탭: 친구 게시물 3개마다 다른 사람의 공개 게시물 1개
MODULE_AT = 6  # 쪽마다 6번째 게시물 뒤에 제안 모듈 하나(활동 소식 자리 4 · 8 과 붙지 않는다)
MODULES = ("recruit", "readers", "people")


@dataclass
class Item:
    kind: str  # post · act · caught · module
    card: psvc.Card | None = None
    act: Activity | None = None
    module: "Module | None" = None
    alt: "Module | None" = None  # 넓은 화면용 대신 모듈 — 오른쪽 칸이 이미 모집 글을 보이면 이것을 보인다


async def _created(session: AsyncSession, post_id: int | None) -> dt.datetime | None:
    if not post_id:
        return None
    return _aware(await session.scalar(select(ReadingPost.created_at).where(ReadingPost.id == post_id)))


async def _decorate(
    session: AsyncSession,
    viewer: User,
    cards: list[psvc.Card],
    *,
    until: dt.datetime,
    friends_done: bool,
    prev_recent: bool | None,
    page_no: int,
    hidden: set[str],
) -> list[Item]:
    """게시물 줄에 활동 소식(4개마다 최대 1) · 모두 확인했어요 · 제안 모듈(6번째 뒤, 하루 숨김 존중)을 끼운다.

    활동 소식과 '모두 확인했어요'는 친구 게시물만 보고 정한다(다른 사람의 공개 게시물은 세지 않는다).
    """
    now = clock()
    mine = [c for c in cards if not c.suggested]
    if mine:
        last = _aware(mine[-1].post.created_at)
        since = min(last, now - ACT_LOOKBACK) if friends_done else last
        acts = await activities(session, viewer.id, since, until, len(cards) // ACT_EVERY)
    else:
        acts = []
    # 모듈 자리: 6번째 게시물 뒤. 마지막 쪽이 그보다 짧으면(THIN 개 이상일 때) 맨 끝에.
    at = MODULE_AT if len(cards) >= MODULE_AT else (len(cards) if friends_done and len(cards) >= THIN else 0)
    module = await pick_module(session, viewer, page_no, hidden) if at else None
    alt = None
    if module is not None and module.key == "recruit":
        alt = await pick_module(session, viewer, page_no, hidden | {"recruit"})
    cutoff = now - CAUGHT_UP
    items: list[Item] = []
    shown_caught = False
    friend_left = len(mine)
    for i, c in enumerate(cards):
        if not c.suggested:
            recent = _aware(c.post.created_at) >= cutoff
            if prev_recent and not recent and not shown_caught:
                items.append(Item("caught"))
                shown_caught = True
            prev_recent = recent
            friend_left -= 1
        elif friends_done and friend_left == 0 and prev_recent and not shown_caught:
            items.append(Item("caught"))  # 친구 글을 다 봤다 — 그 뒤로는 다른 사람들의 게시물
            shown_caught = True
        items.append(Item("post", card=c))
        if (i + 1) % ACT_EVERY == 0 and acts:
            items.append(Item("act", act=acts.pop(0)))  # 활동 소식 자리엔 모듈을 두지 않는다(붙지 않게)
        elif i + 1 == at and module is not None:
            items.append(Item("module", module=module, alt=alt))
    if mine and friends_done and prev_recent and not shown_caught and len(mine) == len(cards):
        items.append(Item("caught"))  # 마지막 쪽이 최근 친구 글로 끝났다
    return items


async def friends_page(
    session: AsyncSession,
    viewer: User,
    before: int | None = None,
    hidden: set[str] | None = None,
    page_no: int = 0,
) -> tuple[list[Item], int | None, int]:
    """친구 탭 한 쪽: (줄줄이 칸, 다음 커서, 이 쪽 게시물 수).

    활동 소식은 이 쪽 시간 범위 안에서만 고른다 — 쪽을 넘겨도 겹치지 않는다."""
    cards, cursor = await psvc.feed(session, viewer.id, "friends", before)
    until = await _created(session, before) or clock()
    items = await _decorate(
        session,
        viewer,
        cards,
        until=until,
        friends_done=cursor is None,
        prev_recent=(until >= clock() - CAUGHT_UP) if before else None,
        page_no=page_no,
        hidden=hidden or set(),
    )
    return items, cursor, len(cards)


# ---------------------------------------------------------------- 전체 탭: 친구 + 다른 사람(두 갈래 커서)


def parse_cursor(raw: str | None) -> tuple[int | None, int | None, bool, bool, int]:
    """'f.o.n' — f · o: 친구 · 다른 사람 갈래에서 마지막으로 보인 id(그보다 작은 것부터).

    'x' 는 그 갈래가 끝났다는 뜻, n 은 쪽 번호."""
    f = o = None
    f_done = o_done = False
    n = 0
    parts = (raw or "").split(".")
    if len(parts) == 3:
        for i, part in enumerate(parts[:2]):
            if part == "x":
                f_done, o_done = (True, o_done) if i == 0 else (f_done, True)
            elif part.isdigit():
                f, o = (int(part), o) if i == 0 else (f, int(part))
        n = int(parts[2]) if parts[2].isdigit() else 0
    return f, o, f_done, o_done, min(n, 1000)


def make_cursor(f: int | None, o: int | None, f_done: bool, o_done: bool, n: int) -> str:
    return f"{'x' if f_done else (f or 0)}.{'x' if o_done else (o or 0)}.{n}"


def _others_cond(viewer_id: int, friends: set[int], blocked: set[int]):
    """다른 사람의 공개 게시물: '가입한 모두' 공개만, 친구 · 나 · 차단 관계 제외."""
    return and_(
        psvc._visible(viewer_id, friends, blocked),
        ReadingPost.visibility == "all",
        ReadingPost.author_id.not_in(friends | {viewer_id}),
    )


def _friends_cond(viewer_id: int, friends: set[int], blocked: set[int]):
    return and_(psvc._visible(viewer_id, friends, blocked), ReadingPost.author_id.in_(friends | {viewer_id}))


async def _mark_suggested(
    session: AsyncSession, viewer_id: int, cards: list[psvc.Card], ids: set[int]
) -> None:
    """다른 사람 게시물에 '새로운 사람' 표시와 관계(친구 신청 단추에 쓴다)를 붙인다."""
    from app.services import chat as chat_service

    states: dict[int, str] = {}
    for c in cards:
        if c.post.id not in ids:
            continue
        c.suggested = True
        if c.author.id not in states:
            states[c.author.id] = chat_service.state_of(
                await chat_service.relation(session, viewer_id, c.author.id), viewer_id
            )
        c.rel = states[c.author.id]


async def mixed_page(
    session: AsyncSession, viewer: User, cur: str | None = None, hidden: set[str] | None = None
) -> tuple[list[Item], str | None, int]:
    """전체 탭 한 쪽(Instagram 홈처럼 하나의 흐름): 친구 3개마다 다른 사람 1개, 친구 글이 끝나면 다른 사람만.

    다른 사람 갈래는 최신 id 순으로 한 묶음을 가져와 그 안에서만 좋아요가 많은 것을 앞에 둔다 —
    커서는 두 갈래의 마지막 id 를 함께 담아서(f.o.n) 쪽을 넘겨도 겹치지 않는다.
    """
    f_cur, o_cur, f_done, o_done, n = parse_cursor(cur)
    friends, blocked = await psvc.circle(session, viewer.id)
    size = psvc.FEED_PAGE
    pattern = ["o" if (i + 1) % (MIX_EVERY + 1) == 0 else "f" for i in range(size)]
    need_f, need_o = pattern.count("f"), pattern.count("o")
    fposts: list[ReadingPost] = []
    if not f_done:
        stmt = select(ReadingPost).where(_friends_cond(viewer.id, friends, blocked))
        if f_cur:
            stmt = stmt.where(ReadingPost.id < f_cur)
        fposts = list(await session.scalars(stmt.order_by(ReadingPost.id.desc()).limit(need_f + 1)))
    f_more = len(fposts) > need_f
    fposts = fposts[:need_f]
    want_o = need_o + (need_f - len(fposts))
    oposts: list[ReadingPost] = []
    if not o_done:
        stmt = select(ReadingPost).where(_others_cond(viewer.id, friends, blocked))
        if o_cur:
            stmt = stmt.where(ReadingPost.id < o_cur)
        oposts = list(await session.scalars(stmt.order_by(ReadingPost.id.desc()).limit(want_o + 1)))
    o_more = len(oposts) > want_o
    oposts = oposts[:want_o]
    o_next = min((p.id for p in oposts), default=None)
    if oposts:  # 한 묶음 안에서는 좋아요가 많은 것을 앞으로(새 것 + 좋아요)
        likes = dict(
            (
                await session.execute(
                    select(PostLike.post_id, func.count())
                    .where(PostLike.post_id.in_([p.id for p in oposts]))
                    .group_by(PostLike.post_id)
                )
            ).all()
        )
        oposts.sort(key=lambda p: (likes.get(p.id, 0), p.id), reverse=True)
    ordered: list[ReadingPost] = []
    fi = oi = 0
    for slot in pattern:
        take_o = (slot == "o" and oi < len(oposts)) or fi >= len(fposts)
        if take_o and oi < len(oposts):
            ordered.append(oposts[oi])
            oi += 1
        elif fi < len(fposts):
            ordered.append(fposts[fi])
            fi += 1
    ordered += fposts[fi:] + oposts[oi:]
    cards = await psvc._cards(session, viewer.id, ordered)
    await _mark_suggested(session, viewer.id, cards, {p.id for p in oposts})
    friends_done_now = f_done or not f_more
    nxt = None
    if f_more or o_more:
        nxt = make_cursor(
            fposts[-1].id if fposts else f_cur, o_next or o_cur, friends_done_now, o_done or not o_more, n + 1
        )
    until = await _created(session, f_cur) or clock()
    items = await _decorate(
        session,
        viewer,
        cards,
        until=until,
        friends_done=friends_done_now,
        prev_recent=(until >= clock() - CAUGHT_UP) if f_cur else None,
        page_no=n,
        hidden=hidden or set(),
    )
    return items, nxt, len(cards)


async def new_count(session: AsyncSession, viewer_id: int, after: int, mixed: bool = False) -> int:
    """after 보다 새로 올라온(보이는) 게시물 수 — 내 글은 빼고.

    친구 탭은 나 · 친구의 글, 전체 탭은 다른 사람의 공개 글까지 센다."""
    friends, blocked = await psvc.circle(session, viewer_id)
    cond = _friends_cond(viewer_id, friends, blocked)
    if mixed:
        cond = or_(cond, _others_cond(viewer_id, friends, blocked))
    return (
        await session.scalar(
            select(func.count())
            .select_from(ReadingPost)
            .where(ReadingPost.id > after, ReadingPost.author_id != viewer_id, cond)
        )
        or 0
    )


async def fresh(session: AsyncSession, viewer_id: int, after: int, mixed: bool = False) -> list[psvc.Card]:
    """새 게시물 알약을 눌렀을 때 끼워 넣을 게시물(최대 NEW_MAX) — 친구 글 먼저, 그다음 다른 사람(전체 탭)."""
    friends, blocked = await psvc.circle(session, viewer_id)
    mine = list(
        await session.scalars(
            select(ReadingPost)
            .where(ReadingPost.id > after, _friends_cond(viewer_id, friends, blocked))
            .order_by(ReadingPost.id.desc())
            .limit(NEW_MAX)
        )
    )
    others: list[ReadingPost] = []
    if mixed:
        others = list(
            await session.scalars(
                select(ReadingPost)
                .where(ReadingPost.id > after, _others_cond(viewer_id, friends, blocked))
                .order_by(ReadingPost.id.desc())
                .limit(max(0, NEW_MAX - len(mine)))
            )
        )
    cards = await psvc._cards(session, viewer_id, mine + others)
    await _mark_suggested(session, viewer_id, cards, {p.id for p in others})
    return cards


# ---------------------------------------------------------------- 제안 모듈


@dataclass
class Module:
    key: str  # recruit · readers · people
    title: str
    recruits: list = field(default_factory=list)  # board.PostView
    people: list = field(default_factory=list)  # [(User, 관계, 한 줄 설명)]
    book: str | None = None


async def recruits(session: AsyncSession, viewer_id: int, n: int = 8) -> list:
    """모집 중이고 자리가 남은 글(게시판 기능이 켜져 있을 때) — 차단 관계 · 내 글 · 개인 파일 책 제외."""
    if not get_settings().feature_board:
        return []
    from app.services import board as board_service

    _, blocked = await psvc.circle(session, viewer_id)
    out = []
    for v in await board_service.board(session, viewer_id):
        if v.host.id in blocked or v.host.id == viewer_id or v.is_member or not v.joinable:
            continue
        if not _public_book(v.book):
            continue
        out.append(v)
    return out[:n]


async def readers(session: AsyncSession, viewer_id: int, n: int = 6) -> tuple[str | None, list]:
    """내가 읽(었)는 책을 지금 읽는 다른 사람들 — 활동이 이미 보이는 사람만:
    친구이거나, 공개 모집 방(모집 중인 글의 방)에 들어 있는 사람. 개인 파일 책은 절대 아님. 차단 제외."""
    from app.services import chat as chat_service

    friends, blocked = await psvc.circle(session, viewer_id)
    rows = await session.execute(
        select(Book)
        .join(LibraryEntry, LibraryEntry.book_id == Book.id)
        .where(LibraryEntry.user_id == viewer_id, LibraryEntry.shelf.in_((Shelf.READING, Shelf.DONE)))
        .order_by(LibraryEntry.updated_at.desc())
    )
    public_room_people: set[int] = set()
    if get_settings().feature_board:
        from app.models_board import RecruitPost

        public_room_people = set(
            await session.scalars(
                select(RoomMember.user_id)
                .join(RecruitPost, RecruitPost.room_id == RoomMember.room_id)
                .where(RecruitPost.status == "open")
            )
        )
    allowed = (friends | public_room_people) - blocked - {viewer_id}
    if not allowed:
        return None, []
    for book in rows.scalars():
        if not _public_book(book):
            continue
        ids = list(
            await session.scalars(
                select(LibraryEntry.user_id)
                .where(
                    LibraryEntry.book_id == book.id,
                    LibraryEntry.shelf == Shelf.READING,
                    LibraryEntry.user_id.in_(allowed),
                )
                .order_by(LibraryEntry.updated_at.desc())
                .limit(n)
            )
        )
        if not ids:
            continue
        users = {u.id: u for u in await session.scalars(select(User).where(User.id.in_(ids)))}
        out = []
        for uid in ids:
            state = chat_service.state_of(await chat_service.relation(session, viewer_id, uid), viewer_id)
            out.append((users[uid], state, "친구" if state == "friends" else "같이 읽기 방"))
        return book.title, out
    return None, []


async def pick_module(session: AsyncSession, viewer: User, page_no: int, hidden: set[str]) -> Module | None:
    """쪽마다 차례로(같이 읽을 사람 → 이 책 읽는 사람들 → 알 수도 있는 사람).

    하루 숨긴 것 · 빈 것은 건너뛴다."""
    for k in range(len(MODULES)):
        key = MODULES[(page_no + k) % len(MODULES)]
        if key in hidden:
            continue
        if key == "recruit":
            found = await recruits(session, viewer.id)
            if found:
                return Module("recruit", "같이 읽을 사람 찾기", recruits=found)
        elif key == "readers":
            title, found = await readers(session, viewer.id)
            if found:
                return Module("readers", "이 책 읽는 사람들", people=found, book=title)
        else:
            found = await people(session, viewer.id, 8)
            if found:
                return Module(
                    "people",
                    "알 수도 있는 사람",
                    people=[(u, "none", f"{rooms[0]}에서 함께") for u, rooms in found],
                )
    return None


# ---------------------------------------------------------------- 인기 공개 게시물


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


# ---------------------------------------------------------------- 동그라미 줄 · 함께 읽은 사람


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


async def people(session: AsyncSession, viewer_id: int, n: int = 5) -> list[tuple[User, list[str]]]:
    """같은 대결방 사람 가운데 아직 관계가 없는 사람 — 친구 신청 후보(친구 기능이 켜져 있을 때)."""
    if not get_settings().feature_chat:
        return []
    from app.services import chat as chat_service

    return [(u, rooms) for u, _, rooms in (await chat_service.roommates(session, viewer_id))[:n]]


@dataclass
class Side:
    """넓은 화면의 오른쪽 칸 — 내 작은 프로필 · 같이 읽을 사람(모집 글) 셋 · 바닥글.

    피드 안의 제안 모듈과 겹치지 않게 사람 추천 · 인기 게시물은 여기 두지 않는다."""

    me: User
    posts: int = 0
    friends: int = 0
    recruits: list = field(default_factory=list)


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
        recruits=await recruits(session, viewer.id, 3),
    )


def josa(word: str | None, pair: str = "을를") -> str:
    """받침이 있으면 앞 글자(을 · 이 · 은), 없으면 뒤 글자(를 · 가 · 는). 한글이 아니면 '을(를)'."""
    last = (word or "").strip()[-1:]
    if "가" <= last <= "힣":
        return pair[0] if (ord(last) - 0xAC00) % 28 else pair[1]
    return f"{pair[0]}({pair[1]})"
