"""공개 페이지 — 로그인 없이 누구나 본다: /guide(사용법) · /docs(규칙) · /developers(만든 방법).

/intro(예전 소개 페이지)는 /guide로 옮겼다. 화면의 규칙 숫자는 app/public_facts.py가 코드 상수에서 읽는다.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import auth, web
from app.public_facts import FACTS

templates = web.templates  # 같은 Jinja 환경(표지·공개 주소·nonce 전역)을 쓴다
router = APIRouter(include_in_schema=False)


def _page(request: Request, name: str, current: str, user) -> HTMLResponse:
    # 공개 문서라 화면은 늘 같은 어두운 공개용으로 그린다(user=None). 로그인했으면 메뉴만 '앱 열기'로
    return web._render(
        request, name, None, current=current, signed_in=user is not None, facts=FACTS, total=len(web.CATALOG)
    )


@router.get("/intro")
async def intro() -> RedirectResponse:
    return RedirectResponse("/guide", status_code=308)


@router.get("/guide", response_class=HTMLResponse)
async def guide(request: Request, user=Depends(auth.current_user)) -> HTMLResponse:
    return _page(request, "guide.html", "guide", user)


@router.get("/docs", response_class=HTMLResponse)
async def docs(request: Request, user=Depends(auth.current_user)) -> HTMLResponse:
    return _page(request, "docs.html", "docs", user)


@router.get("/developers", response_class=HTMLResponse)
async def developers(request: Request, user=Depends(auth.current_user)) -> HTMLResponse:
    return _page(request, "developers.html", "developers", user)
