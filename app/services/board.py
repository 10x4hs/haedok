"""같이 읽을 사람 모집(당근처럼 글을 띄워 두고 참가) — 글 하나가 보통 대결방 하나다.

방은 app/services/races.py의 create_race로 만들고, 참가도 초대 코드와 같은 races.join을 거친다.
정원은 recruit_posts.seats를 `seats < capacity`일 때만 올리는 UPDATE 한 문장으로 지킨다:
Postgres는 그 행을 잠그고 조건을 다시 보며, SQLite는 쓰기를 한 줄로 세우므로
마지막 자리를 동시에 눌러도 한 명만 들어간다.
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Book, RacePresence, RaceQuote, Room, RoomMember, User
from app.models_board import RecruitPost, RecruitReport
from app.services import posts as posts_service
from app.services import races as race_service
from app.services.search import FULLTEXT_SOURCES

OPEN, CLOSED = "open", "closed"
PACES = (3, 5, 10, 20, 0)  # 하루 쪽 수. 0은 '자유'
DEFAULT_PACE = 5
MINUTES_PER_PAGE = 1  # 한 쪽을 읽는 데 드는 대략의 시간(카드에 '약 5분'으로 보여 준다)
MIN_CAPACITY, MAX_CAPACITY, DEFAULT_CAPACITY = 2, 8, 4
MAX_PITCH = 140
MAX_OPEN_POSTS = 3  # 한 사람이 동시에 열어 둘 수 있는 모집 글
TOO_MANY_MSG = f"모집 글은 한 번에 {MAX_OPEN_POSTS}개까지 열 수 있어요. 지난 글을 마감하고 올려 주세요"
RACE_DAYS = 14  # 모집 글로 여는 방의 기간(races.DURATIONS 중 가장 긴 것)
FACES = 4  # 모집 줄에 얼굴로 그리는 사람 수(나머지는 +n)
REASONS = {"spam": "광고·도배", "abuse": "불쾌한 내용", "other": "기타"}


class BoardError(ValueError):
    pass


def clock() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def readable(book: Book | None) -> bool:
    """누구나 앱 안에서 열 수 있는 책인가 — 퍼블릭 도메인 전문이 있고 주인이 없는 책만."""
    return bool(
        book is not None
        and book.has_fulltext
        and book.owner_id is None
        and book.source in FULLTEXT_SOURCES
        and book.total_chunks > 0
    )


def clean_pitch(text: str | None) -> str:
    """한 줄 소개: 줄바꿈·연속 공백을 한 칸으로. 화면에서는 자동 이스케이프로만 보여 준다."""
    return " ".join((text or "").split())[:MAX_PITCH]


def pace_label(pace: int) -> str:
    if not pace:
        return "내 속도대로"
    return f"하루 {pace}쪽 · 약 {pace * MINUTES_PER_PAGE}분"


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


@dataclass
class PostView:
    post: RecruitPost
    room: Room
    book: Book
    host: User
    count: int  # 지금 방에 있는 사람(방장 포함)
    known: int = 0  # 그중 나와 다른 방에서 같이 읽어 본 사람
    is_member: bool = False
    members: list[User] = field(default_factory=list)
    faces: list[User] = field(default_factory=list)  # 줄에 그리는 얼굴(먼저 들어온 순, 최대 FACES명)

    @property
    def more(self) -> int:
        """얼굴로 그리지 못한 사람 수(+n)."""
        return max(self.count - len(self.faces), 0)

    @property
    def empty_seats(self) -> int:
        return max(self.post.capacity - self.count, 0)

    @property
    def seats_text(self) -> str:
        """자리 그림의 글 대신(스크린 리더): '6명 중 1명, 5자리 남음'."""
        if self.empty_seats == 0:
            return f"{self.post.capacity}명 중 {self.count}명, 자리가 다 찼어요"
        return f"{self.post.capacity}명 중 {self.count}명, {self.empty_seats}자리 남음"

    @property
    def ago(self) -> str:
        return posts_service.when(self.post.created_at)

    @property
    def is_open(self) -> bool:
        return self.post.status == OPEN and not race_service.is_over(self.room)

    @property
    def full(self) -> bool:
        return self.count >= self.post.capacity or self.post.seats >= self.post.capacity

    @property
    def left(self) -> int:
        return max(self.post.capacity - max(self.count, self.post.seats), 0)

    @property
    def joinable(self) -> bool:
        return self.is_open and not self.full and not self.is_member

    @property
    def pace_text(self) -> str:
        return pace_label(self.post.pace)

    @property
    def state(self) -> str:
        """카드·상세에 쓰는 짧은 상태 문구."""
        if self.post.status != OPEN:
            return "모집 마감"
        if race_service.is_over(self.room):
            return "기간이 끝났어요"
        if self.full:
            return "자리가 다 찼어요"
        return f"남은 자리 {self.left}"


# --- 만들기 ---


async def open_count(session: AsyncSession, user_id: int) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(RecruitPost)
            .where(RecruitPost.author_id == user_id, RecruitPost.status == OPEN)
        )
        or 0
    )


async def create_post(
    session: AsyncSession, user: User, book: Book | None, pitch: str, pace: int, capacity: int
) -> RecruitPost:
    if not readable(book):
        raise BoardError("모두가 앱에서 읽을 수 있는 고전만 모집할 수 있어요")
    body = clean_pitch(pitch)
    if not body:
        raise BoardError("한 줄 소개를 적어 주세요")
    if pace not in PACES:
        raise BoardError("읽는 속도를 골라 주세요")
    if not MIN_CAPACITY <= capacity <= MAX_CAPACITY:
        raise BoardError(f"인원은 {MIN_CAPACITY}~{MAX_CAPACITY}명으로 정해 주세요")
    if await open_count(session, user.id) >= MAX_OPEN_POSTS:
        raise BoardError(TOO_MANY_MSG)
    room = await race_service.create_race(session, user, book, RACE_DAYS)  # 방장은 여기서 첫 멤버가 된다
    post = RecruitPost(
        room_id=room.id,
        author_id=user.id,
        book_id=book.id,
        pitch=body,
        pace=pace,
        capacity=capacity,
        seats=1,
        status=OPEN,
    )
    session.add(post)
    await session.commit()
    return post


# --- 읽기 ---


async def _known_ids(session: AsyncSession, user_id: int) -> set[int]:
    """나와 한 번이라도 같은 방에 있었던 사람들(나 제외) — 카드의 '아는 사람 n명'."""
    mine = select(RoomMember.room_id).where(RoomMember.user_id == user_id)
    rows = await session.scalars(select(RoomMember.user_id).where(RoomMember.room_id.in_(mine)))
    return set(rows) - {user_id}


async def _views(session: AsyncSession, rows, viewer_id: int, known: set[int]) -> list[PostView]:
    out = []
    room_ids = [room.id for _, room, _, _ in rows]
    members: dict[int, list[User]] = {}
    if room_ids:
        for rid, u in (
            await session.execute(
                select(RoomMember.room_id, User)
                .join(User, User.id == RoomMember.user_id)
                .where(RoomMember.room_id.in_(room_ids))
                .order_by(RoomMember.joined_at, User.id)
            )
        ).all():
            members.setdefault(rid, []).append(u)
    for post, room, book, host in rows:
        people = members.get(room.id, [])
        ids = [u.id for u in people]
        out.append(
            PostView(
                post=post,
                room=room,
                book=book,
                host=host,
                count=len(ids),
                known=len(known & (set(ids) - {viewer_id})),
                is_member=viewer_id in ids,
                faces=people[:FACES],
            )
        )
    return out


def _base():
    return (
        select(RecruitPost, Room, Book, User)
        .join(Room, Room.id == RecruitPost.room_id)
        .join(Book, Book.id == RecruitPost.book_id)
        .join(User, User.id == RecruitPost.author_id)
    )


async def board(session: AsyncSession, viewer_id: int, limit: int | None = None) -> list[PostView]:
    """모집 중인 글, 최신순. 마감했거나 기간이 끝난 방의 글은 빠진다."""
    stmt = _base().where(RecruitPost.status == OPEN).order_by(RecruitPost.id.desc()).limit(200)
    rows = [r for r in (await session.execute(stmt)).all() if not race_service.is_over(r[1])]
    views = await _views(session, rows, viewer_id, await _known_ids(session, viewer_id))
    return views[:limit] if limit else views


async def preview(session: AsyncSession, viewer_id: int, n: int = 3) -> list[PostView]:
    """대결 화면 맨 위 줄: 내가 아직 안 들어간, 자리가 남은 글 n개."""
    return [v for v in await board(session, viewer_id) if v.joinable][:n]


async def view(session: AsyncSession, post_id: int, viewer_id: int) -> PostView | None:
    row = (await session.execute(_base().where(RecruitPost.id == post_id))).first()
    if row is None:
        return None
    v = (await _views(session, [row], viewer_id, await _known_ids(session, viewer_id)))[0]
    v.members = list(
        await session.scalars(
            select(User)
            .join(RoomMember, RoomMember.user_id == User.id)
            .where(RoomMember.room_id == v.room.id)
            .order_by(RoomMember.joined_at, User.id)
        )
    )
    return v


# --- 참가 · 나가기 ---


async def join(session: AsyncSession, post: RecruitPost, user: User) -> str | None:
    """참가시키고 None, 못 하면 이유. 이미 멤버면 아무것도 하지 않고 None(두 번 눌러도 같다)."""
    post_id, room_id, capacity = post.id, post.room_id, post.capacity
    room = await session.get(Room, room_id)
    if room is None:
        return "없어진 모집 글이에요"
    if await race_service.is_member(session, room, user.id):
        return None
    if post.status != OPEN:
        return "모집이 마감됐어요"
    if race_service.is_over(room):
        return "기간이 끝난 모집이에요"
    if await race_service.member_count(session, room) >= capacity:
        return "자리가 다 찼어요"
    # 자리 잡기: 조건을 만족할 때만 한 칸 올린다 — 같은 트랜잭션에서 races.join이 멤버를 넣고 커밋한다
    claimed = await session.execute(
        update(RecruitPost)
        .where(
            RecruitPost.id == post_id, RecruitPost.status == OPEN, RecruitPost.seats < RecruitPost.capacity
        )
        .values(seats=RecruitPost.seats + 1)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await session.rollback()
        return "자리가 다 찼어요"
    if reason := await race_service.join(session, room, user):  # 초대 코드와 같은 길
        await session.rollback()
        return reason
    # races.join이 '이미 멤버'로 커밋 없이 돌아왔다면 잡은 자리를 돌려놓는다(커밋했다면 아무 일도 없다)
    await session.rollback()
    return None


async def _resync(session: AsyncSession, post_id: int, room_id: int) -> None:
    """자리 수를 실제 멤버 수로 맞춘다(나가기·계정 삭제로 비는 자리를 돌려준다)."""
    actual = select(func.count()).where(RoomMember.room_id == room_id).scalar_subquery()
    await session.execute(
        update(RecruitPost)
        .where(RecruitPost.id == post_id)
        .values(seats=actual)
        .execution_options(synchronize_session=False)
    )


async def leave(session: AsyncSession, post: RecruitPost, user: User) -> None:
    if post.author_id == user.id:
        raise BoardError("방장은 나갈 수 없어요. 모집을 마감하거나 글을 지워 주세요")
    post_id, room_id = post.id, post.room_id
    await session.execute(
        delete(RacePresence).where(RacePresence.room_id == room_id, RacePresence.user_id == user.id)
    )
    await session.execute(
        delete(RoomMember).where(RoomMember.room_id == room_id, RoomMember.user_id == user.id)
    )
    await _resync(session, post_id, room_id)
    await session.commit()


# --- 방장 ---


def _host_only(post: RecruitPost, user: User) -> None:
    if post.author_id != user.id:
        raise BoardError("방장만 할 수 있어요")


async def close(session: AsyncSession, post: RecruitPost, user: User) -> None:
    _host_only(post, user)
    if post.status != CLOSED:
        post.status, post.closed_at = CLOSED, clock()
        await session.commit()


async def remove(session: AsyncSession, post: RecruitPost, user: User) -> None:
    """글을 지운다. 방에 방장 혼자면 방도 같이 지우고, 이미 들어온 사람이 있으면 방은 남겨 둔다."""
    _host_only(post, user)
    room_id = post.room_id
    alone = (await session.scalar(select(func.count()).where(RoomMember.room_id == room_id)) or 0) <= 1
    await session.execute(delete(RecruitReport).where(RecruitReport.post_id == post.id))
    await session.delete(post)
    if alone:  # SQLite는 FK CASCADE를 켜 두지 않으므로 딸린 행을 직접 지운다
        for model in (RacePresence, RaceQuote, RoomMember):
            await session.execute(delete(model).where(model.room_id == room_id))
        await session.execute(delete(Room).where(Room.id == room_id))
    await session.commit()


# --- 신고 ---


async def report(session: AsyncSession, post: RecruitPost, user: User, reason: str) -> None:
    if post.author_id == user.id:
        raise BoardError("내 글은 신고할 수 없어요")
    if reason not in REASONS:
        raise BoardError("신고 이유를 골라 주세요")
    session.add(RecruitReport(post_id=post.id, reporter_id=user.id, reason=reason))
    try:
        await session.commit()
    except IntegrityError:  # 같은 글을 두 번 신고하면 처음 것만 남긴다
        await session.rollback()


async def reported(session: AsyncSession, limit: int = 50) -> list[dict]:
    """관리자용: 신고가 들어온 글, 신고 많은 순."""
    counts = (
        select(
            RecruitReport.post_id, func.count().label("n"), func.max(RecruitReport.created_at).label("last")
        )
        .group_by(RecruitReport.post_id)
        .subquery()
    )
    rows = await session.execute(
        select(RecruitPost, Book.title, User.nickname, counts.c.n, counts.c.last)
        .join(counts, counts.c.post_id == RecruitPost.id)
        .join(Book, Book.id == RecruitPost.book_id)
        .join(User, User.id == RecruitPost.author_id)
        .order_by(counts.c.n.desc(), RecruitPost.id.desc())
        .limit(limit)
    )
    out = []
    for post, title, nick, n, last in rows.all():
        reasons = await session.scalars(select(RecruitReport.reason).where(RecruitReport.post_id == post.id))
        tally: dict[str, int] = {}
        for r in reasons:
            tally[REASONS.get(r, r)] = tally.get(REASONS.get(r, r), 0) + 1
        out.append(
            {"post": post, "title": title, "host": nick, "reports": n, "last": _aware(last), "reasons": tally}
        )
    return out


async def admin_close(session: AsyncSession, post: RecruitPost) -> None:
    if post.status != CLOSED:
        post.status, post.closed_at = CLOSED, clock()
        await session.commit()
