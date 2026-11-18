"""읽은 책 게시물 — 책 한 권 + 내 생각을 인스타처럼 올리고, 친구·가입한 사람들이 본다.

규칙(모든 읽기에서 서버가 판정한다):
- 지운 게시물(deleted_at)은 아무에게도 보이지 않는다.
- 차단은 양쪽: 둘 사이에 차단 행이 있으면 서로의 게시물 · 댓글 · 프로필이 보이지 않는다(404).
- '친구만' 게시물은 쓴 사람과 친구에게만. 그 밖의 사람에게는 있는지조차 알리지 않는다(404).
- 책은 스냅샷으로 저장한다. 개인이 올린 파일(source="upload" 또는 주인이 있는 책)은 게시물의 책이 될 수 없다.
- 같은 사람이 같은 책을 하루(한국 날짜)에 한 번만 올린다.

친구 관계는 친구 기능(app/services/chat.py)의 friendships 테이블을 읽는다 —
채팅 기능이 꺼져 있어도 테이블은 있다.
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings
from app.models import Book, LibraryEntry, Shelf, User
from app.models_chat import Friendship
from app.models_posts import PostComment, PostLike, PostReport, ReadingPost

KST = dt.timezone(dt.timedelta(hours=9), "KST")
STATUSES = {"done": "읽었어요", "reading": "읽는 중"}
VISIBILITY = {"friends": "친구만", "all": "가입한 모두"}
DEFAULT_VISIBILITY = "all"
MAX_CAPTION, MAX_QUOTE, MAX_COMMENT = 500, 200, 300
FEED_PAGE, GRID_PAGE = 10, 30
REASONS = {"spam": "광고·도배", "abuse": "불쾌한 내용", "spoiler": "가리지 않은 스포일러", "other": "기타"}

# 속도 제한(10분 창) — 하루 한 번 규칙과 별개로 연타를 막는다
POST_LIMIT, POST_MSG = 10, "게시물을 너무 자주 올렸어요. 10분 뒤에 다시 해 주세요"
LIKE_LIMIT, LIKE_MSG = 120, "좋아요를 너무 빨리 누르고 있어요. 잠시 뒤에 다시 해 주세요"
COMMENT_LIMIT, COMMENT_MSG = 30, "댓글을 너무 빨리 달고 있어요. 잠시 뒤에 다시 해 주세요"
REPORT_LIMIT, REPORT_MSG = 10, "신고를 너무 자주 보냈어요. 10분 뒤에 다시 해 주세요"
DAILY_MSG = "이 책은 오늘 이미 올렸어요. 내일 다시 올려 주세요"

SPOILER = re.compile(r"\|\|(.+?)\|\|", re.S)  # 채팅(app/static/chat.js)과 같은 가리기 표시


class PostError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 거절."""


class NotFound(LookupError):
    """없거나 볼 수 없다 — 둘을 구분하지 않고 404로 답한다."""


def clock() -> dt.datetime:  # 테스트에서 바꾼다
    return dt.datetime.now(dt.UTC)


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=dt.UTC)  # SQLite는 시간대를 저장하지 않는다


def today() -> str:
    return clock().astimezone(KST).date().isoformat()


def when(t: dt.datetime | None) -> str:
    """'방금' · '5분 전' · '3시간 전' · '어제' · '11월 13일' · '2025년 3월 2일'(한국 날짜)."""
    t = _aware(t)
    if t is None:
        return ""
    now = clock()
    secs = (now - t).total_seconds()
    if secs < 60:
        return "방금"
    if secs < 3600:
        return f"{int(secs // 60)}분 전"
    local, day = t.astimezone(KST).date(), now.astimezone(KST).date()
    if local == day:
        return f"{int(secs // 3600)}시간 전"
    if local == day - dt.timedelta(days=1):
        return "어제"
    if local.year == day.year:
        return f"{local.month}월 {local.day}일"
    return f"{local.year}년 {local.month}월 {local.day}일"


def segments(text: str | None) -> list[tuple[bool, str]]:
    """`||글||` 을 가릴 조각으로 나눈다: [(가림?, 글), …]. 화면은 자동 이스케이프로만 보여 준다."""
    text = text or ""
    out: list[tuple[bool, str]] = []
    last = 0
    for m in SPOILER.finditer(text):
        if m.start() > last:
            out.append((False, text[last : m.start()]))
        out.append((True, m.group(1)))
        last = m.end()
    if last < len(text):
        out.append((False, text[last:]))
    return out


# ---------------------------------------------------------------- 책 고르기


@dataclass
class BookPick:
    """게시물에 저장할 책 스냅샷."""

    source: str
    external_id: str
    title: str
    author: str | None = None
    cover: str | None = None
    book_id: int | None = None


def safe_cover(url: str | None) -> str | None:
    """표지 주소는 https 또는 이 사이트의 /static/ 만 — 그 밖의 값은 버린다."""
    url = (url or "").strip()
    if url.startswith("https://") or (url.startswith("/static/") and ".." not in url):
        return url[:1000]
    return None


def _private(source: str | None, owner_id: int | None = None) -> bool:
    return source == "upload" or owner_id is not None


def pick_of_book(book: Book | None, cover: str | None = None) -> BookPick | None:
    """books 행 → 스냅샷. 개인이 올린 파일이면 None(제목이 새어 나가면 안 된다)."""
    if book is None or _private(book.source, book.owner_id):
        return None
    return BookPick(
        source=book.source,
        external_id=book.external_id,
        title=book.title,
        author=book.author,
        cover=safe_cover(cover if cover is not None else book.cover_url),
        book_id=book.id,
    )


def _mac(data: bytes) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, b"posts-pick:" + data, hashlib.sha256).hexdigest()[:32]


def sign_pick(p: BookPick) -> str:
    """검색 결과 한 권을 폼 값으로 — 서버가 서명해 두어 다시 조회하지 않고도 위조를 막는다."""
    raw = json.dumps(
        [p.source, p.external_id, p.title, p.author, p.cover], ensure_ascii=False, separators=(",", ":")
    ).encode()
    return "h:" + base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + _mac(raw)


def unsign_pick(value: str) -> BookPick | None:
    if not value.startswith("h:") or "." not in value:
        return None
    body, _, mac = value[2:].rpartition(".")
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except ValueError:
        return None
    if not hmac.compare_digest(_mac(raw), mac):
        return None
    try:
        source, external_id, title, author, cover = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if _private(source) or not (source and external_id and title):
        return None
    return BookPick(str(source)[:20], str(external_id)[:512], str(title)[:500], author, safe_cover(cover))


async def resolve_pick(session: AsyncSession, value: str) -> BookPick:
    """폼의 책 값: b:<books.id>(서재) 또는 h:<서명된 검색 결과>."""
    value = (value or "").strip()
    if value.startswith("b:") and value[2:].isdigit():
        from app.web import cover_of  # 표지 규칙(서점 표지 → 카탈로그 표지)을 같이 쓴다

        book = await session.get(Book, int(value[2:]))
        if book is not None and _private(book.source, book.owner_id):
            raise PostError("내가 올린 파일은 게시물로 올릴 수 없어요")
        pick = pick_of_book(book, cover_of(book) if book is not None else None)
        if pick is None:
            raise PostError("책을 다시 골라 주세요")
        return pick
    pick = unsign_pick(value)
    if pick is None:
        raise PostError("올릴 책을 골라 주세요")
    return pick


async def library_picks(session: AsyncSession, user_id: int) -> list[tuple[str, BookPick, str]]:
    """내 서재에서 고를 수 있는 책: 다 읽은 책 → 읽는 중(개인 파일은 뺀다). (폼 값, 스냅샷, 상태)."""
    from app.services import books as book_service
    from app.web import cover_of

    out = []
    order = {Shelf.DONE: 0, Shelf.READING: 1}
    entries = [x for x in await book_service.library_of(session, user_id) if x[0].shelf in order]
    for entry, book in sorted(entries, key=lambda x: order[x[0].shelf]):
        pick = pick_of_book(book, cover_of(book))
        if pick is not None:
            out.append((f"b:{book.id}", pick, "done" if entry.shelf == Shelf.DONE else "reading"))
    return out


# ---------------------------------------------------------------- 관계


async def _relations(session: AsyncSession, me: int) -> list[Friendship]:
    return list(
        await session.scalars(
            select(Friendship).where(or_(Friendship.user_low == me, Friendship.user_high == me))
        )
    )


def _other(f: Friendship, me: int) -> int:
    return f.user_high if f.user_low == me else f.user_low


async def circle(session: AsyncSession, me: int) -> tuple[set[int], set[int]]:
    """(친구, 차단 관계 — 누가 막았든) 한 번의 쿼리로."""
    friends, blocked = set(), set()
    for f in await _relations(session, me):
        if f.status == "accepted":
            friends.add(_other(f, me))
        elif f.status == "blocked":
            blocked.add(_other(f, me))
    return friends, blocked


async def state_with(session: AsyncSession, me: int, other: int) -> str:
    """내 쪽에서 본 관계(친구 기능과 같은 말): none · friends · outgoing · incoming · blocking · blocked."""
    from app.services import chat as chat_service

    if me == other:
        return "self"
    return chat_service.state_of(await chat_service.relation(session, me, other), me)


async def friend_count(session: AsyncSession, user_id: int) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Friendship)
            .where(
                Friendship.status == "accepted",
                or_(Friendship.user_low == user_id, Friendship.user_high == user_id),
            )
        )
        or 0
    )


def _visible(me: int, friends: set[int], blocked: set[int]):
    """이 사람에게 보이는 게시물의 조건(SQL)."""
    return and_(
        ReadingPost.deleted_at.is_(None),
        ReadingPost.author_id.not_in(blocked or {0}),
        or_(
            ReadingPost.author_id == me,
            ReadingPost.visibility == "all",
            and_(ReadingPost.visibility == "friends", ReadingPost.author_id.in_(friends or {0})),
        ),
    )


async def can_see(session: AsyncSession, viewer_id: int, post: ReadingPost | None) -> bool:
    if post is None or post.deleted_at is not None:
        return False
    if post.author_id == viewer_id:
        return True
    friends, blocked = await circle(session, viewer_id)
    if post.author_id in blocked:
        return False
    return post.visibility == "all" or post.author_id in friends


async def visible_post(session: AsyncSession, viewer_id: int, post_id: int) -> ReadingPost:
    post = await session.get(ReadingPost, post_id)
    if not await can_see(session, viewer_id, post):
        raise NotFound
    return post


async def face_ids(session: AsyncSession, viewer_id: int) -> set[int]:
    """프로필 사진을 볼 수 있는 사람들 — /avatars 규칙(본인 · 같은 대결방)과 같다. 나머지는 이름 첫 글자로."""
    from app.models import RoomMember

    mine = select(RoomMember.room_id).where(RoomMember.user_id == viewer_id)
    rows = await session.scalars(select(RoomMember.user_id).where(RoomMember.room_id.in_(mine)))
    return set(rows) | {viewer_id}


# ---------------------------------------------------------------- 쓰기


def clean(text: str | None, limit: int, *, lines: bool = True) -> str:
    """앞뒤 공백을 걷고, 줄바꿈은 두 줄까지만 남긴다. 길면 거절(잘라서 몰래 저장하지 않는다)."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"\n{3,}", "\n\n", text) if lines else " ".join(text.split())
    if len(text) > limit:
        raise PostError(f"{limit}자까지 쓸 수 있어요")
    return text


def _rating(raw) -> int | None:
    if raw in (None, "", "0", 0):
        return None
    try:
        r = int(raw)
    except (TypeError, ValueError) as e:
        raise PostError("별점은 1~5개로 골라 주세요") from e
    if not 1 <= r <= 5:
        raise PostError("별점은 1~5개로 골라 주세요")
    return r


def _choice(value: str, allowed: dict, msg: str) -> str:
    if value not in allowed:
        raise PostError(msg)
    return value


def validate(status: str, rating, caption: str, quote: str, visibility: str) -> dict:
    return {
        "status": _choice(status, STATUSES, "읽었어요 · 읽는 중 가운데 골라 주세요"),
        "rating": _rating(rating),
        "caption": clean(caption, MAX_CAPTION),
        "quote": clean(quote, MAX_QUOTE, lines=False) or None,
        "visibility": _choice(visibility, VISIBILITY, "누구에게 보일지 골라 주세요"),
    }


async def posted_today(session: AsyncSession, user_id: int, pick: BookPick) -> bool:
    return (
        await session.scalar(
            select(ReadingPost.id).where(
                ReadingPost.author_id == user_id,
                ReadingPost.book_source == pick.source,
                ReadingPost.external_id == pick.external_id,
                ReadingPost.post_day == today(),
            )
        )
    ) is not None


async def create(session: AsyncSession, user: User, pick: BookPick, fields: dict) -> ReadingPost:
    if await posted_today(session, user.id, pick):
        raise PostError(DAILY_MSG)
    await ratelimit.check(session, f"post:{user.id}", POST_LIMIT, ratelimit.WINDOW_S, POST_MSG)
    post = ReadingPost(
        author_id=user.id,
        book_id=pick.book_id,
        book_source=pick.source,
        external_id=pick.external_id,
        book_title=pick.title[:500],
        book_author=(pick.author or None) and str(pick.author)[:300],
        cover_url=pick.cover,
        post_day=today(),
        created_at=clock(),
        **fields,
    )
    session.add(post)
    try:
        await session.commit()
    except IntegrityError as e:  # 같은 책을 동시에 두 번 — 하나만 남는다
        await session.rollback()
        raise PostError(DAILY_MSG) from e
    return post


def _author_only(post: ReadingPost, user: User) -> None:
    if post.author_id != user.id:
        raise NotFound


async def edit(session: AsyncSession, user: User, post: ReadingPost, fields: dict) -> None:
    _author_only(post, user)
    for key in ("caption", "quote", "rating", "visibility", "status"):
        if key in fields:
            setattr(post, key, fields[key])
    post.updated_at = clock()
    await session.commit()


async def remove(session: AsyncSession, user: User, post: ReadingPost) -> None:
    _author_only(post, user)
    post.deleted_at, post.deleted_by = clock(), user.id
    await session.commit()


# ---------------------------------------------------------------- 좋아요 · 댓글 · 신고


async def like_count(session: AsyncSession, post_id: int) -> int:
    return await session.scalar(select(func.count()).where(PostLike.post_id == post_id)) or 0


async def toggle_like(session: AsyncSession, user: User, post: ReadingPost) -> tuple[bool, int]:
    """누르면 켜고 다시 누르면 끈다. (켜졌나, 수)."""
    await ratelimit.check(session, f"post-like:{user.id}", LIKE_LIMIT, ratelimit.WINDOW_S, LIKE_MSG)
    post_id = post.id
    mine = await session.get(PostLike, (post_id, user.id))
    if mine is not None:
        await session.delete(mine)
        await session.commit()
        liked = False
    else:
        session.add(PostLike(post_id=post_id, user_id=user.id))
        try:
            await session.commit()
        except IntegrityError:  # 동시에 두 번 — 한 번만 센다
            await session.rollback()
        liked = True
    return liked, await like_count(session, post_id)


async def add_comment(session: AsyncSession, user: User, post: ReadingPost, body: str) -> PostComment:
    text = clean(body, MAX_COMMENT)
    if not text:
        raise PostError("댓글을 적어 주세요")
    await ratelimit.check(session, f"post-comment:{user.id}", COMMENT_LIMIT, ratelimit.WINDOW_S, COMMENT_MSG)
    c = PostComment(post_id=post.id, user_id=user.id, body=text, created_at=clock())
    session.add(c)
    await session.commit()
    return c


async def delete_comment(session: AsyncSession, user: User, post: ReadingPost, comment_id: int) -> None:
    """내 댓글, 또는 내 게시물에 달린 댓글을 지운다."""
    c = await session.get(PostComment, comment_id)
    if c is None or c.post_id != post.id or c.deleted_at is not None:
        raise NotFound
    if user.id not in (c.user_id, post.author_id):
        raise NotFound
    c.deleted_at = clock()
    await session.commit()


async def report(session: AsyncSession, user: User, post: ReadingPost, reason: str) -> None:
    if post.author_id == user.id:
        raise PostError("내 게시물은 신고할 수 없어요")
    if reason not in REASONS:
        raise PostError("신고 이유를 골라 주세요")
    await ratelimit.check(session, f"post-report:{user.id}", REPORT_LIMIT, ratelimit.WINDOW_S, REPORT_MSG)
    session.add(PostReport(post_id=post.id, reporter_id=user.id, reason=reason))
    try:
        await session.commit()
    except IntegrityError:  # 같은 게시물을 두 번 신고하면 처음 것만 남긴다
        await session.rollback()


# ---------------------------------------------------------------- 읽기


@dataclass
class Card:
    post: ReadingPost
    author: User
    likes: int = 0
    liked: bool = False
    comments: int = 0

    @property
    def status_label(self) -> str:
        return STATUSES.get(self.post.status, "")

    @property
    def visibility_label(self) -> str:
        return VISIBILITY.get(self.post.visibility, "")


async def _cards(session: AsyncSession, viewer_id: int, posts: list[ReadingPost]) -> list[Card]:
    if not posts:
        return []
    ids = [p.id for p in posts]
    authors = {
        u.id: u for u in await session.scalars(select(User).where(User.id.in_({p.author_id for p in posts})))
    }
    likes = dict(
        (
            await session.execute(
                select(PostLike.post_id, func.count())
                .where(PostLike.post_id.in_(ids))
                .group_by(PostLike.post_id)
            )
        ).all()
    )
    mine = set(
        await session.scalars(
            select(PostLike.post_id).where(PostLike.post_id.in_(ids), PostLike.user_id == viewer_id)
        )
    )
    _, blocked = await circle(session, viewer_id)
    comments = dict(
        (
            await session.execute(
                select(PostComment.post_id, func.count())
                .where(
                    PostComment.post_id.in_(ids),
                    PostComment.deleted_at.is_(None),
                    PostComment.user_id.not_in(blocked or {0}),
                )
                .group_by(PostComment.post_id)
            )
        ).all()
    )
    return [
        Card(p, authors[p.author_id], likes.get(p.id, 0), p.id in mine, comments.get(p.id, 0))
        for p in posts
        if p.author_id in authors
    ]


async def card(session: AsyncSession, viewer_id: int, post: ReadingPost) -> Card:
    return (await _cards(session, viewer_id, [post]))[0]


async def _page(
    session: AsyncSession, stmt, before: int | None, size: int
) -> tuple[list[ReadingPost], int | None]:
    if before:
        stmt = stmt.where(ReadingPost.id < before)
    rows = list(await session.scalars(stmt.order_by(ReadingPost.id.desc()).limit(size + 1)))
    more = len(rows) > size
    rows = rows[:size]
    return rows, (rows[-1].id if more and rows else None)


async def feed(
    session: AsyncSession,
    viewer_id: int,
    tab: str = "friends",
    before: int | None = None,
    size: int = FEED_PAGE,
) -> tuple[list[Card], int | None]:
    """friends: 나와 친구의 게시물(보이는 것만) · all: '가입한 모두' 게시물. 최신순, 커서는 마지막 id."""
    friends, blocked = await circle(session, viewer_id)
    cond = _visible(viewer_id, friends, blocked)
    if tab == "all":
        cond = and_(cond, ReadingPost.visibility == "all")
    else:
        cond = and_(cond, ReadingPost.author_id.in_(friends | {viewer_id}))
    posts, cursor = await _page(session, select(ReadingPost).where(cond), before, size)
    return await _cards(session, viewer_id, posts), cursor


async def comments_of(
    session: AsyncSession, viewer_id: int, post: ReadingPost
) -> list[tuple[PostComment, User]]:
    """댓글(오래된 것 → 최신), 지운 것과 차단 관계인 사람의 것은 뺀다."""
    _, blocked = await circle(session, viewer_id)
    rows = await session.execute(
        select(PostComment, User)
        .join(User, User.id == PostComment.user_id)
        .where(
            PostComment.post_id == post.id,
            PostComment.deleted_at.is_(None),
            PostComment.user_id.not_in(blocked or {0}),
        )
        .order_by(PostComment.id)
        .limit(500)
    )
    return [(c, u) for c, u in rows.all()]


@dataclass
class Profile:
    user: User
    state: str  # self · none · friends · outgoing · incoming · blocking
    posts: int
    friends: int
    done: int


async def profile(session: AsyncSession, viewer_id: int, user_id: int) -> Profile:
    """프로필 머리. 차단 관계면(누가 막았든) NotFound."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound
    friends, blocked = await circle(session, viewer_id)
    if user_id in blocked:
        raise NotFound
    posts = await session.scalar(
        select(func.count())
        .select_from(ReadingPost)
        .where(ReadingPost.author_id == user_id, _visible(viewer_id, friends, blocked))
    )
    done = await session.scalar(
        select(func.count())
        .select_from(LibraryEntry)
        .where(LibraryEntry.user_id == user_id, LibraryEntry.shelf == Shelf.DONE)
    )
    return Profile(
        user=user,
        state=await state_with(session, viewer_id, user_id),
        posts=posts or 0,
        friends=await friend_count(session, user_id),
        done=done or 0,
    )


async def grid(
    session: AsyncSession, viewer_id: int, user_id: int, before: int | None = None, size: int = GRID_PAGE
) -> tuple[list[ReadingPost], int | None]:
    friends, blocked = await circle(session, viewer_id)
    stmt = select(ReadingPost).where(ReadingPost.author_id == user_id, _visible(viewer_id, friends, blocked))
    return await _page(session, stmt, before, size)


async def grass(session: AsyncSession, user_id: int, weeks: int = 20) -> list[dict] | None:
    """프로필의 작은 잔디(읽은 날) — 목표 기능에서 읽기만 한다(정산 · 행 만들기 없음). 꺼져 있으면 None."""
    if not get_settings().feature_goals:
        return None
    from app.models_goals import ReadingGoal
    from app.services import goals

    day = goals.reading_day(goals.clock())
    start = day - dt.timedelta(days=day.weekday()) - dt.timedelta(weeks=weeks - 1)
    pages = (await goals.history(session, user_id, start)).pages
    row = await session.get(ReadingGoal, user_id)
    target = row.daily_pages if row is not None else goals.DEFAULT_GOAL
    cells, d = [], start
    while d <= day:
        p = pages.get(d, 0)
        cells.append(
            {"level": goals.level(p, target), "label": f"{d.month}월 {d.day}일 · {p}쪽" if p else ""}
        )
        d += dt.timedelta(days=1)
    return cells


# ---------------------------------------------------------------- 관리자


async def reported(session: AsyncSession, limit: int = 100) -> list[dict]:
    """신고가 들어온 게시물 — 처리 안 한 것 먼저, 신고 많은 순."""
    counts = (
        select(
            PostReport.post_id,
            func.count().label("n"),
            func.max(PostReport.created_at).label("last"),
            func.min(case((PostReport.resolved.is_(True), 1), else_=0)).label("done"),
        )
        .group_by(PostReport.post_id)
        .subquery()
    )
    rows = await session.execute(
        select(ReadingPost, User.nickname, counts.c.n, counts.c.last, counts.c.done)
        .join(counts, counts.c.post_id == ReadingPost.id)
        .join(User, User.id == ReadingPost.author_id)
        .order_by(counts.c.done, counts.c.n.desc(), ReadingPost.id.desc())
        .limit(limit)
    )
    out = []
    for post, nick, n, last, done in rows.all():
        tally: dict[str, int] = {}
        for r in await session.scalars(select(PostReport.reason).where(PostReport.post_id == post.id)):
            label = REASONS.get(r, r)
            tally[label] = tally.get(label, 0) + 1
        out.append(
            {
                "post": post,
                "author": nick,
                "reports": n,
                "last": _aware(last),
                "reasons": tally,
                "resolved": bool(done),
            }
        )
    return out


async def admin_act(session: AsyncSession, admin: User, post: ReadingPost, action: str) -> None:
    """hide: 모두에게서 내린다(소프트 삭제) · resolve: 그대로 두고 처리 표시만."""
    if action == "hide" and post.deleted_at is None:
        post.deleted_at, post.deleted_by = clock(), admin.id
    for rep in await session.scalars(select(PostReport).where(PostReport.post_id == post.id)):
        rep.resolved = True
    await session.commit()
