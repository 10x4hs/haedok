"""마일리지 — 잔액 · 쓰기(기록 보호권, 제휴 리워드) · 환불.

잔액 = 모은 것(points_log, 적립 규칙은 app/services/points.py 그대로) − 쓴 것(mileage_spends).
순위표는 계속 '모은 것'으로 줄 세운다.

동시에 눌러도 더 쓰지 않게 하는 장치(한 트랜잭션 안에서):
1. 사용자 행을 잠근다(Postgres SELECT … FOR UPDATE). SQLite 는 이 절을 무시한다.
2. 차감 행에 사용자별 순번 seq 를 붙인다. (user_id, seq) unique 라서 같은 잔액을 보고 들어온
   두 요청 중 하나는 반드시 IntegrityError 로 막힌다 — 잠금이 없는 SQLite 에서도 맞다.
3. 폼마다 주는 idem_key 가 (user_id, idem_key) unique — 같은 폼을 두 번 보내도 한 번만 쓴다.
4. 기다리는 교환 요청은 사람당 하나(부분 unique 인덱스), 재고는 조건부 UPDATE 로 줄인다.
막히면 전체를 되돌리고, 같은 키로 이미 끝난 일이면 그 결과를 돌려준다.
"""

import datetime as dt
import importlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import LibraryEntry, PointsLog, User
from app.models_mileage import (
    CANCELLED,
    REJECTED,
    REQUESTED,
    SENT,
    MileageSpend,
    RewardItem,
    RewardRedemption,
)
from app.services import points

KST = points.KST

FREEZE = "freeze"
REWARD = "reward"
REFUND = "refund"
KIND_LABELS = {FREEZE: "기록 보호권", REWARD: "리워드 교환", REFUND: "돌려받음"}

FREEZE_PRICE = 50
FREEZE_MAX = 2
MIN_ACCOUNT_DAYS = 7
MONTHLY_LIMIT = 2
HISTORY_SIZE = 30

# 처음 보여 줄 예시 상품 — 모두 지어낸 이름이고 is_sample 이다(진짜 제휴처가 아니다).
# 같은 목록이 migrations/versions/d44a3f8b0c04_mileage.py 에도 들어 있다.
SAMPLE_ITEMS = [
    {
        "partner_name": "동네 카페(예시)",
        "title": "따뜻한 음료 한 잔",
        "cost": 300,
        "sort": 10,
        "image": "warm-drink",
    },
    {
        "partner_name": "골목 책방(예시)",
        "title": "책 5,000원 할인권",
        "cost": 500,
        "sort": 20,
        "image": "bookshop",
    },
    {
        "partner_name": "모퉁이 문구점(예시)",
        "title": "연필과 책갈피 세트",
        "cost": 200,
        "sort": 30,
        "image": "pencils",
    },
]

# --- 상품 그림 ---
# 함께 담긴 그림(app/static/rewards/, 출처는 CREDITS.md): 키 → (파일, 관리자 화면 이름)
BUNDLED_IMAGES = {
    "warm-drink": ("warm-drink.webp", "따뜻한 음료"),
    "bookshop": ("bookshop.webp", "책방 서가"),
    "pencils": ("pencils.webp", "색연필"),
    "gift": ("gift.svg", "선물 상자(그림)"),
}
# 바깥 그림은 https 이고 이 호스트일 때만 — 아무 주소나 받으면 보는 사람의 접속이 남의 서버에 기록된다
ALLOWED_IMAGE_HOSTS = frozenset({"upload.wikimedia.org"})
IMAGE_MAX = 300


def clean_image(value: str | None) -> str | None:
    """관리자가 고른 그림 값을 검사한다. 비면 None, 허용하지 않는 값은 ValueError."""
    value = (value or "").strip()
    if not value:
        return None
    if value in BUNDLED_IMAGES:
        return value
    if len(value) > IMAGE_MAX or any(c.isspace() or c in "\"'<>" for c in value):
        raise ValueError(value)
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_IMAGE_HOSTS
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        raise ValueError(value)
    return value


def image_src(value: str | None) -> str:
    """화면에 쓸 주소. 저장된 값이 (나중에 규칙이 바뀌어) 허용 밖이면 기본 그림으로."""
    try:
        value = clean_image(value)
    except ValueError:
        value = None
    if value is None:
        value = "gift"
    if value in BUNDLED_IMAGES:
        return f"/static/rewards/{BUNDLED_IMAGES[value][0]}"
    return value


ERRORS = {
    "balance": "마일리지가 모자라요",
    "stock": "다 나갔어요. 다음에 다시 들어오면 바꿀 수 있어요",
    "pending": "기다리는 요청이 하나 있어요. 그 요청이 끝나면 다시 바꿀 수 있어요",
    "monthly": f"리워드는 한 달에 {MONTHLY_LIMIT}번까지 바꿀 수 있어요",
    "too_new": f"가입하고 {MIN_ACCOUNT_DAYS}일이 지나면 리워드를 바꿀 수 있어요",
    "sample": "아직 제휴 준비 중인 예시예요. 제휴처가 들어오면 교환이 열려요. 마일리지는 그대로예요",
    "inactive": "지금은 바꿀 수 없는 리워드예요",
    "freeze_full": f"기록 보호권은 {FREEZE_MAX}개까지 가질 수 있어요",
    "unavailable": "지금은 바꿀 수 없어요",
    "not_found": "요청을 찾을 수 없어요",
    "busy": "다른 요청을 처리하고 있어요. 잠시 뒤 다시 해 주세요",
}


class MileageError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(ERRORS.get(code, code))
        self.code = code


def clock() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def enabled() -> bool:
    return get_settings().feature_mileage


def _utc(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def month_start(now: dt.datetime) -> dt.datetime:
    """이번 달 1일 00:00 KST 를 UTC 로."""
    local = now.astimezone(KST).date().replace(day=1)
    return dt.datetime.combine(local, dt.time.min, KST).astimezone(dt.UTC)


_KEY = re.compile(r"[A-Za-z0-9_-]{8,64}")


def clean_key(key: str | None) -> str | None:
    """폼이 보낸 멱등 키 — 모양이 이상하면 없는 것으로 본다(그때는 순번 제약만 지킨다)."""
    return key if key and _KEY.fullmatch(key) else None


# --- 잔액 ---


@dataclass
class Balance:
    earned: int
    spent: int

    @property
    def balance(self) -> int:
        return self.earned - self.spent


async def balance(session: AsyncSession, user_id: int) -> Balance:
    earned = await session.scalar(select(func.sum(PointsLog.amount)).where(PointsLog.user_id == user_id))
    spent = await session.scalar(select(func.sum(MileageSpend.amount)).where(MileageSpend.user_id == user_id))
    return Balance(earned=int(earned or 0), spent=int(spent or 0))


async def card(session: AsyncSession, user_id: int) -> Balance | None:
    """/me 카드용 — 기능이 꺼져 있으면 None(카드는 예전처럼 모은 마일리지를 보여 준다)."""
    return await balance(session, user_id) if enabled() else None


async def earned_since(session: AsyncSession, user_id: int, since: dt.datetime) -> int:
    total = await session.scalar(
        select(func.sum(PointsLog.amount)).where(PointsLog.user_id == user_id, PointsLog.created_at >= since)
    )
    return int(total or 0)


def earn_rules() -> list[dict]:
    """적립 규칙을 points.RULES 에서 그대로 읽는다 — 화면이 규칙과 어긋날 수 없게."""
    notes = {
        points.CHUNK: "서버가 머문 시간을 확인한 쪽만, 같은 쪽은 한 번",
        points.REACTION: f"문단에 처음 남긴 반응, 하루 {points.REACTION_DAILY_CAP}번까지",
        points.RACE_FINISH: "대결 책을 끝까지",
        points.OVERTAKE: "대결에서 친구를 앞질렀을 때, 사람마다 한 번",
        points.QUOTE: "대결방에 문장 공유",
        points.STREAK: f"{points.STREAK_DAYS}일 연속으로 읽을 때마다",
    }
    return [
        {"source": s, "label": points.LABELS.get(s, s), "amount": amount, "note": notes.get(s, "")}
        for s, amount in points.RULES.items()
    ]


async def history(session: AsyncSession, user_id: int, n: int = HISTORY_SIZE) -> list[dict]:
    """모은 것과 쓴 것을 시간순(최근 먼저)으로 섞는다."""
    earned = await session.scalars(
        select(PointsLog).where(PointsLog.user_id == user_id).order_by(PointsLog.id.desc()).limit(n)
    )
    spent = list(
        await session.scalars(
            select(MileageSpend)
            .where(MileageSpend.user_id == user_id)
            .order_by(MileageSpend.id.desc())
            .limit(n)
        )
    )
    ids = [int(s.ref.split(":")[1]) for s in spent if s.ref and s.ref.startswith("redemption:")]
    titles: dict[int, str] = {}
    if ids:
        rows = await session.execute(
            select(RewardRedemption.id, RewardItem.title)
            .join(RewardItem, RewardItem.id == RewardRedemption.item_id)
            .where(RewardRedemption.id.in_(ids))
        )
        titles = dict(rows.all())
    out = [
        {"label": points.LABELS.get(r.source, r.source), "amount": r.amount, "at": _utc(r.created_at)}
        for r in earned
        if r.created_at
    ]
    for s in spent:
        label = KIND_LABELS.get(s.kind, s.kind)
        if s.ref and s.ref.startswith("redemption:"):
            title = titles.get(int(s.ref.split(":")[1]))
            label = f"{label} · {title}" if title else label
        out.append({"label": label, "amount": -s.amount, "at": _utc(s.created_at)})
    out.sort(key=lambda e: e["at"], reverse=True)
    return out[:n]


# --- 상품 ---


async def catalogue(session: AsyncSession, include_inactive: bool = False) -> list[RewardItem]:
    stmt = select(RewardItem).order_by(RewardItem.sort, RewardItem.id)
    if not include_inactive:
        stmt = stmt.where(RewardItem.active.is_(True))
    return list(await session.scalars(stmt))


async def seed_samples(session: AsyncSession) -> int:
    """상품이 하나도 없을 때만 예시 상품을 넣는다(모두 is_sample)."""
    if await session.scalar(select(func.count(RewardItem.id))):
        return 0
    for item in SAMPLE_ITEMS:
        session.add(RewardItem(**item, is_sample=True, active=True))
    await session.commit()
    return len(SAMPLE_ITEMS)


async def my_requests(session: AsyncSession, user_id: int, n: int = 10) -> list[tuple[RewardRedemption, str]]:
    rows = await session.execute(
        select(RewardRedemption, RewardItem.title)
        .join(RewardItem, RewardItem.id == RewardRedemption.item_id)
        .where(RewardRedemption.user_id == user_id)
        .order_by(RewardRedemption.id.desc())
        .limit(n)
    )
    return [(r, t) for r, t in rows.all()]


# --- 쓰기 공통 ---


async def _lock_user(session: AsyncSession, user_id: int) -> None:
    """같은 사람의 쓰기를 한 줄로 세운다(Postgres). SQLite 는 FOR UPDATE 를 그리지 않는다."""
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())


async def _spend_by_key(session: AsyncSession, user_id: int, key: str | None) -> MileageSpend | None:
    if key is None:
        return None
    return await session.scalar(
        select(MileageSpend).where(MileageSpend.user_id == user_id, MileageSpend.idem_key == key)
    )


async def _debit(
    session: AsyncSession, user_id: int, amount: int, kind: str, ref: str | None, key: str | None
) -> MileageSpend:
    """잔액을 확인하고 차감 행을 넣는다. 동시에 들어온 다른 차감과 겹치면 flush 가 IntegrityError."""
    # 순번을 잔액보다 먼저 읽는다: 그 뒤에 들어온 차감은 잔액에 안 보였더라도 같은 순번을 노려 부딪힌다
    last = await session.scalar(select(func.max(MileageSpend.seq)).where(MileageSpend.user_id == user_id))
    if (await balance(session, user_id)).balance < amount:
        raise MileageError("balance")
    row = MileageSpend(user_id=user_id, amount=amount, kind=kind, ref=ref, idem_key=key, seq=(last or 0) + 1)
    row.created_at = clock()
    session.add(row)
    await session.flush()
    return row


def _refund_row(user_id: int, amount: int, ref: str, key: str) -> MileageSpend:
    """돌려주기 — 음수 행 하나. 키가 unique 라 같은 일로 두 번 돌려주지 않는다."""
    row = MileageSpend(user_id=user_id, amount=-amount, kind=REFUND, ref=ref, idem_key=key)
    row.created_at = clock()
    return row


# --- 기록 보호권 ---


def freeze_service():
    """app.services.goals(다른 작업에서 만든다)를 늦게 불러온다. 없거나 꺼져 있으면 None."""
    if not getattr(get_settings(), "feature_goals", True):
        return None
    try:
        goals = importlib.import_module("app.services.goals")
    except ImportError:
        return None
    return goals if callable(getattr(goals, "grant_freezes", None)) else None


async def freeze_count(session: AsyncSession, user_id: int) -> int | None:
    """지금 가진 보호권 수 — goals 에 freeze_count 가 있으면 그걸, 없으면 0개 더하기로 읽는다."""
    goals = freeze_service()
    if goals is None:
        return None
    if callable(getattr(goals, "freeze_count", None)):
        return int(await goals.freeze_count(session, user_id))
    return int(await goals.grant_freezes(session, user_id, 0))


async def buy_freeze(session: AsyncSession, user: User, key: str | None) -> int:
    """기록 보호권 1개를 FREEZE_PRICE 에 산다. 새 보유 수를 돌려준다."""
    goals = freeze_service()
    if goals is None:
        raise MileageError("unavailable")
    key, uid = clean_key(key), user.id  # 되돌리면 user 가 만료되므로 id 를 먼저 잡아 둔다
    try:
        await _lock_user(session, uid)
        if await _spend_by_key(session, uid, key):
            await session.commit()  # 이미 끝난 요청을 다시 보냄 — 쓴 것 없이 잠금만 푼다
            return await freeze_count(session, uid) or 0
        held = await freeze_count(session, uid) or 0
        if held >= FREEZE_MAX:
            raise MileageError("freeze_full")
        spend = await _debit(session, uid, FREEZE_PRICE, FREEZE, FREEZE, key)
        new = int(await goals.grant_freezes(session, uid, 1))
        if new <= held:
            # 그사이 다른 곳에서 상한까지 채웠다 — 받은 게 없으니 돌려준다
            session.add(_refund_row(uid, FREEZE_PRICE, FREEZE, f"refund-{spend.id}"))
            await session.commit()
            raise MileageError("freeze_full")
        await session.commit()
        return new
    except MileageError:
        await session.rollback()
        raise
    except (IntegrityError, OperationalError):
        await session.rollback()
        if await _spend_by_key(session, uid, key):
            return await freeze_count(session, uid) or 0
        raise MileageError("busy") from None


# --- 제휴 리워드 ---


async def redeem(session: AsyncSession, user: User, item_id: int, key: str | None) -> RewardRedemption:
    """리워드 교환 요청. 예시 상품은 아무것도 쓰지 않고 MileageError('sample')."""
    key, uid, joined = clean_key(key), user.id, user.created_at  # 되돌리면 user 가 만료된다
    try:
        return await _redeem(session, uid, joined, item_id, key)
    except MileageError:
        await session.rollback()
        raise
    except (IntegrityError, OperationalError):
        await session.rollback()
        if done := await _redemption_by_key(session, uid, key):
            return done
        # 같은 순간 다른 요청이 먼저 들어갔다 — 그 요청이 기다리는 중이면 그대로 알려 준다
        raise MileageError("pending" if await _pending(session, uid) else "busy") from None


async def _redemption_by_key(session: AsyncSession, user_id: int, key: str | None) -> RewardRedemption | None:
    spend = await _spend_by_key(session, user_id, key)
    if spend is None or not (spend.ref or "").startswith("redemption:"):
        return None
    return await session.get(RewardRedemption, int(spend.ref.split(":")[1]))


async def _pending(session: AsyncSession, user_id: int) -> RewardRedemption | None:
    return await session.scalar(
        select(RewardRedemption).where(
            RewardRedemption.user_id == user_id, RewardRedemption.status == REQUESTED
        )
    )


async def _redeem(
    session: AsyncSession, uid: int, joined: dt.datetime | None, item_id: int, key: str | None
) -> RewardRedemption:
    item = await session.get(RewardItem, item_id)
    if item is None or not item.active:
        raise MileageError("inactive")
    if item.is_sample:
        raise MileageError("sample")
    await _lock_user(session, uid)
    if done := await _redemption_by_key(session, uid, key):
        await session.commit()  # 쓴 것 없음 — 잠금만 푼다(rollback 은 돌려줄 객체를 만료시킨다)
        return done
    now = clock()
    if joined and now - _utc(joined) < dt.timedelta(days=MIN_ACCOUNT_DAYS):
        raise MileageError("too_new")
    if await _pending(session, uid):
        raise MileageError("pending")
    this_month = await session.scalar(
        select(func.count(RewardRedemption.id)).where(
            RewardRedemption.user_id == uid,
            RewardRedemption.created_at >= month_start(now),
            RewardRedemption.status.in_((REQUESTED, SENT)),
        )
    )
    if (this_month or 0) >= MONTHLY_LIMIT:
        raise MileageError("monthly")
    if (await balance(session, uid)).balance < item.cost:
        raise MileageError("balance")
    if item.stock is not None:
        res = await session.execute(
            update(RewardItem)
            .where(RewardItem.id == item.id, RewardItem.stock > 0)
            .values(stock=RewardItem.stock - 1)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            raise MileageError("stock")
        session.expire(item, ["stock"])  # 메모리의 값은 이제 낡았다
    req = RewardRedemption(user_id=uid, item_id=item.id, cost=item.cost, status=REQUESTED)
    req.created_at = now
    session.add(req)
    await session.flush()
    await _debit(session, uid, item.cost, REWARD, f"redemption:{req.id}", key)
    await session.commit()
    return req


async def _close(
    session: AsyncSession,
    redemption_id: int,
    status: str,
    note: str | None,
    refund: bool,
    user_id: int | None = None,
) -> RewardRedemption:
    """기다리는 요청을 닫는다(조건부 UPDATE — 두 사람이 동시에 눌러도 한 번만). 필요하면 돌려준다."""
    req = await session.get(RewardRedemption, redemption_id)
    if req is None or (user_id is not None and req.user_id != user_id):
        raise MileageError("not_found")
    res = await session.execute(
        update(RewardRedemption)
        .where(RewardRedemption.id == redemption_id, RewardRedemption.status == REQUESTED)
        .values(status=status, handled_at=clock(), admin_note=(note or None))
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        await session.rollback()
        raise MileageError("not_found")
    if refund:
        session.add(_refund_row(req.user_id, req.cost, f"redemption:{req.id}", f"refund-redemption-{req.id}"))
        # 수량이 정해진 상품이면 하나 되돌려 놓는다(메모리에 있는 상품 객체도 같이 맞춘다)
        await session.execute(
            update(RewardItem)
            .where(RewardItem.id == req.item_id, RewardItem.stock.is_not(None))
            .values(stock=RewardItem.stock + 1)
            .execution_options(synchronize_session="fetch")
        )
    await session.commit()
    await session.refresh(req)
    return req


async def cancel(session: AsyncSession, user: User, redemption_id: int) -> RewardRedemption:
    """본인이 기다리는 요청을 거둔다 — 마일리지는 돌려준다."""
    return await _close(session, redemption_id, CANCELLED, None, refund=True, user_id=user.id)


async def reject(session: AsyncSession, redemption_id: int, note: str | None) -> RewardRedemption:
    return await _close(session, redemption_id, REJECTED, (note or "").strip()[:280], refund=True)


async def mark_sent(session: AsyncSession, redemption_id: int, note: str | None) -> RewardRedemption:
    return await _close(session, redemption_id, SENT, (note or "").strip()[:280], refund=False)


# --- 관리자 ---


@dataclass
class Signals:
    """보내기 전에 운영자가 볼 것."""

    account_days: int
    verified_pages: int
    earned_7d: int
    balance: int
    sent_total: int


async def signals(session: AsyncSession, user: User) -> Signals:
    now = clock()
    pages = await session.scalar(
        select(func.sum(LibraryEntry.verified_chunk + 1)).where(
            LibraryEntry.user_id == user.id, LibraryEntry.verified_chunk >= 0
        )
    )
    sent = await session.scalar(
        select(func.count(RewardRedemption.id)).where(
            RewardRedemption.user_id == user.id, RewardRedemption.status == SENT
        )
    )
    age = (now - _utc(user.created_at)).days if user.created_at else 0
    return Signals(
        account_days=age,
        verified_pages=int(pages or 0),
        earned_7d=await earned_since(session, user.id, now - dt.timedelta(days=7)),
        balance=(await balance(session, user.id)).balance,
        sent_total=int(sent or 0),
    )


async def queue(session: AsyncSession, n: int = 50) -> list[dict]:
    """기다리는 요청 먼저, 그다음 최근에 처리한 것."""
    pending_first = (RewardRedemption.status != REQUESTED).label("handled")
    rows = await session.execute(
        select(RewardRedemption, RewardItem, User)
        .join(RewardItem, RewardItem.id == RewardRedemption.item_id)
        .join(User, User.id == RewardRedemption.user_id)
        .order_by(pending_first, RewardRedemption.id.desc())
        .limit(n)
    )
    out = []
    for req, item, u in rows.all():
        out.append(
            {
                "req": req,
                "item": item,
                "user": u,
                "signals": await signals(session, u) if req.status == REQUESTED else None,
            }
        )
    return out


STATUS_LABELS = {
    REQUESTED: "확인 중",
    SENT: "보냄",
    REJECTED: "거절됨 · 돌려받음",
    CANCELLED: "취소함 · 돌려받음",
}
