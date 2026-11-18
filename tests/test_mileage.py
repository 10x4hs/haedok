"""마일리지 — 잔액(모은 것 − 쓴 것), 쓰기 규칙, 동시 요청, 환불, 화면, 관리자, 기능 플래그."""

import asyncio
import datetime as dt
import re
import sys
import types

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import Base
from app.models import PointsLog, User
from app.models_mileage import REJECTED, REQUESTED, SENT, MileageSpend, RewardItem, RewardRedemption
from app.services import mileage, points
from app.services.mileage import MileageError
from tests.conftest import signup
from tests.test_api import upstream

NOW = dt.datetime(2026, 11, 18, 3, 0, tzinfo=dt.UTC)  # 수요일 12:00 KST


@pytest.fixture
def mclock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(mileage, "clock", lambda: state["now"])
    monkeypatch.setattr(points, "clock", lambda: state["now"])

    class C:
        @staticmethod
        def set(t: dt.datetime) -> None:
            state["now"] = t

    return C()


@pytest.fixture
def goals(monkeypatch):
    """app.services.goals 를 흉내 낸다 — 다른 작업이 만드는 진짜 모듈 대신."""
    state = {"count": {}, "calls": []}

    async def grant_freezes(session, user_id: int, n: int) -> int:
        state["calls"].append((user_id, n))
        state["count"][user_id] = min(2, state["count"].get(user_id, 0) + n)
        return state["count"][user_id]

    fake = types.ModuleType("app.services.goals")
    fake.grant_freezes = grant_freezes
    monkeypatch.setitem(sys.modules, "app.services.goals", fake)
    return state


@pytest.fixture
def no_goals(monkeypatch):
    monkeypatch.setitem(sys.modules, "app.services.goals", None)  # import 하면 ImportError


async def _user(session, email="m@example.com", days_old: int = 30) -> User:
    u = User(nickname=email.split("@")[0], email=email, password_hash=None)
    u.created_at = NOW - dt.timedelta(days=days_old)
    session.add(u)
    await session.commit()
    return u


_ref = iter(range(10**6))


async def _earn(session, user_id: int, amount: int) -> None:
    row = PointsLog(user_id=user_id, source=points.CHUNK, amount=amount, ref_id=f"t:{next(_ref)}")
    row.created_at = NOW - dt.timedelta(days=1)
    session.add(row)
    await session.commit()


async def _item(session, cost=100, stock=None, sample=False, title="진짜 상품") -> RewardItem:
    it = RewardItem(
        partner_name="테스트 제휴처", title=title, cost=cost, stock=stock, is_sample=sample, active=True
    )
    session.add(it)
    await session.commit()
    return it


async def _redeem(s, uid: int, item_id: int, key: str) -> RewardRedemption:
    """쓰기가 막히면 세션을 되돌려 객체가 만료되므로, 매번 id 로 다시 꺼낸다."""
    return await mileage.redeem(s, await s.get(User, uid), item_id, key)


async def _freeze(s, uid: int, key: str) -> int:
    return await mileage.buy_freeze(s, await s.get(User, uid), key)


async def _count(session, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where)) or 0


# --- 잔액 ---


async def test_balance_is_earned_minus_spent_and_leaderboard_keeps_earned(session_factory, mclock, goals):
    async with session_factory() as s:
        u = await _user(s)
        uid = u.id
        await _earn(s, uid, 120)
        assert (await mileage.balance(s, uid)).balance == 120
        await _freeze(s, uid, "key-aaaaaaaa")
        b = await mileage.balance(s, uid)
        assert (b.earned, b.spent, b.balance) == (120, 50, 70)
        board = await points.leaderboard(s, uid, "all")
        assert board.me.points == 120  # 순위는 모은 것으로


async def test_cannot_spend_more_than_balance(session_factory, mclock, goals):
    async with session_factory() as s:
        u = await _user(s)
        uid = u.id
        await _earn(s, uid, 40)
        it = await _item(s, cost=100)
        iid = it.id
        with pytest.raises(MileageError) as e:
            await _freeze(s, uid, "key-aaaaaaaa")
        assert e.value.code == "balance"
        with pytest.raises(MileageError) as e:
            await _redeem(s, uid, iid, "key-bbbbbbbb")
        assert e.value.code == "balance"
        assert await _count(s, MileageSpend) == 0 and await _count(s, RewardRedemption) == 0
        assert [c for c in goals["calls"] if c[1] > 0] == []


# --- 같은 폼 두 번 · 동시 요청 ---


async def test_double_submit_same_key_is_one_redemption(session_factory, mclock):
    async with session_factory() as s:
        u = await _user(s)
        uid = u.id
        await _earn(s, uid, 500)
        it = await _item(s, cost=100, stock=5)
        iid = it.id
        a = await _redeem(s, uid, iid, "same-key-123")
        b = await _redeem(s, uid, iid, "same-key-123")
        assert a.id == b.id
        assert await _count(s, MileageSpend) == 1 and await _count(s, RewardRedemption) == 1
        await s.refresh(it)
        assert it.stock == 4
        assert (await mileage.balance(s, uid)).balance == 400


@pytest.fixture
async def file_db(tmp_path):
    """동시 요청은 연결이 따로인 파일 SQLite 로 — 실제 서버처럼 각 요청이 자기 연결을 쓴다."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}", connect_args={"timeout": 10})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _parallel(factory, fn, *args_list):
    async def one(args):
        async with factory() as s:
            user = await s.get(User, args[0])
            try:
                return await fn(s, user, *args[1:])
            except MileageError as e:
                return e

    return await asyncio.gather(*(one(a) for a in args_list))


@pytest.mark.parametrize("keys", [("k-parallel-1", "k-parallel-2"), ("k-parallel-1", "k-parallel-1")])
async def test_parallel_redeem_yields_one_redemption_and_one_spend(file_db, mclock, keys):
    async with file_db() as s:
        u = await _user(s)
        uid = u.id
        await _earn(s, uid, 1000)
        it = await _item(s, cost=100, stock=10)
        item_id = it.id
    results = await _parallel(file_db, mileage.redeem, *[(uid, item_id, k) for k in keys])
    async with file_db() as s:
        assert await _count(s, RewardRedemption) == 1
        assert await _count(s, MileageSpend) == 1
        assert (await mileage.balance(s, uid)).balance == 900
        assert (await s.get(RewardItem, item_id)).stock == 9
    ok = [r for r in results if isinstance(r, RewardRedemption)]
    assert ok and len({r.id for r in ok}) == 1
    if keys[0] != keys[1]:
        assert len(ok) == 1 and isinstance(results[results.index(ok[0]) ^ 1], MileageError)


async def test_parallel_freeze_buys_cannot_overspend(file_db, mclock, goals):
    async with file_db() as s:
        u = await _user(s)
        uid = u.id
        await _earn(s, uid, 60)  # 한 개 값만 있다
    results = await _parallel(file_db, mileage.buy_freeze, (uid, "k-freeze-1"), (uid, "k-freeze-2"))
    async with file_db() as s:
        assert (await mileage.balance(s, uid)).balance == 10
        assert await _count(s, MileageSpend) == 1
    assert sum(isinstance(r, int) for r in results) == 1
    assert [c for c in goals["calls"] if c[1] > 0] == [(uid, 1)]


# --- 예시 상품 ---


async def test_sample_item_never_deducts(session_factory, mclock):
    async with session_factory() as s:
        u = await _user(s)
        uid = u.id
        await _earn(s, uid, 1000)
        assert await mileage.seed_samples(s) == len(mileage.SAMPLE_ITEMS)
        assert await mileage.seed_samples(s) == 0  # 이미 있으면 넣지 않는다
        items = await mileage.catalogue(s)
        assert items and all(i.is_sample for i in items)
        for iid in [i.id for i in items]:
            with pytest.raises(MileageError) as e:
                await _redeem(s, uid, iid, f"sample-key-{iid}")
            assert e.value.code == "sample"
        assert await _count(s, MileageSpend) == 0 and await _count(s, RewardRedemption) == 0
        assert (await mileage.balance(s, uid)).balance == 1000


# --- 한도 ---


async def test_one_pending_request_at_a_time(session_factory, mclock):
    async with session_factory() as s:
        u = await _user(s)
        uid = u.id
        await _earn(s, uid, 1000)
        it = await _item(s, cost=100)
        iid = it.id
        await _redeem(s, uid, iid, "pending-key-1")
        with pytest.raises(MileageError) as e:
            await _redeem(s, uid, iid, "pending-key-2")
        assert e.value.code == "pending"
        assert await _count(s, MileageSpend) == 1


async def test_monthly_limit_counts_kst_calendar_month(session_factory, mclock):
    async with session_factory() as s:
        u = await _user(s)
        uid = u.id
        await _earn(s, uid, 1000)
        it = await _item(s, cost=100)
        iid = it.id
        r1 = await _redeem(s, uid, iid, "month-key-1")
        await mileage.reject(s, r1.id, "테스트")  # 거절된 건 세지 않는다
        for i in (2, 3):
            r = await _redeem(s, uid, iid, f"month-key-{i}")
            await mileage.mark_sent(s, r.id, None)
        with pytest.raises(MileageError) as e:
            await _redeem(s, uid, iid, "month-key-4")
        assert e.value.code == "monthly"
        # 12월 1일 00:00 KST(= 11월 30일 15:00 UTC)부터 새 달
        mclock.set(dt.datetime(2026, 11, 30, 14, 59, tzinfo=dt.UTC))
        with pytest.raises(MileageError):
            await _redeem(s, uid, iid, "month-key-5")
        mclock.set(dt.datetime(2026, 11, 30, 15, 0, tzinfo=dt.UTC))
        assert (await _redeem(s, uid, iid, "month-key-6")).status == REQUESTED


async def test_account_must_be_seven_days_old(session_factory, mclock):
    async with session_factory() as s:
        u = await _user(s, days_old=6)
        uid = u.id
        await _earn(s, uid, 1000)
        it = await _item(s, cost=100)
        iid = it.id
        with pytest.raises(MileageError) as e:
            await _redeem(s, uid, iid, "age-key-1")
        assert e.value.code == "too_new"
        assert await _count(s, MileageSpend) == 0
        mclock.set(NOW + dt.timedelta(days=1))
        assert (await _redeem(s, uid, iid, "age-key-2")).status == REQUESTED


async def test_out_of_stock(session_factory, mclock):
    async with session_factory() as s:
        u = await _user(s)
        uid = u.id
        await _earn(s, uid, 1000)
        it = await _item(s, cost=100, stock=0)
        iid = it.id
        with pytest.raises(MileageError) as e:
            await _redeem(s, uid, iid, "stock-key-1")
        assert e.value.code == "stock"
        assert await _count(s, MileageSpend) == 0


# --- 환불 ---


async def test_reject_and_cancel_refund_with_offsetting_rows(session_factory, mclock):
    async with session_factory() as s:
        u = await _user(s)
        uid = u.id
        other_id = (await _user(s, email="other@example.com")).id
        await _earn(s, uid, 100)
        it = await _item(s, cost=100, stock=3)
        iid = it.id
        rid = (await _redeem(s, uid, iid, "refund-key-1")).id
        assert (await mileage.balance(s, uid)).balance == 0
        done = await mileage.reject(s, rid, "확인이 안 됐어요")
        assert done.status == REJECTED and done.admin_note == "확인이 안 됐어요" and done.handled_at
        assert (await mileage.balance(s, uid)).balance == 100
        amounts = sorted(x.amount for x in await s.scalars(select(MileageSpend)))
        assert amounts == [-100, 100]  # 지우지 않고 반대 행을 더한다
        assert await s.scalar(select(RewardItem.stock).where(RewardItem.id == iid)) == 3
        with pytest.raises(MileageError):  # 두 번 돌려주지 않는다
            await mileage.reject(s, rid, None)
        with pytest.raises(MileageError):
            await mileage.mark_sent(s, rid, None)
        # 본인 취소도 돌려준다 — 남의 요청은 못 거둔다
        rid2 = (await _redeem(s, uid, iid, "refund-key-2")).id
        with pytest.raises(MileageError):
            await mileage.cancel(s, await s.get(User, other_id), rid2)
        await mileage.cancel(s, await s.get(User, uid), rid2)
        assert (await mileage.balance(s, uid)).balance == 100
        assert await _count(s, MileageSpend) == 4


# --- 기록 보호권 ---


async def test_freeze_calls_grant_once_and_respects_cap(session_factory, mclock, goals):
    async with session_factory() as s:
        u = await _user(s)
        uid = u.id
        await _earn(s, uid, 500)
        assert await _freeze(s, uid, "freeze-key-1") == 1
        assert [c for c in goals["calls"] if c[1] > 0] == [(uid, 1)]
        assert await _freeze(s, uid, "freeze-key-1") == 1  # 같은 폼 다시 보냄
        assert [c for c in goals["calls"] if c[1] > 0] == [(uid, 1)]
        assert await _freeze(s, uid, "freeze-key-2") == 2
        with pytest.raises(MileageError) as e:
            await _freeze(s, uid, "freeze-key-3")
        assert e.value.code == "freeze_full"
        assert len([c for c in goals["calls"] if c[1] > 0]) == 2
        assert (await mileage.balance(s, uid)).spent == 100


async def test_freeze_hidden_when_goals_module_missing(api, session_factory, no_goals):
    async with api(upstream) as c:
        await signup(c)
        page = await c.get("/mileage")
        assert page.status_code == 200 and "기록 보호권" not in page.text
        csrf = re.search(r'name="csrf" value="([^"]+)"', (await c.get("/me")).text).group(1)
        r = await c.post("/mileage/freeze", data={"csrf": csrf, "key": "freeze-http-1"})
        assert r.status_code == 303 and "err=unavailable" in r.headers["location"]


# --- 화면 ---


async def _signup_with_mileage(c, session_factory, amount=500, email="reader@example.com") -> int:
    await signup(c, email=email)
    async with session_factory() as s:
        u = await s.scalar(select(User).where(User.email == email))
        u.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
        await s.commit()
        if amount:
            await _earn(s, u.id, amount)
        return u.id


async def test_mileage_page_shows_rules_shelves_notice_and_http_exchange(api, session_factory, goals):
    async with api(upstream) as c:
        uid = await _signup_with_mileage(c, session_factory)
        async with session_factory() as s:
            await mileage.seed_samples(s)
            real = await _item(s, cost=100, title="진짜 리워드")
            sample_id = next(i.id for i in await mileage.catalogue(s) if i.is_sample)
        page = await c.get("/mileage")
        assert page.status_code == 200
        text = page.text
        for source, amount in points.RULES.items():
            assert f"+{amount}</b>" in text and points.LABELS[source] in text
        assert f"하루 {points.REACTION_DAILY_CAP}번까지" in text
        for line in (
            "마일리지는 현금으로 바꿀 수 없어요",
            "다른 사람에게 줄 수 없어요",
            "리워드는 운영자가 확인한 뒤 보내요",
            "부정하게 쌓은 마일리지는 회수돼요",
            "제휴와 리워드는 바뀌거나 끝날 수 있어요",
        ):
            assert line in text
        assert "앱에서 쓰기" in text and "기록 보호권" in text and "제휴 리워드" in text
        assert "예시 · 제휴 준비 중" in text and "제휴처가 들어오면 교환이 열려요" in text
        assert 'href="/leaderboard"' in text  # 순위는 여기서 간다
        assert "500<small>P</small>" in text

        csrf = re.search(r'name="csrf" value="([^"]+)"', text).group(1)
        r = await c.post(f"/mileage/rewards/{sample_id}", data={"csrf": csrf, "key": "http-sample-1"})
        assert "err=sample" in r.headers["location"]
        # 진짜 상품: 같은 폼을 두 번 눌러도 한 번
        for _ in range(2):
            r = await c.post(f"/mileage/rewards/{real.id}", data={"csrf": csrf, "key": "http-real-1"})
            assert r.status_code == 303 and "ok=requested" in r.headers["location"]
        async with session_factory() as s:
            assert await _count(s, MileageSpend, MileageSpend.user_id == uid) == 1
            assert await _count(s, RewardRedemption) == 1
        after = (await c.get("/mileage?ok=requested")).text
        assert "교환을 요청했어요" in after and "확인 중이에요" in after and "400<small>P</small>" in after
        assert "리워드 교환 · 진짜 리워드" in after  # 내역에 쓴 것도 보인다
        # CSRF 없이는 못 쓴다
        r = await c.post(f"/mileage/rewards/{real.id}", data={"key": "http-real-2"})
        assert r.status_code == 403
        # 본인 취소
        rid = re.search(r'action="/mileage/requests/(\d+)/cancel"', after).group(1)
        r = await c.post(f"/mileage/requests/{rid}/cancel", data={"csrf": csrf})
        assert "ok=cancelled" in r.headers["location"]
        assert "500<small>P</small>" in (await c.get("/mileage")).text


async def test_anonymous_is_sent_to_login(api):
    async with api(upstream) as c:
        r = await c.get("/mileage")
        assert r.status_code in (303, 307) or "로그인" in r.text


async def test_no_page_shows_the_old_word(api, session_factory, goals, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "reader@example.com")
    async with api(upstream) as c:
        await _signup_with_mileage(c, session_factory)
        pages = ["/", "/me", "/leaderboard", "/leaderboard?range=all", "/mileage", "/admin/rewards", "/races"]
        for path in pages:
            r = await c.get(path)
            assert r.status_code == 200, path
            assert "포인트" not in r.text, path
        assert "마일리지" in (await c.get("/me")).text
        assert "마일리지 순위" in (await c.get("/leaderboard")).text


async def test_me_card_links_to_mileage_and_shows_balance(api, session_factory, goals):
    async with api(upstream) as c:
        uid = await _signup_with_mileage(c, session_factory, amount=120)
        async with session_factory() as s:
            await _freeze(s, uid, "card-key-1")
        me = (await c.get("/me")).text
        card = re.search(r'<a class="points-card[^>]*>.*?</a>', me, re.S).group(0)
        assert 'href="/mileage"' in card and "70<small>P</small>" in card and "마일리지" in card


# --- 관리자 ---


async def test_admin_routes_refuse_non_admins(api, session_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_emails", "boss@example.com")
    async with api(upstream) as c:
        for path in ("/admin/rewards",):
            assert (await c.get(path)).status_code == 404
        await _signup_with_mileage(c, session_factory)
        csrf = re.search(r'name="csrf" value="([^"]+)"', (await c.get("/me")).text).group(1)
        assert (await c.get("/admin/rewards")).status_code == 404
        async with session_factory() as s:
            it = await _item(s)
        posts = [
            ("/admin/rewards/items", {"partner_name": "x", "title": "y", "cost": "1"}),
            ("/admin/rewards/items/samples", {}),
            (f"/admin/rewards/items/{it.id}/toggle", {"field": "is_sample"}),
            (f"/admin/rewards/items/{it.id}/stock", {"stock": "9"}),
            ("/admin/rewards/requests/1/send", {}),
            ("/admin/rewards/requests/1/reject", {}),
        ]
        for path, data in posts:
            assert (await c.post(path, data={"csrf": csrf, **data})).status_code == 404, path
        async with session_factory() as s:
            assert (await s.get(RewardItem, it.id)).stock is None


async def test_admin_manages_items_and_queue(api, session_factory, monkeypatch, mclock):
    monkeypatch.setattr(get_settings(), "admin_emails", "boss@example.com")
    async with session_factory() as s:
        buyer = await _user(s, email="buyer@example.com")
        await _earn(s, buyer.id, 300)
    async with api(upstream) as c:
        await signup(c, email="boss@example.com")
        page = await c.get("/admin/rewards")
        assert page.status_code == 200 and 'href="/admin/rewards"' in page.text
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        r = await c.post("/admin/rewards/items/samples", data={"csrf": csrf})
        assert "msg=" in r.headers["location"]
        r = await c.post(
            "/admin/rewards/items",
            data={"csrf": csrf, "partner_name": "새 제휴처", "title": "새 상품", "cost": "100", "stock": "2"},
        )
        assert r.status_code == 303 and "msg=" in r.headers["location"]
        async with session_factory() as s:
            item = await s.scalar(select(RewardItem).where(RewardItem.title == "새 상품"))
            assert item.is_sample is False and item.stock == 2  # 체크하지 않으면 진짜
            sample = await s.scalar(select(RewardItem).where(RewardItem.is_sample.is_(True)))
        await c.post(f"/admin/rewards/items/{sample.id}/toggle", data={"csrf": csrf, "field": "active"})
        await c.post(f"/admin/rewards/items/{item.id}/stock", data={"csrf": csrf, "stock": ""})
        async with session_factory() as s:
            assert (await s.get(RewardItem, sample.id)).active is False
            assert (await s.get(RewardItem, item.id)).stock is None
            buyer = await s.get(User, buyer.id)
            req = await mileage.redeem(s, buyer, item.id, "admin-queue-1")
        queue = (await c.get("/admin/rewards")).text
        assert "buyer@example.com" in queue and "가입 30일째" in queue and "최근 7일 적립 300P" in queue
        r = await c.post(f"/admin/rewards/requests/{req.id}/send", data={"csrf": csrf, "note": "11/18 보냄"})
        assert "msg=" in r.headers["location"]
        async with session_factory() as s:
            done = await s.get(RewardRedemption, req.id)
            assert done.status == SENT and done.admin_note == "11/18 보냄"
            assert (await mileage.balance(s, buyer.id)).balance == 200
        r = await c.post(f"/admin/rewards/requests/{req.id}/reject", data={"csrf": csrf})
        assert "err=" in r.headers["location"]  # 이미 보낸 건 거절(환불)할 수 없다


# --- 기능 플래그 ---


async def test_flag_off_404_and_card_links_to_leaderboard(api, session_factory, monkeypatch, goals):
    monkeypatch.setattr(get_settings(), "feature_mileage", False)
    monkeypatch.setattr(get_settings(), "admin_emails", "reader@example.com")
    async with api(upstream) as c:
        await _signup_with_mileage(c, session_factory)
        csrf = re.search(r'name="csrf" value="([^"]+)"', (await c.get("/me")).text).group(1)
        for path in ("/mileage", "/admin/rewards"):
            assert (await c.get(path)).status_code == 404, path
        for path in ("/mileage/freeze", "/mileage/rewards/1", "/admin/rewards/items/samples"):
            assert (await c.post(path, data={"csrf": csrf})).status_code == 404, path
        me = (await c.get("/me")).text
        card = re.search(r'<a class="points-card[^>]*>.*?</a>', me, re.S).group(0)
        assert 'href="/leaderboard"' in card and "/mileage" not in me
        assert "순위 ›" in card and "500<small>P</small>" in card
