"""AI 화면의 절제 규칙(round 9 과 같다) — 고정폭 글꼴 없음 · 긴 안내문 없음 · 도우미는 앱 셸."""

from __future__ import annotations

import re

import httpx
import pytest

from app.core.config import get_settings
from tests.conftest import signup
from tests.test_ai_reader import make_book, set_verified, uid


def upstream(_: httpx.Request) -> httpx.Response:
    return httpx.Response(404)


@pytest.fixture
def ai_on(monkeypatch):
    s = get_settings()
    for k, v in (("ai_provider", "fake"), ("feature_ai_rag", True), ("feature_ai_shop", True), ("feature_ai_reader", True)):
        monkeypatch.setattr(s, k, v)
    return s


def _main(html: str) -> str:
    return html[html.index("<main") : html.index("</main>")]


async def test_assistant_is_an_app_shell_with_quiet_text(api, ai_on):
    async with api(upstream) as c:
        await signup(c)
        html = (await c.get("/assistant")).text
    assert 'class="app ai-shop-page ai-page"' in html
    dock = html[html.index('class="ai-dock"') :]
    assert 'class="ai-chips ai-quick"' in dock  # 빠른 답은 입력칸 자리에(대화 위에 겹치지 않게)
    assert "ai-fine" not in html  # 입력칸 아래 두 줄 안내 대신 머리줄의 '?'
    assert 'class="ai-info"' in html and "AI 답변은 틀릴 수 있어요" in html
    assert not re.search(r'class="[^"]*\bmono\b', _main(html))


async def test_ai_pages_use_no_monospace(api, session_factory, ai_on):
    async with api(upstream) as c:
        await signup(c)
        async with session_factory() as s:
            book = await make_book(s)
            await set_verified(s, await uid(s), book, 2)
        for path in (f"/books/{book.id}/ask", "/me/recap", f"/books/{book.id}"):
            html = (await c.get(path)).text
            assert not re.search(r'class="[^"]*\bmono\b[^"]*"[^>]*>[^<]*[가-힣]', _main(html) if "<main" in html else html), path
        ask = (await c.get(f"/books/{book.id}/ask")).text
        assert 'class="app ai-page"' in ask and "ai-page.js" in ask
