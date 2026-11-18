"""마일리지 화면(/mileage)과 리워드 관리(/admin/rewards).

FEATURE_MILEAGE 가 꺼져 있으면 모든 주소가 404 이고, /me 카드는 예전처럼 순위로 간다.
쓰기는 모두 폼 POST + CSRF + 로그인(관리자는 ADMIN_EMAILS) — 결과는 코드로만 돌려보낸다
(?ok=… / ?err=…). 주소창에 아무 글이나 넣어 화면에 띄울 수 없게.
"""

import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import admin, auth
from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models import User
from app.models_mileage import REQUESTED, RewardItem
from app.services import mileage
from app.services.mileage import MileageError
from app.web import _render, templates

router = APIRouter(include_in_schema=False)

RATE_LIMIT = 20  # 10분에 교환·구매·취소 시도
RATE_MSG = "너무 자주 눌렀어요. 잠시 뒤 다시 해 주세요"

OK = {
    "freeze": "기록 보호권을 받았어요",
    "requested": "교환을 요청했어요. 운영자가 확인한 뒤 가입한 이메일로 보내요",
    "cancelled": "요청을 거뒀어요. 마일리지는 돌려받았어요",
}

templates.env.globals["mileage_on"] = mileage.enabled
templates.env.globals["mileage_status"] = mileage.STATUS_LABELS
templates.env.globals["reward_image"] = mileage.image_src
if ("/admin/rewards", "리워드") not in admin.NAV:  # 기능이 꺼지면 admin/_layout.html 이 숨긴다
    admin.NAV.append(("/admin/rewards", "리워드"))


def require_feature(settings: Settings = Depends(get_settings)) -> None:
    if not settings.feature_mileage:
        raise HTTPException(404)


def _back(path: str, ok: str | None = None, err: str | None = None) -> RedirectResponse:
    if ok:
        path += f"?ok={quote(ok)}"
    elif err:
        path += f"?err={quote(err)}"
    return RedirectResponse(path, status_code=303)


async def _limit(request: Request, session: AsyncSession, user: User) -> None:
    await ratelimit.check(session, f"mileage:{user.id}", RATE_LIMIT, ratelimit.WINDOW_S, RATE_MSG)


# --- 사용자 화면 ---


@router.get("/mileage", response_class=HTMLResponse, dependencies=[Depends(require_feature)])
async def page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> HTMLResponse:
    ok, err = request.query_params.get("ok", ""), request.query_params.get("err", "")
    freezes = await mileage.freeze_count(session, user.id)
    return _render(
        request,
        "mileage.html",
        user,
        bal=await mileage.balance(session, user.id),
        rules=mileage.earn_rules(),
        history=await mileage.history(session, user.id),
        items=await mileage.catalogue(session),
        requests=await mileage.my_requests(session, user.id),
        freezes=freezes,
        freeze_price=mileage.FREEZE_PRICE,
        freeze_max=mileage.FREEZE_MAX,
        monthly_limit=mileage.MONTHLY_LIMIT,
        min_days=mileage.MIN_ACCOUNT_DAYS,
        requested=REQUESTED,
        # 폼마다 새 키 — 두 번 눌러도 한 번만 처리된다
        key=lambda: secrets.token_urlsafe(16),
        ok_msg=OK.get(ok),
        err_msg=mileage.ERRORS.get(err) or (RATE_MSG if err == "rate" else None),
        info=err == "sample",  # 예시 상품 안내는 오류가 아니다
    )


@router.post("/mileage/freeze", dependencies=[Depends(require_feature)])
async def buy_freeze(
    request: Request,
    csrf: str = Form(""),
    key: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    try:
        await _limit(request, session, user)
        await mileage.buy_freeze(session, user, key)
    except ratelimit.RateLimited:
        return _back("/mileage", err="rate")
    except MileageError as e:
        return _back("/mileage", err=e.code)
    return _back("/mileage", ok="freeze")


@router.post("/mileage/rewards/{item_id}", dependencies=[Depends(require_feature)])
async def redeem(
    request: Request,
    item_id: int,
    csrf: str = Form(""),
    key: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    try:
        await _limit(request, session, user)
        await mileage.redeem(session, user, item_id, key)
    except ratelimit.RateLimited:
        return _back("/mileage", err="rate")
    except MileageError as e:
        return _back("/mileage", err=e.code)
    return _back("/mileage", ok="requested")


@router.post("/mileage/requests/{redemption_id}/cancel", dependencies=[Depends(require_feature)])
async def cancel(
    request: Request,
    redemption_id: int,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(auth.require_user),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    try:
        await _limit(request, session, user)
        await mileage.cancel(session, user, redemption_id)
    except ratelimit.RateLimited:
        return _back("/mileage", err="rate")
    except MileageError as e:
        return _back("/mileage", err=e.code)
    return _back("/mileage", ok="cancelled")


# --- 관리자: /admin/rewards ---

ADMIN = "/admin/rewards"


@router.get(ADMIN, response_class=HTMLResponse, dependencies=[Depends(require_feature)])
async def admin_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> HTMLResponse:
    return admin._page(
        request,
        user,
        "rewards",
        items=await mileage.catalogue(session, include_inactive=True),
        queue=await mileage.queue(session),
        requested=REQUESTED,
        min_days=mileage.MIN_ACCOUNT_DAYS,
        images=mileage.BUNDLED_IMAGES,
        hosts=sorted(mileage.ALLOWED_IMAGE_HOSTS),
    )


def _int(value: str, lo: int, hi: int) -> int | None:
    try:
        n = int(value.strip())
    except ValueError:
        return None
    return n if lo <= n <= hi else None


@router.post(f"{ADMIN}/items", dependencies=[Depends(require_feature)])
async def add_item(
    request: Request,
    csrf: str = Form(""),
    partner_name: str = Form(""),
    title: str = Form(""),
    cost: str = Form(""),
    stock: str = Form(""),
    is_sample: str = Form(""),
    sort: str = Form("0"),
    image: str = Form(""),
    image_url: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    try:
        n_image = _image(image, image_url)
    except ValueError:
        return admin._back(ADMIN, err=IMAGE_ERR)
    partner_name, title = partner_name.strip(), title.strip()
    n_cost = _int(cost, 1, 1_000_000)
    n_stock = None if not stock.strip() else _int(stock, 0, 1_000_000)
    if not (0 < len(partner_name) <= 60 and 0 < len(title) <= 100) or n_cost is None:
        return admin._back(ADMIN, err="제휴처·상품 이름과 가격(1 이상)을 확인해 주세요")
    if stock.strip() and n_stock is None:
        return admin._back(ADMIN, err="수량은 0 이상 숫자로, 제한이 없으면 비워 두세요")
    session.add(
        RewardItem(
            partner_name=partner_name,
            title=title,
            cost=n_cost,
            stock=n_stock,
            is_sample=is_sample == "1",
            active=True,
            sort=_int(sort, -10_000, 10_000) or 0,
            image=n_image,
        )
    )
    await session.commit()
    return admin._back(ADMIN, msg=f"{title}을(를) 올렸어요")


IMAGE_ERR = (
    "그림은 목록에서 고르거나 "
    + ", ".join(sorted(mileage.ALLOWED_IMAGE_HOSTS))
    + " 의 https 주소만 쓸 수 있어요"
)


def _image(choice: str, url: str) -> str | None:
    """목록에서 고른 키, 또는 주소 칸(있으면 이쪽이 앞선다). 허용 밖이면 ValueError."""
    return mileage.clean_image(url if url.strip() else choice)


@router.post(f"{ADMIN}/items/{{item_id}}/image", dependencies=[Depends(require_feature)])
async def set_image(
    request: Request,
    item_id: int,
    csrf: str = Form(""),
    image: str = Form(""),
    image_url: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    item = await _item(session, item_id)
    try:
        item.image = _image(image, image_url)
    except ValueError:
        return admin._back(ADMIN, err=IMAGE_ERR)
    await session.commit()
    return admin._back(ADMIN, msg=f"{item.title} 그림을 바꿨어요")


@router.post(f"{ADMIN}/items/samples", dependencies=[Depends(require_feature)])
async def add_samples(
    request: Request,
    csrf: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    n = await mileage.seed_samples(session)
    if not n:
        return admin._back(ADMIN, err="상품이 이미 있어서 예시를 넣지 않았어요")
    return admin._back(ADMIN, msg=f"예시 상품 {n}개를 넣었어요")


async def _item(session: AsyncSession, item_id: int) -> RewardItem:
    item = await session.get(RewardItem, item_id)
    if item is None:
        raise HTTPException(404)
    return item


@router.post(f"{ADMIN}/items/{{item_id}}/toggle", dependencies=[Depends(require_feature)])
async def toggle_item(
    request: Request,
    item_id: int,
    csrf: str = Form(""),
    field: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    if field not in ("active", "is_sample"):
        return admin._back(ADMIN, err="바꿀 항목을 알 수 없어요")
    item = await _item(session, item_id)
    setattr(item, field, not getattr(item, field))
    await session.commit()
    if field == "active":
        return admin._back(ADMIN, msg=f"{item.title}: {'보이기' if item.active else '숨김'}")
    return admin._back(ADMIN, msg=f"{item.title}: {'예시' if item.is_sample else '진짜 교환'}")


@router.post(f"{ADMIN}/items/{{item_id}}/stock", dependencies=[Depends(require_feature)])
async def set_stock(
    request: Request,
    item_id: int,
    csrf: str = Form(""),
    stock: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    item = await _item(session, item_id)
    if stock.strip():
        n = _int(stock, 0, 1_000_000)
        if n is None:
            return admin._back(ADMIN, err="수량은 0 이상 숫자로, 제한이 없으면 비워 두세요")
        item.stock = n
    else:
        item.stock = None
    await session.commit()
    return admin._back(ADMIN, msg=f"{item.title} 수량: {'제한 없음' if item.stock is None else item.stock}")


@router.post(f"{ADMIN}/requests/{{redemption_id}}/{{action}}", dependencies=[Depends(require_feature)])
async def handle_request(
    request: Request,
    redemption_id: int,
    action: str,
    csrf: str = Form(""),
    note: str = Form(""),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(admin.require_admin),
) -> RedirectResponse:
    auth.check_csrf(request, csrf)
    try:
        if action == "send":
            await mileage.mark_sent(session, redemption_id, note)
            return admin._back(ADMIN, msg="보냄으로 표시했어요")
        if action == "reject":
            await mileage.reject(session, redemption_id, note)
            return admin._back(ADMIN, msg="거절하고 마일리지를 돌려줬어요")
    except MileageError:
        return admin._back(ADMIN, err="이미 처리했거나 없는 요청이에요")
    raise HTTPException(404)
