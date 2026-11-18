"""친구 맺기 + 채팅(개인 · 방).

친구
- 두 사람 사이에는 friendships 한 행만 있다(작은 id, 큰 id). 신청 → 수락/거절/취소, 친구 끊기, 차단.
- 차단당한 사람은 신청도 개인 메시지도 못 보내고, 차단한 사람에게는 그 사람의 방 메시지가 보이지 않는다.

방 채팅 = 스포일러 안전
- '책 이야기'는 보낼 때 서버가 그 사람의 검증된 쪽(verified_chunk)을 도장으로 찍는다.
  클라이언트 값은 믿지 않는다.
- 도장보다 덜 읽은 독자에게는 본문 없이 자리표시(누가 · 언제 · n쪽을 읽으면 열려요)만 준다.
  판정은 반응과 같은 함수(social.page_open)를 쓴다. 따라잡으면 다음 폴링에 본문이 온다.
- 고치기는 없다(지우고 다시 보낸다) — 도장이 낡지 않는다.

개인 채팅
- 수락한 친구끼리만. 도장이 없고, '가리기'는 탭할 때까지 흐리게 보여 줄 뿐 본문은 전달된다.
"""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.models import Book, Room, RoomMember, User
from app.models_chat import ChatMessage, ChatRead, ChatReport, ChatThread, Friendship
from app.services import social

MAX_BODY = 500
PAGE_SIZE = 50
LOCKED_MAX = 50  # 폴링 한 번에 다시 살펴볼 잠긴 메시지 수
PREVIEW = 60
LOCKED_TEXT = "잠긴 메시지"
DELETED_TEXT = "삭제된 메시지"

SEND_LIMIT, SEND_WINDOW = 20, 60
SEND_MSG = "메시지를 너무 빨리 보내고 있어요. 잠시 뒤에 다시 보내 주세요"
FRIEND_LIMIT, FRIEND_WINDOW = 20, 600
FRIEND_MSG = "친구 신청을 너무 자주 했어요. 10분 뒤에 다시 해 주세요"
REPORT_LIMIT, REPORT_WINDOW = 20, 600
REPORT_MSG = "신고를 너무 자주 했어요. 10분 뒤에 다시 해 주세요"

PENDING, ACCEPTED, BLOCKED = "pending", "accepted", "blocked"


def clock() -> dt.datetime:  # 테스트에서 바꾼다
    return dt.datetime.now(dt.UTC)


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=dt.UTC)  # SQLite는 시간대를 저장하지 않는다


class ChatError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 거절. status는 API 응답 코드."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message, self.status = message, status


class NotFound(LookupError):
    """없거나 볼 수 없다 — 둘을 구분하지 않고 404로 답한다(있는지조차 알리지 않는다)."""


# ---------------------------------------------------------------- 친구


def pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


async def relation(session: AsyncSession, me: int, other: int) -> Friendship | None:
    low, high = pair(me, other)
    return await session.scalar(
        select(Friendship).where(Friendship.user_low == low, Friendship.user_high == high)
    )


def _blocks(f: Friendship, who: int) -> bool:
    return f.low_blocks if who == f.user_low else f.high_blocks


def state_of(f: Friendship | None, me: int) -> str:
    """내 쪽에서 본 관계: none · friends · outgoing · incoming · blocking(내가 막음) · blocked(내가 막힘)."""
    if f is None:
        return "none"
    if f.status == BLOCKED:
        return "blocking" if _blocks(f, me) else "blocked"
    if f.status == ACCEPTED:
        return "friends"
    return "outgoing" if f.requester_id == me else "incoming"


async def _user(session: AsyncSession, user_id: int) -> User:
    u = await session.get(User, user_id)
    if u is None:
        raise NotFound
    return u


async def send_request(session: AsyncSession, me: User, other_id: int) -> str:
    """친구 신청. 상대가 먼저 보낸 신청이 있으면 그대로 수락한다. 결과 상태를 돌려준다."""
    if other_id == me.id:
        raise ChatError("나에게는 친구 신청을 할 수 없어요")
    await _user(session, other_id)
    await ratelimit.check(session, f"friend:{me.id}", FRIEND_LIMIT, FRIEND_WINDOW, FRIEND_MSG)
    f = await relation(session, me.id, other_id)
    state = state_of(f, me.id)
    if state == "friends":
        raise ChatError("이미 친구예요", 409)
    if state == "outgoing":
        raise ChatError("이미 친구 신청을 보냈어요", 409)
    if state == "blocking":
        raise ChatError("차단을 먼저 풀어 주세요", 409)
    if state == "blocked":
        raise ChatError("친구 신청을 보낼 수 없어요", 409)
    if state == "incoming":  # 서로 신청 = 친구
        f.status = ACCEPTED
        await session.commit()
        return "friends"
    low, high = pair(me.id, other_id)
    session.add(Friendship(user_low=low, user_high=high, requester_id=me.id, status=PENDING))
    await session.commit()
    return "outgoing"


async def accept(session: AsyncSession, me: User, other_id: int) -> None:
    f = await relation(session, me.id, other_id)
    if state_of(f, me.id) != "incoming":
        raise NotFound
    f.status = ACCEPTED
    await session.commit()


async def _drop(session: AsyncSession, me: User, other_id: int, expect: str) -> None:
    f = await relation(session, me.id, other_id)
    if state_of(f, me.id) != expect:
        raise NotFound
    await session.delete(f)
    await session.commit()


async def decline(session: AsyncSession, me: User, other_id: int) -> None:
    await _drop(session, me, other_id, "incoming")


async def cancel(session: AsyncSession, me: User, other_id: int) -> None:
    await _drop(session, me, other_id, "outgoing")


async def unfriend(session: AsyncSession, me: User, other_id: int) -> None:
    await _drop(session, me, other_id, "friends")


async def block(session: AsyncSession, me: User, other_id: int) -> None:
    """차단 — 친구·신청은 사라진다. 상대가 이미 나를 막았어도 내 차단을 따로 적는다."""
    if other_id == me.id:
        raise ChatError("나를 차단할 수는 없어요")
    await _user(session, other_id)
    f = await relation(session, me.id, other_id)
    if f is None:
        low, high = pair(me.id, other_id)
        f = Friendship(user_low=low, user_high=high, requester_id=me.id, status=BLOCKED)
        session.add(f)
    f.status = BLOCKED
    if me.id == f.user_low:
        f.low_blocks = True
    else:
        f.high_blocks = True
    await session.commit()


async def unblock(session: AsyncSession, me: User, other_id: int) -> None:
    f = await relation(session, me.id, other_id)
    if f is None or f.status != BLOCKED or not _blocks(f, me.id):
        raise NotFound
    if me.id == f.user_low:
        f.low_blocks = False
    else:
        f.high_blocks = False
    if not (f.low_blocks or f.high_blocks):
        await session.delete(f)
    await session.commit()


def _mine(me: int):
    return or_(Friendship.user_low == me, Friendship.user_high == me)


def _other(f: Friendship, me: int) -> int:
    return f.user_high if f.user_low == me else f.user_low


async def _relations(session: AsyncSession, me: int) -> list[Friendship]:
    return list(await session.scalars(select(Friendship).where(_mine(me))))


async def blocked_by_me(session: AsyncSession, me: int) -> set[int]:
    """내가 차단한 사람들 — 이 사람들의 메시지는 나에게 보이지 않는다."""
    return {_other(f, me) for f in await _relations(session, me) if f.status == BLOCKED and _blocks(f, me)}


async def _users(session: AsyncSession, ids) -> dict[int, User]:
    ids = set(ids)
    if not ids:
        return {}
    return {u.id: u for u in await session.scalars(select(User).where(User.id.in_(ids)))}


@dataclass
class People:
    friends: list[User] = field(default_factory=list)
    incoming: list[User] = field(default_factory=list)
    outgoing: list[User] = field(default_factory=list)
    blocking: list[User] = field(default_factory=list)


async def people(session: AsyncSession, me: int) -> People:
    rels = await _relations(session, me)
    users = await _users(session, (_other(f, me) for f in rels))
    out = People()
    for f in sorted(rels, key=lambda f: f.id, reverse=True):
        u = users.get(_other(f, me))
        state = state_of(f, me)
        if u is not None and state in ("friends", "incoming", "outgoing", "blocking"):
            getattr(out, state).append(u)
    out.friends.sort(key=lambda u: u.nickname)
    return out


async def friend_ids(session: AsyncSession, me: int) -> set[int]:
    return {_other(f, me) for f in await _relations(session, me) if f.status == ACCEPTED}


async def are_friends(session: AsyncSession, me: int, other: int) -> bool:
    f = await relation(session, me, other)
    return f is not None and f.status == ACCEPTED


async def _states(session: AsyncSession, me: int, users: list[User]) -> list[tuple[User, str]]:
    rels = {_other(f, me): f for f in await _relations(session, me)}
    return [(u, state_of(rels.get(u.id), me)) for u in users]


async def search(session: AsyncSession, me: int, nickname: str) -> list[tuple[User, str]]:
    """닉네임이 정확히 같은 사람(나와 나를 차단한 사람은 빼고)."""
    q = nickname.strip()
    if not q:
        return []
    found = list(
        await session.scalars(
            select(User).where(User.nickname == q, User.id != me).order_by(User.id).limit(20)
        )
    )
    return [(u, s) for u, s in await _states(session, me, found) if s != "blocked"]


async def roommates(session: AsyncSession, me: int) -> list[tuple[User, str]]:
    """나와 같은 대결방에 있는 사람 중 아직 아무 관계가 없는 사람 — 친구 신청 후보."""
    my_rooms = select(RoomMember.room_id).where(RoomMember.user_id == me)
    ids = select(RoomMember.user_id).where(RoomMember.room_id.in_(my_rooms), RoomMember.user_id != me)
    found = list(await session.scalars(select(User).where(User.id.in_(ids)).order_by(User.nickname)))
    return [(u, s) for u, s in await _states(session, me, found) if s == "none"]


# ---------------------------------------------------------------- 대화


@dataclass
class Conv:
    """내가 열 수 있는 대화 하나. thread 는 아직 메시지가 없으면 None."""

    kind: str  # "room" | "dm"
    key: int  # room_id 또는 친구 user_id
    title: str
    thread: ChatThread | None
    room: Room | None = None
    book: Book | None = None
    other: User | None = None

    @property
    def href(self) -> str:
        return f"/chat/{self.kind}/{self.key}"


async def room_conv(session: AsyncSession, me: User, room_id: int) -> Conv:
    room = await session.get(Room, room_id)
    member = room is not None and await session.scalar(
        select(RoomMember.user_id).where(RoomMember.room_id == room_id, RoomMember.user_id == me.id)
    )
    if not member:
        raise NotFound
    book = await session.get(Book, room.book_id)
    thread = await session.scalar(select(ChatThread).where(ChatThread.room_id == room_id))
    return Conv("room", room_id, room.name, thread, room=room, book=book)


async def dm_conv(session: AsyncSession, me: User, other_id: int) -> Conv:
    if other_id == me.id or not await are_friends(session, me.id, other_id):
        raise NotFound
    other = await _user(session, other_id)
    low, high = pair(me.id, other_id)
    thread = await session.scalar(
        select(ChatThread).where(ChatThread.user_low == low, ChatThread.user_high == high)
    )
    return Conv("dm", other_id, other.nickname, thread, other=other)


async def _ensure_thread(session: AsyncSession, me: User, conv: Conv) -> ChatThread:
    if conv.thread is not None:
        return conv.thread
    if conv.kind == "room":
        t = ChatThread(room_id=conv.key)
    else:
        low, high = pair(me.id, conv.key)
        t = ChatThread(user_low=low, user_high=high)
    session.add(t)
    await session.flush()
    conv.thread = t
    return t


def clean_body(text: str | None) -> str:
    body = (text or "").strip()
    if not body:
        raise ChatError("메시지를 적어 주세요")
    if len(body) > MAX_BODY:
        raise ChatError(f"메시지는 {MAX_BODY}자까지 보낼 수 있어요")
    return body


async def _reader_page(session: AsyncSession, me: int, conv: Conv) -> int | None:
    """방 채팅에서 이 사람이 검증받은 마지막 청크. 개인 채팅은 None."""
    if conv.kind != "room":
        return None
    return await social.verified_of(session, me, conv.room.book_id)


async def send(
    session: AsyncSession,
    me: User,
    conv: Conv,
    body: str | None,
    book_talk: bool = True,
    veiled: bool = False,
) -> dict:
    """보내기. 방의 '책 이야기'는 내 검증된 쪽을 서버에서 읽어 도장을 찍는다(요청의 쪽 값은 받지도 않는다)."""
    text = clean_body(body)
    await ratelimit.check(session, f"chat:{me.id}", SEND_LIMIT, SEND_WINDOW, SEND_MSG)
    thread = await _ensure_thread(session, me, conv)
    msg = ChatMessage(thread_id=thread.id, user_id=me.id, body=text, created_at=clock())
    if conv.kind == "room" and book_talk:
        msg.book_id = conv.room.book_id
        msg.page = await social.verified_of(session, me.id, conv.room.book_id)
    if conv.kind == "dm":
        msg.veiled = bool(veiled)
    session.add(msg)
    await session.flush()
    await _mark(session, thread.id, me.id, msg.id)
    await session.commit()
    return render(msg, me, me.id, await _reader_page(session, me.id, conv))


def avatar_of(u: User) -> str | None:
    return social.avatar_path(u.id, u.avatar)


def render(m: ChatMessage, author: User | None, me: int, reader_page: int | None) -> dict:
    """메시지 한 줄의 응답. 잠기거나 지운 메시지는 'body' 키 자체를 넣지 않는다."""
    out = {
        "id": m.id,
        "user_id": m.user_id,
        "nickname": author.nickname if author else "(알 수 없음)",
        "avatar": avatar_of(author) if author else None,
        "at": _aware(m.created_at).isoformat() if m.created_at else None,
        "mine": m.user_id == me,
        "kind": "book" if m.page is not None else "chat",
    }
    if m.page is not None:
        out["page"] = m.page + 1  # 화면의 쪽 번호(1부터)
    if m.deleted_at is not None:
        out["deleted"] = True
        return out
    if (
        m.user_id != me
        and m.page is not None
        and not social.page_open(-1 if reader_page is None else reader_page, m.page)
    ):
        out["sealed"] = True
        return out
    out["body"] = m.body
    if m.veiled:
        out["veiled"] = True
    return out


async def _mark(session: AsyncSession, thread_id: int, me: int, upto: int) -> None:
    """마지막으로 본 위치를 앞으로만 옮긴다."""
    r = await session.get(ChatRead, (thread_id, me))
    if r is None:
        session.add(ChatRead(thread_id=thread_id, user_id=me, last_read_id=upto))
    elif upto > (r.last_read_id or 0):
        r.last_read_id = upto


async def messages(
    session: AsyncSession,
    me: User,
    conv: Conv,
    after: int | None = None,
    before: int | None = None,
    locked: list[int] | None = None,
    deleted_since: dt.datetime | None = None,
) -> dict:
    """대화의 메시지. after 가 없으면 최근 50개(before 가 있으면 그보다 이전 50개).

    폴링(after)은 새 메시지와 함께, 클라이언트가 잠긴 채 들고 있는 메시지(locked) 중 바뀐 것,
    deleted_since 뒤에 지워진 메시지 id를 돌려준다. 볼 때마다 읽은 위치를 옮긴다.
    """
    now = clock()
    reader_page = await _reader_page(session, me.id, conv)
    empty = {
        "items": [],
        "updates": [],
        "deleted": [],
        "last_id": after or 0,
        "has_more": False,
        "now": now.isoformat(),
        "my_page": None if reader_page is None else reader_page + 1,
    }
    if conv.thread is None:
        return empty
    tid = conv.thread.id
    hidden = await blocked_by_me(session, me.id) if conv.kind == "room" else set()
    q = select(ChatMessage).where(ChatMessage.thread_id == tid)
    if hidden:
        q = q.where(ChatMessage.user_id.not_in(hidden))
    has_more = False
    if after is not None:
        rows = list(
            await session.scalars(q.where(ChatMessage.id > after).order_by(ChatMessage.id).limit(200))
        )
    else:
        if before is not None:
            q = q.where(ChatMessage.id < before)
        rows = list(await session.scalars(q.order_by(ChatMessage.id.desc()).limit(PAGE_SIZE + 1)))
        has_more = len(rows) > PAGE_SIZE
        rows = list(reversed(rows[:PAGE_SIZE]))
    updates: list[ChatMessage] = []
    if locked:
        ids = [i for i in locked[:LOCKED_MAX] if after is None or i <= after]
        if ids:
            updates = list(await session.scalars(q.where(ChatMessage.id.in_(ids)).order_by(ChatMessage.id)))
    deleted: list[int] = []
    if deleted_since is not None:
        deleted = list(
            await session.scalars(
                select(ChatMessage.id).where(
                    ChatMessage.thread_id == tid,
                    ChatMessage.deleted_at.is_not(None),
                    ChatMessage.deleted_at > deleted_since,
                )
            )
        )
    authors = await _users(session, (m.user_id for m in rows + updates))
    items = [render(m, authors.get(m.user_id), me.id, reader_page) for m in rows]
    newest = await session.scalar(select(func.max(ChatMessage.id)).where(ChatMessage.thread_id == tid)) or 0
    if before is None:  # 최신을 본 것 — 지난 메시지를 거슬러 볼 때는 읽은 위치를 옮기지 않는다
        await _mark(session, tid, me.id, newest)
        await session.commit()
    # 다음 폴링의 커서. 가려진(차단) 메시지 뒤로도 넘어가도록 대화의 최신 id까지.
    # 한 번에 다 못 줬으면 받은 데까지
    capped = after is not None and len(rows) == 200
    last_id = rows[-1].id if capped else max(after or 0, newest)
    return empty | {
        "items": items,
        "updates": [render(m, authors.get(m.user_id), me.id, reader_page) for m in updates],
        "deleted": deleted,
        "last_id": last_id,
        "has_more": has_more,
    }


async def _message_conv(session: AsyncSession, me: User, message_id: int) -> tuple[ChatMessage, Conv]:
    """메시지와 그 대화 — 내가 볼 수 없는 대화의 메시지면 NotFound."""
    m = await session.get(ChatMessage, message_id)
    if m is None:
        raise NotFound
    t = await session.get(ChatThread, m.thread_id)
    if t.room_id is not None:
        conv = await room_conv(session, me, t.room_id)
    else:
        other = t.user_high if t.user_low == me.id else t.user_low
        if me.id not in (t.user_low, t.user_high):
            raise NotFound
        conv = await dm_conv(session, me, other)
    if m.user_id in (await blocked_by_me(session, me.id) if conv.kind == "room" else set()):
        raise NotFound
    return m, conv


async def remove(session: AsyncSession, me: User, message_id: int) -> None:
    """내 메시지는 내가, 방 메시지는 방장이 지운다(소프트 삭제 → '삭제된 메시지')."""
    m, conv = await _message_conv(session, me, message_id)
    owner = conv.kind == "room" and conv.room.owner_id == me.id
    if m.user_id != me.id and not owner:
        raise NotFound
    if m.deleted_at is None:
        m.deleted_at, m.deleted_by = clock(), me.id
        await session.commit()


async def report(session: AsyncSession, me: User, message_id: int, reason: str | None) -> None:
    m, _ = await _message_conv(session, me, message_id)
    if m.user_id == me.id:
        raise ChatError("내 메시지는 신고할 수 없어요")
    await ratelimit.check(session, f"chat-report:{me.id}", REPORT_LIMIT, REPORT_WINDOW, REPORT_MSG)
    exists = await session.scalar(
        select(ChatReport.id).where(ChatReport.message_id == m.id, ChatReport.reporter_id == me.id)
    )
    if exists is None:
        session.add(
            ChatReport(message_id=m.id, reporter_id=me.id, reason=(reason or "").strip()[:200] or None)
        )
        await session.commit()


# ---------------------------------------------------------------- 목록 · 안 읽은 수


def _my_threads(me: int):
    """내가 볼 수 있는 대화(thread id) — 내가 들어 있는 방, 친구(수락)와의 개인 대화."""
    my_rooms = select(RoomMember.room_id).where(RoomMember.user_id == me)
    friends = select(Friendship.id).where(
        Friendship.status == ACCEPTED,
        Friendship.user_low == ChatThread.user_low,
        Friendship.user_high == ChatThread.user_high,
    )
    return select(ChatThread.id).where(
        or_(
            ChatThread.room_id.in_(my_rooms),
            and_(or_(ChatThread.user_low == me, ChatThread.user_high == me), friends.exists()),
        )
    )


async def _unread(session: AsyncSession, me: int, thread_ids, hidden: set[int]) -> dict[int, int]:
    q = (
        select(ChatMessage.thread_id, func.count())
        .outerjoin(ChatRead, and_(ChatRead.thread_id == ChatMessage.thread_id, ChatRead.user_id == me))
        .where(
            ChatMessage.thread_id.in_(thread_ids),
            ChatMessage.user_id != me,
            ChatMessage.deleted_at.is_(None),
            ChatMessage.id > func.coalesce(ChatRead.last_read_id, 0),
        )
        .group_by(ChatMessage.thread_id)
    )
    if hidden:
        q = q.where(ChatMessage.user_id.not_in(hidden))
    return dict((await session.execute(q)).all())


async def unread_total(session: AsyncSession, me: int) -> int:
    """메뉴의 점 — 화면을 그릴 때 한 번 센다(다른 화면에서 따로 폴링하지 않는다)."""
    counts = await _unread(session, me, _my_threads(me), await blocked_by_me(session, me))
    return sum(counts.values())


def preview(m: ChatMessage | None, me: int, reader_page: int | None) -> str | None:
    """목록 미리보기 — 잠긴(도장보다 덜 읽음) 메시지와 가린 메시지의 본문은 절대 내보내지 않는다."""
    if m is None:
        return None
    if m.deleted_at is not None:
        return DELETED_TEXT
    if m.veiled:
        return LOCKED_TEXT
    if (
        m.user_id != me
        and m.page is not None
        and not social.page_open(-1 if reader_page is None else reader_page, m.page)
    ):
        return LOCKED_TEXT
    return m.body if len(m.body) <= PREVIEW else m.body[:PREVIEW] + "…"


async def conversations(session: AsyncSession, me: User, kind: str) -> list[dict]:
    """개인(dm) 또는 방(room) 탭의 대화 목록: 이름 · 마지막 활동 · 안 읽은 수 · 안전한 미리보기."""
    convs: list[Conv] = []
    pages: dict[int, int] = {}  # room_id -> 내 검증 쪽
    if kind == "room":
        rows = (
            await session.execute(
                select(Room, Book)
                .join(RoomMember, RoomMember.room_id == Room.id)
                .join(Book, Book.id == Room.book_id)
                .where(RoomMember.user_id == me.id)
            )
        ).all()
        threads = {
            t.room_id: t
            for t in await session.scalars(
                select(ChatThread).where(ChatThread.room_id.in_([r.id for r, _ in rows] or [0]))
            )
        }
        for room, book in rows:
            convs.append(Conv("room", room.id, room.name, threads.get(room.id), room=room, book=book))
            pages[room.id] = await social.verified_of(session, me.id, book.id)
    else:
        ids = await friend_ids(session, me.id)
        users = await _users(session, ids)
        threads = {}
        if ids:
            for t in await session.scalars(
                select(ChatThread).where(
                    or_(
                        and_(ChatThread.user_low == me.id, ChatThread.user_high.in_(ids)),
                        and_(ChatThread.user_high == me.id, ChatThread.user_low.in_(ids)),
                    )
                )
            ):
                threads[t.user_high if t.user_low == me.id else t.user_low] = t
        for uid, u in users.items():
            convs.append(Conv("dm", uid, u.nickname, threads.get(uid), other=u))
    hidden = await blocked_by_me(session, me.id) if kind == "room" else set()
    tids = [c.thread.id for c in convs if c.thread]
    last: dict[int, ChatMessage] = {}
    unread: dict[int, int] = {}
    if tids:
        newest = select(func.max(ChatMessage.id)).where(ChatMessage.thread_id.in_(tids))
        if hidden:
            newest = newest.where(ChatMessage.user_id.not_in(hidden))
        newest = newest.group_by(ChatMessage.thread_id)
        last = {
            m.thread_id: m
            for m in await session.scalars(select(ChatMessage).where(ChatMessage.id.in_(newest)))
        }
        unread = await _unread(session, me.id, tids, hidden)
    out = []
    for c in convs:
        m = last.get(c.thread.id) if c.thread else None
        out.append(
            {
                "kind": c.kind,
                "key": c.key,
                "href": c.href,
                "title": c.title,
                "subtitle": c.book.title if c.book and c.book.title != c.title else None,
                "user": c.other,
                "at": _aware(m.created_at) if m else None,
                "unread": unread.get(c.thread.id, 0) if c.thread else 0,
                "preview": preview(m, me.id, pages.get(c.key) if c.kind == "room" else None),
                "mine": m is not None and m.user_id == me.id,
            }
        )
    epoch = dt.datetime.min.replace(tzinfo=dt.UTC)
    out.sort(key=lambda d: (d["at"] or epoch, d["title"]), reverse=True)
    return out
