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

- 방은 두 채널로 나뉜다: '# 책 이야기'(쪽 도장, 잠김) · '# 수다'(도장 없음). 채널이 도장을 정한다.
- 답장 인용 · 반응 · 공지 · 목록 미리보기 어디에도 잠긴 본문은 나가지 않는다.

개인 채팅
- 수락한 친구끼리만. 도장이 없다. ||스포일러||는 탭할 때까지 흐리게 보여 줄 뿐 본문은 전달된다.
"""

import datetime as dt
import re
from dataclasses import dataclass, field

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.models import Book, Room, RoomMember, User
from app.models_chat import ChatMessage, ChatReaction, ChatRead, ChatReport, ChatThread, Friendship
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
REACT_LIMIT, REACT_WINDOW = 60, 60
REACT_MSG = "반응을 너무 빨리 바꾸고 있어요. 잠시 뒤에 다시 해 주세요"
MAX_NOTICE = 140
QUOTE = 80
ACTIVE_FOR = dt.timedelta(minutes=10)  # 이 안에 책을 펼쳤으면 '지금 읽는 중' 링
CHANNELS = ("book", "chat")
SPOILER = re.compile(r"\|\|(.+?)\|\|", re.S)

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


async def roommates(session: AsyncSession, me: int) -> list[tuple[User, str, list[str]]]:
    """나와 같은 대결방에 있는 사람 중 아직 아무 관계가 없는 사람 — 친구 신청 후보와 함께 있는 방 이름."""
    my_rooms = select(RoomMember.room_id).where(RoomMember.user_id == me)
    rows = (
        await session.execute(
            select(User, Room.name)
            .join(RoomMember, RoomMember.user_id == User.id)
            .join(Room, Room.id == RoomMember.room_id)
            .where(RoomMember.room_id.in_(my_rooms), User.id != me)
            .order_by(User.nickname, Room.id)
        )
    ).all()
    rooms: dict[int, list[str]] = {}
    users: dict[int, User] = {}
    for u, name in rows:
        users[u.id] = u
        rooms.setdefault(u.id, []).append(name)
    states = await _states(session, me, list(users.values()))
    return [(u, s, rooms[u.id]) for u, s in states if s == "none"]


async def shared_rooms(session: AsyncSession, me: int, other: int) -> list[str]:
    """두 사람이 함께 있는 대결방 이름."""
    mine = select(RoomMember.room_id).where(RoomMember.user_id == me)
    return list(
        await session.scalars(
            select(Room.name)
            .join(RoomMember, RoomMember.room_id == Room.id)
            .where(RoomMember.user_id == other, Room.id.in_(mine))
            .order_by(Room.id)
        )
    )


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


def clean_body(text: str | None, limit: int = MAX_BODY) -> str:
    body = (text or "").strip()
    if not body:
        raise ChatError("메시지를 적어 주세요")
    if len(body) > limit:
        raise ChatError(f"{limit}자까지 보낼 수 있어요")
    return body


def channel_of(m: ChatMessage) -> str:
    """방 메시지의 채널 — 쪽 도장이 있으면 '# 책 이야기', 없으면 '# 수다'. 개인 채팅은 늘 chat."""
    return "book" if m.page is not None else "chat"


CHANNEL_SQL = case((ChatMessage.page.is_(None), "chat"), else_="book")


def mask_spoilers(text: str) -> str:
    """미리보기 · 인용에서 ||스포일러||를 가린다."""
    return SPOILER.sub("[스포일러]", text)


def snippet(text: str, n: int) -> str:
    t = " ".join(mask_spoilers(text).split())
    return t if len(t) <= n else t[:n] + "…"


async def _reader_page(session: AsyncSession, me: int, conv: Conv) -> int | None:
    """방 채팅에서 이 사람이 검증받은 마지막 청크. 개인 채팅은 None."""
    if conv.kind != "room":
        return None
    return await social.verified_of(session, me, conv.room.book_id)


def _sealed(m: ChatMessage, me: int, reader_page: int | None) -> bool:
    """잠김 판정 — 반응(reactions_for)과 같은 social.page_open 규칙. 내 메시지는 늘 열린다."""
    return (
        m.user_id != me
        and m.page is not None
        and not social.page_open(-1 if reader_page is None else reader_page, m.page)
    )


def _open_for(m: ChatMessage, me: int, reader_page: int | None) -> bool:
    return m.deleted_at is None and not m.veiled and not _sealed(m, me, reader_page)


async def send(
    session: AsyncSession,
    me: User,
    conv: Conv,
    body: str | None,
    channel: str = "book",
    reply_to: int | None = None,
) -> dict:
    """보내기. 방에서는 채널이 도장을 정한다: '# 책 이야기'면 내 검증된 쪽을 서버가 읽어 찍고,
    '# 수다'면 찍지 않는다. 요청의 쪽 값은 받지도 않는다."""
    text = clean_body(body)
    channel = channel if conv.kind == "room" and channel in CHANNELS else "chat"
    reader_page = await _reader_page(session, me.id, conv)
    quoted = None
    if reply_to is not None:
        quoted = await session.get(ChatMessage, reply_to)
        hidden = await blocked_by_me(session, me.id) if conv.kind == "room" else set()
        if (
            quoted is None
            or conv.thread is None
            or quoted.thread_id != conv.thread.id
            or channel_of(quoted) != channel
            or quoted.user_id in hidden
            or quoted.deleted_at is not None
            or _sealed(quoted, me.id, reader_page)
        ):
            raise ChatError("답장할 수 없는 메시지예요")
    await ratelimit.check(session, f"chat:{me.id}", SEND_LIMIT, SEND_WINDOW, SEND_MSG)
    thread = await _ensure_thread(session, me, conv)
    msg = ChatMessage(thread_id=thread.id, user_id=me.id, body=text, created_at=clock())
    if channel == "book":
        msg.book_id = conv.room.book_id
        msg.page = reader_page
    if quoted is not None:
        msg.reply_to_id = quoted.id
    session.add(msg)
    await session.flush()
    await _mark(session, thread.id, me.id, channel, msg.id)
    await session.commit()
    return (await render_many(session, me.id, [msg], reader_page))[0]


def avatar_of(u: User) -> str | None:
    return social.avatar_path(u.id, u.avatar)


def _quote(q: ChatMessage | None, author: User | None, me: int, reader_page: int | None) -> dict | None:
    if q is None:
        return None
    out = {"id": q.id, "nickname": author.nickname if author else "(알 수 없음)", "mine": q.user_id == me}
    if q.deleted_at is not None:
        out["text"] = DELETED_TEXT
        out["gone"] = True
    elif not _open_for(q, me, reader_page):
        out["text"] = LOCKED_TEXT  # 잠긴 인용 — 원문은 절대 싣지 않는다
        out["locked"] = True
    else:
        out["text"] = snippet(q.body, QUOTE)
    return out


POST_LINK = re.compile(r"(?:https?://[^\s/]+)?/p/(\d{1,9})\b")
MAX_CARDS = 2


def post_ids(text: str) -> list[int]:
    """메시지 안의 읽기 게시물 링크(/p/{id}) — 앞에서부터 두 개까지."""
    out: list[int] = []
    for mm in POST_LINK.finditer(text or ""):
        pid = int(mm.group(1))
        if pid not in out:
            out.append(pid)
    return out[:MAX_CARDS]


async def _post_cards(session: AsyncSession, me: int, msgs: list[ChatMessage]) -> dict[int, dict]:
    """게시물 카드: 읽는 사람이 그 게시물을 볼 수 있을 때만(posts.can_see). 못 보면 링크 글자만 남는다.

    게시물 기능(FEATURE_POSTS)이 꺼져 있거나 모듈이 없으면 아무것도 붙이지 않는다.
    """
    from app.core.config import get_settings

    if not getattr(get_settings(), "feature_posts", False):
        return {}
    ids = {pid for m in msgs for pid in post_ids(m.body)}
    if not ids:
        return {}
    try:  # 기능끼리 얽히지 않게 필요할 때만 불러온다
        from app.models_posts import ReadingPost
        from app.services import posts
    except ImportError:  # pragma: no cover - 게시물 기능이 없는 배포
        return {}
    found = {p.id: p for p in await session.scalars(select(ReadingPost).where(ReadingPost.id.in_(ids)))}
    authors = await _users(session, [p.author_id for p in found.values()])
    photos = await posts.photos_of(session, found) if hasattr(posts, "photos_of") else {}
    cards: dict[int, dict] = {}
    for pid, p in found.items():
        if await posts.can_see(session, me, p):
            a = authors.get(p.author_id)
            cards[pid] = {
                "id": pid,
                "href": f"/p/{pid}",
                "title": p.book_title,
                "author": a.nickname if a else "",
                # 사진을 올린 게시물이면 첫 사진, 아니면 책 표지 (Instagram 공유 카드처럼)
                "cover": photos[pid][0].url if photos.get(pid) else posts.safe_cover(p.cover_url),
            }
    return cards


def render(
    m: ChatMessage,
    author: User | None,
    me: int,
    reader_page: int | None,
    reactions: list[ChatReaction] | None = None,
    quote: dict | None = None,
    cards: dict[int, dict] | None = None,
) -> dict:
    """메시지 한 줄의 응답. 잠기거나 지운 메시지는 'body' 키 자체를 넣지 않고 반응도 싣지 않는다."""
    out = {
        "id": m.id,
        "user_id": m.user_id,
        "nickname": author.nickname if author else "(알 수 없음)",
        "avatar": avatar_of(author) if author else None,
        "at": _aware(m.created_at).isoformat() if m.created_at else None,
        "mine": m.user_id == me,
        "kind": channel_of(m),
    }
    if m.page is not None:
        out["page"] = m.page + 1  # 화면의 쪽 번호(1부터)
    if m.deleted_at is not None:
        out["deleted"] = True
        return out
    if _sealed(m, me, reader_page):
        out["sealed"] = True
        return out
    out["body"] = m.body
    shown = [cards[pid] for pid in post_ids(m.body) if cards and pid in cards]
    if shown:
        out["cards"] = shown
    if m.veiled:
        out["veiled"] = True
    if quote is not None:
        out["reply"] = quote
    counts: dict[str, int] = {}
    mine = None
    for r in reactions or []:
        counts[r.emoji] = counts.get(r.emoji, 0) + 1
        if r.user_id == me:
            mine = r.emoji
    out["reactions"] = [
        {"emoji": e, "count": counts[e], "mine": e == mine} for e in social.EMOJIS if e in counts
    ]
    return out


async def render_many(
    session: AsyncSession, me: int, msgs: list[ChatMessage], reader_page: int | None
) -> list[dict]:
    """여러 메시지를 한꺼번에: 작성자 · 반응 · 답장 인용을 쿼리 몇 번으로 모은다."""
    if not msgs:
        return []
    ids = [m.id for m in msgs]
    quoted_ids = {m.reply_to_id for m in msgs if m.reply_to_id}
    quoted = (
        {q.id: q for q in await session.scalars(select(ChatMessage).where(ChatMessage.id.in_(quoted_ids)))}
        if quoted_ids
        else {}
    )
    authors = await _users(session, [m.user_id for m in msgs] + [q.user_id for q in quoted.values()])
    reacts: dict[int, list[ChatReaction]] = {}
    for r in await session.scalars(select(ChatReaction).where(ChatReaction.message_id.in_(ids))):
        reacts.setdefault(r.message_id, []).append(r)
    # 잠긴 메시지의 링크는 카드로도 풀지 않는다(본문을 안 주는 것과 같은 이유)
    cards = await _post_cards(
        session, me, [m for m in msgs if _open_for(m, me, reader_page) or m.user_id == me]
    )
    out = []
    for m in msgs:
        q = quoted.get(m.reply_to_id) if m.reply_to_id else None
        quote = _quote(q, authors.get(q.user_id) if q else None, me, reader_page) if q else None
        out.append(render(m, authors.get(m.user_id), me, reader_page, reacts.get(m.id), quote, cards))
    return out


async def _mark(session: AsyncSession, thread_id: int, me: int, channel: str, upto: int) -> int:
    """마지막으로 본 위치를 앞으로만 옮긴다. 옮기기 전 위치를 돌려준다."""
    r = await session.get(ChatRead, (thread_id, me, channel))
    if r is None:
        session.add(ChatRead(thread_id=thread_id, user_id=me, channel=channel, last_read_id=upto))
        return 0
    before = r.last_read_id or 0
    if upto > before:
        r.last_read_id = upto
    return before


def _channel_filter(q, conv: Conv, channel: str | None):
    if conv.kind != "room" or channel not in CHANNELS:
        return q
    return q.where(ChatMessage.page.is_not(None) if channel == "book" else ChatMessage.page.is_(None))


async def messages(
    session: AsyncSession,
    me: User,
    conv: Conv,
    after: int | None = None,
    before: int | None = None,
    locked: list[int] | None = None,
    deleted_since: dt.datetime | None = None,
    channel: str | None = None,
) -> dict:
    """대화(방은 채널 하나)의 메시지. after 가 없으면 최근 50개(before 가 있으면 그보다 이전 50개).

    폴링(after)은 새 메시지와 함께, 잠긴 채 들고 있는 메시지(locked) · deleted_since 뒤에 바뀐 메시지
    (반응 · 삭제)를 다시 보내고, 지워진 id를 따로 알려 준다. 볼 때마다 이 채널의 읽은 위치를 옮긴다.
    """
    now = clock()
    if conv.kind == "room":
        channel = channel if channel in CHANNELS else "book"
    else:
        channel = "chat"
    reader_page = await _reader_page(session, me.id, conv)
    empty = {
        "items": [],
        "updates": [],
        "deleted": [],
        "last_id": after or 0,
        "has_more": False,
        "now": now.isoformat(),
        "channel": channel,
        "my_page": None if reader_page is None else reader_page + 1,
        "read_upto": 0,
        "seen_upto": 0,
        "other_unread": 0,
        "notice": None,
    }
    if conv.thread is None:
        return empty
    tid = conv.thread.id
    hidden = await blocked_by_me(session, me.id) if conv.kind == "room" else set()
    q = select(ChatMessage).where(ChatMessage.thread_id == tid)
    if hidden:
        q = q.where(ChatMessage.user_id.not_in(hidden))
    q = _channel_filter(q, conv, channel)
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
    seen = {m.id for m in rows}
    updates: list[ChatMessage] = []
    if locked or deleted_since is not None:
        conds = []
        ids = [i for i in (locked or [])[:LOCKED_MAX] if after is None or i <= after]
        if ids:
            conds.append(ChatMessage.id.in_(ids))
        if deleted_since is not None:
            conds.append(ChatMessage.updated_at > deleted_since)
        if conds:
            updates = [
                m
                for m in await session.scalars(q.where(or_(*conds)).order_by(ChatMessage.id))
                if m.id not in seen and (after is None or m.id <= after)
            ]
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
    rendered = await render_many(session, me.id, rows + updates, reader_page)
    newest_q = _channel_filter(
        select(func.max(ChatMessage.id)).where(ChatMessage.thread_id == tid), conv, channel
    )
    newest = await session.scalar(newest_q) or 0
    read_upto = 0
    if before is None:  # 최신을 본 것 — 지난 메시지를 거슬러 볼 때는 읽은 위치를 옮기지 않는다
        read_upto = await _mark(session, tid, me.id, channel, newest)
        await session.commit()
    seen_upto = 0
    if conv.kind == "dm":  # '읽음' — 친구가 마지막으로 본 위치
        seen_upto = (
            await session.scalar(
                select(ChatRead.last_read_id).where(
                    ChatRead.thread_id == tid, ChatRead.user_id == conv.key, ChatRead.channel == "chat"
                )
            )
            or 0
        )
    other_unread = 0
    if conv.kind == "room":
        other = "chat" if channel == "book" else "book"
        other_unread = (await _unread(session, me.id, [tid], hidden, channel=other)).get(tid, 0)
    # 다음 폴링의 커서. 가려진(차단) 메시지 뒤로도 넘어가도록 채널의 최신 id까지.
    # 한 번에 다 못 줬으면 받은 데까지
    capped = after is not None and len(rows) == 200
    last_id = rows[-1].id if capped else max(after or 0, newest)
    return empty | {
        "items": rendered[: len(rows)],
        "updates": rendered[len(rows) :],
        "deleted": deleted,
        "last_id": last_id,
        "has_more": has_more,
        "read_upto": read_upto,
        "seen_upto": seen_upto,
        "other_unread": other_unread,
        "notice": conv.thread.notice if conv.kind == "room" else None,
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
        m.updated_at = m.deleted_at
        await session.commit()


async def react(session: AsyncSession, me: User, message_id: int, emoji: str | None) -> dict:
    """반응 달기 · 바꾸기 · 빼기(emoji=None). 한 사람이 한 메시지에 하나 — 기본 키가 막는다.

    잠겼거나 지운 메시지에는 달 수 없다(잠긴 메시지는 무엇인지조차 모르니까).
    """
    m, conv = await _message_conv(session, me, message_id)
    reader_page = await _reader_page(session, me.id, conv)
    if m.deleted_at is not None or _sealed(m, me.id, reader_page):
        raise ChatError("반응할 수 없는 메시지예요")
    if emoji is not None and emoji not in social.EMOJIS:
        raise ChatError("고를 수 있는 이모지가 아니에요")
    await ratelimit.check(session, f"chat-react:{me.id}", REACT_LIMIT, REACT_WINDOW, REACT_MSG)
    r = await session.get(ChatReaction, (m.id, me.id))
    if emoji is None:
        if r is not None:
            await session.delete(r)
    elif r is None:
        session.add(ChatReaction(message_id=m.id, user_id=me.id, emoji=emoji))
    else:
        r.emoji = emoji
    m.updated_at = clock()
    await session.commit()
    return (await render_many(session, me.id, [m], reader_page))[0]


async def set_notice(session: AsyncSession, me: User, room_id: int, text: str | None) -> str | None:
    """방 공지 한 줄 — 방장만. 비우면 내린다."""
    conv = await room_conv(session, me, room_id)
    if conv.room.owner_id != me.id:
        raise ChatError("방장만 공지를 정할 수 있어요", 403)
    body = (text or "").strip()
    if len(body) > MAX_NOTICE:
        raise ChatError(f"공지는 {MAX_NOTICE}자까지 적을 수 있어요")
    await ratelimit.check(session, f"chat:{me.id}", SEND_LIMIT, SEND_WINDOW, SEND_MSG)
    thread = await _ensure_thread(session, me, conv)
    thread.notice = body or None
    thread.notice_at = clock() if body else None
    await session.commit()
    return thread.notice


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


async def _unread(
    session: AsyncSession, me: int, thread_ids, hidden: set[int], channel: str | None = None
) -> dict[int, int]:
    """대화별 안 읽은 수 — 메시지마다 그 채널의 읽은 위치와 비교한다."""
    q = (
        select(ChatMessage.thread_id, func.count())
        .outerjoin(
            ChatRead,
            and_(
                ChatRead.thread_id == ChatMessage.thread_id,
                ChatRead.user_id == me,
                ChatRead.channel == CHANNEL_SQL,
            ),
        )
        .where(
            ChatMessage.thread_id.in_(thread_ids),
            ChatMessage.user_id != me,
            ChatMessage.deleted_at.is_(None),
            ChatMessage.id > func.coalesce(ChatRead.last_read_id, 0),
        )
        .group_by(ChatMessage.thread_id)
    )
    if channel == "book":
        q = q.where(ChatMessage.page.is_not(None))
    elif channel == "chat":
        q = q.where(ChatMessage.page.is_(None))
    if hidden:
        q = q.where(ChatMessage.user_id.not_in(hidden))
    return dict((await session.execute(q)).all())


async def unread_total(session: AsyncSession, me: int) -> int:
    """메뉴의 점 — 화면을 그릴 때 한 번 센다(다른 화면에서 따로 폴링하지 않는다)."""
    counts = await _unread(session, me, _my_threads(me), await blocked_by_me(session, me))
    return sum(counts.values())


async def channel_unread(session: AsyncSession, me: User, conv: Conv) -> dict[str, int]:
    """방의 채널 탭 숫자."""
    if conv.thread is None:
        return {"book": 0, "chat": 0}
    hidden = await blocked_by_me(session, me.id)
    return {
        ch: (await _unread(session, me.id, [conv.thread.id], hidden, channel=ch)).get(conv.thread.id, 0)
        for ch in CHANNELS
    }


def preview(m: ChatMessage | None, me: int, reader_page: int | None) -> str | None:
    """목록 미리보기 — 잠긴(도장보다 덜 읽음) 메시지와 가린 메시지의 본문은 절대 내보내지 않는다.
    ||스포일러|| 부분도 가린다."""
    if m is None:
        return None
    if m.deleted_at is not None:
        return DELETED_TEXT
    if not _open_for(m, me, reader_page):
        return LOCKED_TEXT
    return snippet(m.body, PREVIEW)


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
                "book": c.book,
                "at": _aware(m.created_at) if m else None,
                "unread": unread.get(c.thread.id, 0) if c.thread else 0,
                "preview": preview(m, me.id, pages.get(c.key) if c.kind == "room" else None),
                "mine": m is not None and m.user_id == me.id,
            }
        )
    epoch = dt.datetime.min.replace(tzinfo=dt.UTC)
    out.sort(key=lambda d: (d["at"] or epoch, d["title"]), reverse=True)
    return out


# ---------------------------------------------------------------- 친구가 지금 읽는 책


def public_book(book: Book | None) -> bool:
    """제목을 친구에게 보여 줘도 되는 책 — 퍼블릭 도메인 카탈로그 책만(올린 개인 파일은 절대 아님)."""
    return book is not None and book.has_fulltext and book.owner_id is None and book.source != "upload"


@dataclass
class FriendCard:
    user: User
    active: bool = False  # 최근 10분 안에 책을 펼쳤다
    book: Book | None = None  # 지금 읽는 책 — public_book 일 때만
    page: int = 0
    total: int = 0
    week: list[dict] | None = None  # 이번 주 읽은 날(목표 기능이 켜져 있을 때)


async def friend_cards(session: AsyncSession, me: int, week: bool = False) -> list[FriendCard]:
    """수락한 친구만: 지금 읽는 중인지(링), 읽는 책(공개 책만), 이번 주 읽은 날.

    친구가 아닌 사람 · 차단한 사람은 아예 들어오지 않는다.
    """
    from app.models import LibraryEntry, Shelf

    ids = await friend_ids(session, me)
    if not ids:
        return []
    users = await _users(session, ids)
    rows = (
        await session.execute(
            select(LibraryEntry, Book)
            .join(Book, Book.id == LibraryEntry.book_id)
            .where(LibraryEntry.user_id.in_(ids), LibraryEntry.shelf != Shelf.WANT)
            .order_by(LibraryEntry.updated_at.desc())
        )
    ).all()
    cutoff = clock() - ACTIVE_FOR
    cards: dict[int, FriendCard] = {uid: FriendCard(u) for uid, u in users.items()}
    latest: set[int] = set()
    for e, b in rows:
        card = cards[e.user_id]
        if e.updated_at is not None and _aware(e.updated_at) >= cutoff:
            card.active = True
        if e.user_id in latest:
            continue
        latest.add(e.user_id)  # 가장 최근에 펼친 책 하나 — 개인 파일이면 제목 없이 둔다
        if public_book(b):
            card.book, card.page, card.total = b, e.current_chunk + 1, b.total_chunks
    if week:
        await _fill_week(session, list(cards.values()))
    out = list(cards.values())
    out.sort(key=lambda c: (not c.active, c.book is None, c.user.nickname))
    return out


async def _fill_week(session: AsyncSession, cards: list[FriendCard]) -> None:
    """이번 주(월~일) 읽은 날 — 목표 기능(FEATURE_GOALS)이 꺼져 있으면 비워 둔다."""
    from app.core.config import get_settings

    if not cards or not get_settings().feature_goals:
        return
    from app.services import goals  # 기능끼리 얽히지 않게 필요할 때만 불러온다

    today = goals.reading_day(goals.clock())
    monday = today - dt.timedelta(days=today.weekday())
    for c in cards:
        pages = (await goals.history(session, c.user.id, monday)).pages
        c.week = [
            {
                "day": "월화수목금토일"[i],
                "read": pages.get(monday + dt.timedelta(days=i), 0) > 0,
                "future": monday + dt.timedelta(days=i) > today,
            }
            for i in range(7)
        ]


async def members(session: AsyncSession, room: Room, me: int) -> list[dict]:
    """방 멤버 패널 — 검증된 쪽과 진행 막대(대결방이 이미 함께 보는 값). 메시지 내용은 없다."""
    from app.services import races as race_service

    board = await race_service.standings(session, room, me)
    return [
        {"user": s.user, "pages": s.pages, "pct": s.pct, "me": s.is_me, "owner": s.user.id == room.owner_id}
        for s in board
    ]
