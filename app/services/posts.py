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
from dataclasses import dataclass, field

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core import ratelimit
from app.core.config import get_settings
from app.models import Book, LibraryEntry, Shelf, User
from app.models_chat import Friendship
from app.models_posts import (
    PostComment,
    PostCommentLike,
    PostCommentReport,
    PostLike,
    PostPhoto,
    PostReport,
    PostSave,
    ProfileBio,
    ReadingPost,
)
from app.services import post_photos as photo_rules

KST = dt.timezone(dt.timedelta(hours=9), "KST")
STATUSES = {"done": "읽었어요", "reading": "읽는 중"}
VISIBILITY = {"friends": "친구만", "all": "가입한 모두"}
DEFAULT_VISIBILITY = "all"
MAX_CAPTION, MAX_QUOTE, MAX_COMMENT, MAX_BIO = 2200, 200, 300, 150  # 캡션 · 소개는 인스타와 같은 길이
FEED_PAGE, GRID_PAGE = 10, 30
ASPECTS = {"1:1": (1, 1), "4:5": (4, 5)}  # 게시물 전체에 한 번 고르는 사진 비율
REASONS = {"spam": "광고·도배", "abuse": "불쾌한 내용", "spoiler": "가리지 않은 스포일러", "other": "기타"}

# 속도 제한(10분 창) — 하루 한 번 규칙과 별개로 연타를 막는다
POST_LIMIT, POST_MSG = 10, "게시물을 너무 자주 올렸어요. 10분 뒤에 다시 해 주세요"
LIKE_LIMIT, LIKE_MSG = 120, "좋아요를 너무 빨리 누르고 있어요. 잠시 뒤에 다시 해 주세요"
COMMENT_LIMIT, COMMENT_MSG = 30, "댓글을 너무 빨리 달고 있어요. 잠시 뒤에 다시 해 주세요"
REPORT_LIMIT, REPORT_MSG = 10, "신고를 너무 자주 보냈어요. 10분 뒤에 다시 해 주세요"
SAVE_LIMIT, SAVE_MSG = 120, "저장을 너무 빨리 누르고 있어요. 잠시 뒤에 다시 해 주세요"
BIO_LIMIT, BIO_MSG = 20, "소개를 너무 자주 바꿨어요. 10분 뒤에 다시 해 주세요"
PHOTO_DAY_MSG = f"사진은 하루에 {photo_rules.DAILY_PHOTOS}장까지 올릴 수 있어요. 내일 다시 올려 주세요"
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
    """프로필 사진을 볼 수 있는 사람들 — /avatars 규칙(본인 · 같은 대결방 · 맺은 친구)과 같다.

    나머지는 깨진 그림 대신 이름 첫 글자로 보인다.
    """
    from app.models import RoomMember

    mine = select(RoomMember.room_id).where(RoomMember.user_id == viewer_id)
    out = set(await session.scalars(select(RoomMember.user_id).where(RoomMember.room_id.in_(mine))))
    if get_settings().feature_chat:  # /avatars 는 채팅(친구) 기능이 켜져 있을 때만 친구에게 사진을 준다
        out |= (await circle(session, viewer_id))[0]
    return out | {viewer_id}


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


# 하루 사진 수는 rate_limits 표에 (사람, 한국 날짜) 키로 센다 — 게시물을 지워 사진 행이 사라져도 수는 남는다.
# 창을 아주 길게 잡아(1,000일) 한 키 안에서 창이 바뀌지 않게 하고, 날짜가 바뀌면 키가 바뀐다.
PHOTO_WINDOW_S = 86_400 * 1000


def _photo_key(user_id: int) -> str:
    return f"post-photos:{user_id}:{today()}"


async def photos_today(session: AsyncSession, user_id: int) -> int:
    return int(
        await session.scalar(
            select(ratelimit.rate_limits.c.count).where(ratelimit.rate_limits.c.key == _photo_key(user_id))
        )
        or 0
    )


async def create(
    session: AsyncSession,
    user: User,
    pick: BookPick,
    fields: dict,
    photos: list[photo_rules.CleanPhoto] | None = None,
) -> ReadingPost:
    """책은 늘 있어야 한다(읽기 앱). 사진은 0~5장 — 없으면 책 표지가 그림이 된다."""
    photos = photos or []
    if len(photos) > photo_rules.MAX_PHOTOS:
        raise PostError(f"사진은 {photo_rules.MAX_PHOTOS}장까지 올릴 수 있어요")
    if await posted_today(session, user.id, pick):
        raise PostError(DAILY_MSG)
    if photos and await photos_today(session, user.id) + len(photos) > photo_rules.DAILY_PHOTOS:
        raise PostError(PHOTO_DAY_MSG)
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
        await session.flush()
        for i, ph in enumerate(photos):
            session.add(
                PostPhoto(
                    post_id=post.id,
                    idx=i,
                    data=ph.data,
                    width=ph.width,
                    height=ph.height,
                    digest=ph.digest,
                    created_at=clock(),
                )
            )
        await session.commit()
    except IntegrityError as e:  # 같은 책을 동시에 두 번 — 하나만 남는다
        await session.rollback()
        raise PostError(DAILY_MSG) from e
    for _ in photos:  # 하루 사진 수(최대 5번, 게시물이 올라간 뒤에만 센다)
        await ratelimit.hit(session, _photo_key(user.id), photo_rules.DAILY_PHOTOS, PHOTO_WINDOW_S)
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


async def _drop_photos(session: AsyncSession, post_id: int) -> None:
    """사진 바이트는 지운 게시물과 함께 바로 지운다(저장 공간 · 사생활). 게시물 행은 소프트 삭제로 남는다."""
    await session.execute(delete(PostPhoto).where(PostPhoto.post_id == post_id))


async def remove(session: AsyncSession, user: User, post: ReadingPost) -> None:
    _author_only(post, user)
    post.deleted_at, post.deleted_by = clock(), user.id
    await _drop_photos(session, post.id)
    await session.commit()


# ---------------------------------------------------------------- 좋아요 · 댓글 · 신고


async def like_count(session: AsyncSession, post_id: int) -> int:
    return await session.scalar(select(func.count()).where(PostLike.post_id == post_id)) or 0


async def toggle_like(
    session: AsyncSession, user: User, post: ReadingPost, want: bool | None = None
) -> tuple[bool, int]:
    """버튼은 켜고 끈다(want=None). 그림을 두 번 탭하면 켜기만 한다(want=True, 몇 번이어도 같다).

    (켜졌나, 수)를 돌려준다.
    """
    await ratelimit.check(session, f"post-like:{user.id}", LIKE_LIMIT, ratelimit.WINDOW_S, LIKE_MSG)
    post_id = post.id
    mine = await session.get(PostLike, (post_id, user.id))
    if mine is not None and want:
        liked = True
    elif mine is not None:
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


async def _live_comment(
    session: AsyncSession, viewer_id: int, post: ReadingPost, comment_id: int
) -> PostComment:
    """이 게시물의, 지우지 않았고 차단 관계가 아닌 사람의 댓글(답글이면 윗댓글도 살아 있어야)."""
    c = await session.get(PostComment, comment_id)
    if c is None or c.post_id != post.id or c.deleted_at is not None:
        raise NotFound
    _, blocked = await circle(session, viewer_id)
    if c.user_id in blocked:
        raise NotFound
    if c.parent_id is not None:
        parent = await session.get(PostComment, c.parent_id)
        if parent is None or parent.deleted_at is not None or parent.user_id in blocked:
            raise NotFound
    return c


async def add_comment(
    session: AsyncSession, user: User, post: ReadingPost, body: str, parent_id: int | None = None
) -> PostComment:
    """댓글 또는 답글. 답글은 한 단계만 — 답글에 답하면 같은 윗댓글 묶음에 붙는다(인스타와 같다)."""
    text = clean(body, MAX_COMMENT)
    if not text:
        raise PostError("댓글을 적어 주세요")
    parent = None
    if parent_id is not None:
        try:
            parent = await _live_comment(session, user.id, post, parent_id)
        except NotFound as e:
            raise PostError("답글을 달 수 없는 댓글이에요") from e
    await ratelimit.check(session, f"post-comment:{user.id}", COMMENT_LIMIT, ratelimit.WINDOW_S, COMMENT_MSG)
    c = PostComment(post_id=post.id, user_id=user.id, body=text, created_at=clock())
    if parent is not None:
        c.parent_id = parent.parent_id or parent.id
    session.add(c)
    await session.commit()
    return c


async def toggle_comment_like(
    session: AsyncSession, user: User, post: ReadingPost, comment_id: int
) -> tuple[bool, int]:
    c = await _live_comment(session, user.id, post, comment_id)
    await ratelimit.check(session, f"post-like:{user.id}", LIKE_LIMIT, ratelimit.WINDOW_S, LIKE_MSG)
    mine = await session.get(PostCommentLike, (c.id, user.id))
    if mine is not None:
        await session.delete(mine)
        liked = False
    else:
        session.add(PostCommentLike(comment_id=c.id, user_id=user.id))
        liked = True
    try:
        await session.commit()
    except IntegrityError:  # 동시에 두 번
        await session.rollback()
        liked = True
    n = await session.scalar(select(func.count()).where(PostCommentLike.comment_id == c.id)) or 0
    return liked, n


async def report_comment(
    session: AsyncSession, user: User, post: ReadingPost, comment_id: int, reason: str
) -> None:
    c = await _live_comment(session, user.id, post, comment_id)
    if c.user_id == user.id:
        raise PostError("내 댓글은 신고할 수 없어요")
    if reason not in REASONS:
        raise PostError("신고 이유를 골라 주세요")
    await ratelimit.check(session, f"post-report:{user.id}", REPORT_LIMIT, ratelimit.WINDOW_S, REPORT_MSG)
    session.add(PostCommentReport(comment_id=c.id, reporter_id=user.id, reason=reason))
    try:
        await session.commit()
    except IntegrityError:  # 같은 댓글을 두 번 신고하면 처음 것만 남긴다
        await session.rollback()


async def delete_comment(session: AsyncSession, user: User, post: ReadingPost, comment_id: int) -> None:
    """내 댓글, 또는 내 게시물에 달린 댓글을 지운다."""
    c = await session.get(PostComment, comment_id)
    if c is None or c.post_id != post.id or c.deleted_at is not None:
        raise NotFound
    if user.id not in (c.user_id, post.author_id):
        raise NotFound
    c.deleted_at = clock()  # 윗댓글을 지우면 답글들도 함께 보이지 않는다(comments_of 가 거른다)
    await session.commit()


# ---------------------------------------------------------------- 저장 · 보내기 · 소개


async def toggle_save(session: AsyncSession, user: User, post: ReadingPost) -> bool:
    """책갈피 — 켜고 끈다. 저장한 사람만 안다."""
    await ratelimit.check(session, f"post-save:{user.id}", SAVE_LIMIT, ratelimit.WINDOW_S, SAVE_MSG)
    mine = await session.get(PostSave, (post.id, user.id))
    if mine is not None:
        await session.delete(mine)
        await session.commit()
        return False
    session.add(PostSave(post_id=post.id, user_id=user.id, created_at=clock()))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
    return True


async def share_targets(session: AsyncSession, me: int) -> list[User]:
    """보내기 시트에 보일 친구들(이름순). 채팅 기능이 꺼져 있으면 빈 목록."""
    if not get_settings().feature_chat:
        return []
    friends, _ = await circle(session, me)
    if not friends:
        return []
    return list(await session.scalars(select(User).where(User.id.in_(friends)).order_by(User.nickname)))


async def send_to_friend(session: AsyncSession, user: User, post: ReadingPost, friend_id: int) -> None:
    """종이비행기: 친구와의 개인 채팅에 /p/{id} 링크 한 줄을 보낸다.

    채팅은 볼 수 있는 사람에게만 그 링크를 게시물 카드로 보여 준다.

    친구가 아니면(차단 · 신청 중 포함) NotFound. 채팅의 보내기 규칙(속도 제한 포함)을 그대로 쓴다.
    """
    if not get_settings().feature_chat:
        raise NotFound
    from app.services import chat as chat_service

    try:
        conv = await chat_service.dm_conv(session, user, friend_id)
    except chat_service.NotFound as e:
        raise NotFound from e
    await chat_service.send(session, user, conv, f"/p/{post.id}")


async def bio_of(session: AsyncSession, user_id: int) -> str:
    row = await session.get(ProfileBio, user_id)
    return row.bio if row is not None else ""


async def set_bio(session: AsyncSession, user: User, text: str) -> str:
    bio = clean(text, MAX_BIO)
    await ratelimit.check(session, f"post-bio:{user.id}", BIO_LIMIT, ratelimit.WINDOW_S, BIO_MSG)
    row = await session.get(ProfileBio, user.id)
    if row is None:
        session.add(ProfileBio(user_id=user.id, bio=bio, updated_at=clock()))
    else:
        row.bio, row.updated_at = bio, clock()
    await session.commit()
    return bio


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
class Photo:
    """사진 한 장의 정보(바이트 없이) — 주소에 내용 해시를 붙여 캐시를 오래 둔다."""

    post_id: int
    idx: int
    width: int
    height: int
    digest: str

    @property
    def url(self) -> str:
        return f"/media/p/{self.post_id}/{self.idx}.jpg?v={self.digest}"

    @property
    def ratio(self) -> str:
        return f"{self.width} / {self.height}"


async def photos_of(session: AsyncSession, post_ids) -> dict[int, list[Photo]]:
    ids = list(post_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(PostPhoto.post_id, PostPhoto.idx, PostPhoto.width, PostPhoto.height, PostPhoto.digest)
        .where(PostPhoto.post_id.in_(ids))
        .order_by(PostPhoto.post_id, PostPhoto.idx)
    )
    out: dict[int, list[Photo]] = {}
    for row in rows.all():
        out.setdefault(row[0], []).append(Photo(*row))
    return out


async def photo_bytes(session: AsyncSession, post_id: int, idx: int) -> bytes | None:
    return await session.scalar(
        select(PostPhoto.data).where(PostPhoto.post_id == post_id, PostPhoto.idx == idx)
    )


async def recent_authors(session: AsyncSession, viewer_id: int, author_ids) -> set[int]:
    """24시간 안에 (이 사람에게 보이는) 게시물을 올린 사람들 — 머리 사진에 고리를 그린다.

    인스타의 스토리 고리 자리다(스토리가 없으니 '최근 게시물'로)."""
    ids = set(author_ids)
    if not ids:
        return set()
    friends, blocked = await circle(session, viewer_id)
    since = clock() - dt.timedelta(hours=24)
    return set(
        await session.scalars(
            select(ReadingPost.author_id)
            .where(
                ReadingPost.author_id.in_(ids),
                ReadingPost.created_at >= since,
                _visible(viewer_id, friends, blocked),
            )
            .distinct()
        )
    )


@dataclass
class Card:
    post: ReadingPost
    author: User
    likes: int = 0
    liked: bool = False
    comments: int = 0
    photos: list[Photo] = field(default_factory=list)
    saved: bool = False
    friend_liker: str | None = None  # 'OO님 외 n명' 에 쓸 친구 이름(가장 최근에 누른 친구)
    recent: bool = False  # 24시간 안에 게시물을 올린 사람이면 머리 사진에 고리
    suggested: bool = False  # 피드 전체 탭: 친구가 아닌 사람의 공개 게시물('새로운 사람' 표시)
    rel: str = ""  # 그 사람과의 관계(none · outgoing · incoming …) — 친구 신청 단추를 그릴지

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
    friends, blocked = await circle(session, viewer_id)
    parent = aliased(PostComment)
    comments = dict(
        (
            await session.execute(
                select(PostComment.post_id, func.count())
                .outerjoin(parent, parent.id == PostComment.parent_id)
                .where(
                    PostComment.post_id.in_(ids),
                    PostComment.deleted_at.is_(None),
                    PostComment.user_id.not_in(blocked or {0}),
                    or_(
                        PostComment.parent_id.is_(None),
                        and_(parent.deleted_at.is_(None), parent.user_id.not_in(blocked or {0})),
                    ),
                )
                .group_by(PostComment.post_id)
            )
        ).all()
    )
    saved = set(
        await session.scalars(
            select(PostSave.post_id).where(PostSave.post_id.in_(ids), PostSave.user_id == viewer_id)
        )
    )
    likers: dict[int, str] = {}
    if friends:
        rows = await session.execute(
            select(PostLike.post_id, User.nickname)
            .join(User, User.id == PostLike.user_id)
            .where(PostLike.post_id.in_(ids), PostLike.user_id.in_(friends - blocked))
            .order_by(PostLike.created_at.desc())
        )
        for pid, nick in rows.all():
            likers.setdefault(pid, nick)
    photos = await photos_of(session, ids)
    recent = await recent_authors(session, viewer_id, authors)
    return [
        Card(
            p,
            authors[p.author_id],
            likes.get(p.id, 0),
            p.id in mine,
            comments.get(p.id, 0),
            photos=photos.get(p.id, []),
            saved=p.id in saved,
            friend_liker=likers.get(p.id),
            recent=p.author_id in recent,
        )
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


@dataclass
class Comment:
    """댓글 한 줄 — 답글은 윗댓글의 replies 에 들어간다(한 단계만)."""

    c: PostComment
    who: User
    likes: int = 0
    liked: bool = False
    replies: list["Comment"] = field(default_factory=list)


async def comments_of(session: AsyncSession, viewer_id: int, post: ReadingPost) -> list[Comment]:
    """댓글(오래된 것 → 최신)과 그 아래 답글. 지운 것 · 차단 관계인 사람의 것은 빼고,
    윗댓글이 지워졌거나 안 보이면 그 답글들도 뺀다."""
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
        .limit(1000)
    )
    pairs = rows.all()
    ids = [c.id for c, _ in pairs]
    likes, mine = {}, set()
    if ids:
        likes = dict(
            (
                await session.execute(
                    select(PostCommentLike.comment_id, func.count())
                    .where(PostCommentLike.comment_id.in_(ids))
                    .group_by(PostCommentLike.comment_id)
                )
            ).all()
        )
        mine = set(
            await session.scalars(
                select(PostCommentLike.comment_id).where(
                    PostCommentLike.comment_id.in_(ids), PostCommentLike.user_id == viewer_id
                )
            )
        )
    top: dict[int, Comment] = {}
    out: list[Comment] = []
    for c, u in pairs:
        item = Comment(c, u, likes.get(c.id, 0), c.id in mine)
        if c.parent_id is None:
            top[c.id] = item
            out.append(item)
        elif c.parent_id in top:  # 윗댓글이 살아 있고 보일 때만(윗댓글 id 가 늘 더 작다)
            top[c.parent_id].replies.append(item)
    return out


def comment_total(threads: list[Comment]) -> int:
    return sum(1 + len(t.replies) for t in threads)


@dataclass
class Profile:
    user: User
    state: str  # self · none · friends · outgoing · incoming · blocking
    posts: int
    friends: int
    done: int
    bio: str = ""
    recent: bool = False


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
        bio=await bio_of(session, user_id),
        recent=user_id in await recent_authors(session, viewer_id, {user_id}),
    )


@dataclass
class Tile:
    """격자 한 칸 — 첫 사진(없으면 표지)과 사진 수(여러 장이면 겹친 네모 표시)."""

    post: ReadingPost
    photo: Photo | None = None
    count: int = 0
    big: bool = False  # 탐색 격자의 큰 칸

    @property
    def many(self) -> bool:
        return self.count > 1


async def tiles_of(session: AsyncSession, posts: list[ReadingPost]) -> list[Tile]:
    photos = await photos_of(session, [p.id for p in posts])
    return [Tile(p, (photos.get(p.id) or [None])[0], len(photos.get(p.id, []))) for p in posts]


async def grid(
    session: AsyncSession, viewer_id: int, user_id: int, before: int | None = None, size: int = GRID_PAGE
) -> tuple[list[Tile], int | None]:
    friends, blocked = await circle(session, viewer_id)
    stmt = select(ReadingPost).where(ReadingPost.author_id == user_id, _visible(viewer_id, friends, blocked))
    posts, cursor = await _page(session, stmt, before, size)
    return await tiles_of(session, posts), cursor


async def saved_grid(
    session: AsyncSession, viewer_id: int, page: int = 0, size: int = GRID_PAGE
) -> tuple[list[Tile], bool]:
    """내가 저장한 게시물 — 저장한 순서(최신이 먼저).

    지금 볼 수 없게 된 게시물(지움 · 친구 끊김 · 차단)은 뺀다."""
    friends, blocked = await circle(session, viewer_id)
    rows = list(
        await session.scalars(
            select(ReadingPost)
            .join(PostSave, PostSave.post_id == ReadingPost.id)
            .where(PostSave.user_id == viewer_id, _visible(viewer_id, friends, blocked))
            .order_by(PostSave.created_at.desc(), PostSave.post_id.desc())
            .offset(page * size)
            .limit(size + 1)
        )
    )
    return await tiles_of(session, rows[:size]), len(rows) > size


EXPLORE_PAGE = 30  # 다섯 칸이 한 묶음(큰 칸 1 + 작은 칸 4) — 묶음이 딱 맞게


async def explore(
    session: AsyncSession, viewer_id: int, before: int | None = None, size: int = EXPLORE_PAGE
) -> tuple[list[Tile], int | None]:
    """탐색: '가입한 모두' 게시물(차단 · 지운 것 빼고) 격자. 다섯 칸마다 한 칸이 크다 — 왼쪽 · 오른쪽 번갈아.

    큰 칸은 (가능하면) 사진이 있는 게시물이 맡는다 — 묶음 안에서 순서를 바꾸지는 않고 자리만 정한다.
    """
    friends, blocked = await circle(session, viewer_id)
    cond = and_(_visible(viewer_id, friends, blocked), ReadingPost.visibility == "all")
    posts, cursor = await _page(session, select(ReadingPost).where(cond), before, size)
    tiles = await tiles_of(session, posts)
    for i, t in enumerate(tiles):
        t.big = i % 5 == (0 if (i // 5) % 2 == 0 else 2)
    return tiles, cursor


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


@dataclass
class Tiles:
    """내 프로필의 작은 칸들 — 각 기능의 서비스에서 읽기만 한다. 기능이 꺼져 있으면 None."""

    goal: dict | None = None  # {"pages": 3, "target": 5, "pct": 60}
    grass: list[dict] | None = None
    mileage: int | None = None

    @property
    def any(self) -> bool:
        return self.goal is not None or self.grass is not None or self.mileage is not None


async def tiles(session: AsyncSession, user_id: int) -> Tiles:
    out = Tiles()
    settings = get_settings()
    if settings.feature_goals:
        from app.models_goals import ReadingGoal
        from app.services import goals

        day = goals.reading_day(goals.clock())
        row = await session.get(ReadingGoal, user_id)
        target = row.daily_pages if row is not None else goals.DEFAULT_GOAL
        pages = (await goals.history(session, user_id, day)).pages.get(day, 0)
        out.goal = {"pages": pages, "target": target, "pct": min(100, round(pages * 100 / max(target, 1)))}
        out.grass = await grass(session, user_id, weeks=8)
    if settings.feature_mileage:
        from app.services import mileage

        card = await mileage.card(session, user_id)
        out.mileage = card.balance if card is not None else None
    return out


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
        await _drop_photos(session, post.id)
    for rep in await session.scalars(select(PostReport).where(PostReport.post_id == post.id)):
        rep.resolved = True
    await session.commit()


async def reported_comments(session: AsyncSession, limit: int = 100) -> list[dict]:
    """신고가 들어온 댓글 — 처리 안 한 것 먼저, 신고 많은 순. 지운 댓글도 원문을 보여 준다."""
    counts = (
        select(
            PostCommentReport.comment_id,
            func.count().label("n"),
            func.min(case((PostCommentReport.resolved.is_(True), 1), else_=0)).label("done"),
        )
        .group_by(PostCommentReport.comment_id)
        .subquery()
    )
    rows = await session.execute(
        select(PostComment, User.nickname, counts.c.n, counts.c.done)
        .join(counts, counts.c.comment_id == PostComment.id)
        .join(User, User.id == PostComment.user_id)
        .order_by(counts.c.done, counts.c.n.desc(), PostComment.id.desc())
        .limit(limit)
    )
    return [
        {"comment": c, "author": nick, "reports": n, "resolved": bool(done)}
        for c, nick, n, done in rows.all()
    ]


async def admin_comment_act(session: AsyncSession, comment: PostComment, action: str) -> None:
    """hide: 댓글을 내린다(소프트 삭제) · resolve: 그대로 두고 처리 표시만."""
    if action == "hide" and comment.deleted_at is None:
        comment.deleted_at = clock()
    for rep in await session.scalars(
        select(PostCommentReport).where(PostCommentReport.comment_id == comment.id)
    ):
        rep.resolved = True
    await session.commit()
