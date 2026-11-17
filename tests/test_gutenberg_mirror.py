"""구텐베르크 본 서버가 503(점검·과부하)일 때 공식 미러에서 평문을 받는다. 네트워크에는 나가지 않는다."""

import httpx
import pytest

from app.sources.gutendex import GutendexSource
from tests.conftest import mock_client

RAW = """The Project Gutenberg eBook of Pride and Prejudice

Title: Pride and Prejudice
Author: Jane Austen

*** START OF THE PROJECT GUTENBERG EBOOK PRIDE AND PREJUDICE ***
It is a truth universally acknowledged,
that a single man in possession of a good fortune,
must be in want of a wife.
*** END OF THE PROJECT GUTENBERG EBOOK PRIDE AND PREJUDICE ***
License"""
BODY = "It is a truth universally acknowledged, that a single man in possession of a good fortune, must be in want of a wife."
BOOK = {
    "id": 1342,
    "title": "Pride and Prejudice",
    "authors": [{"name": "Austen, Jane"}],
    "languages": ["en"],
    "formats": {"text/plain; charset=utf-8": "https://www.gutenberg.org/ebooks/1342.txt.utf-8"},
}
MAINTENANCE = httpx.Response(503, html="<title>Temporarily Down for Maintenance</title>")


def server(gutendex_up: bool, main_up: bool, mirror_up: bool = True):
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.host)
        if req.url.host == "gutendex.com":
            return httpx.Response(200, json=BOOK) if gutendex_up else httpx.Response(502)
        if req.url.host == "www.gutenberg.org":
            return httpx.Response(200, text=RAW) if main_up else MAINTENANCE
        if req.url.host == "gutenberg.pglaf.org" and req.url.path == "/cache/epub/1342/pg1342.txt":
            return httpx.Response(200, text=RAW) if mirror_up else httpx.Response(503)
        return httpx.Response(404)

    return handler, seen


async def test_text_comes_from_the_mirror_when_gutenberg_is_down():
    handler, seen = server(gutendex_up=True, main_up=False)
    meta, text = await GutendexSource(mock_client(handler)).fetch_book("1342")
    assert text == BODY and meta.title == "Pride and Prejudice" and meta.authors == ("Austen, Jane",)
    # gutendex가 알려 준 주소 → 본 서버의 캐시 → 미러 순서
    assert seen == ["gutendex.com", "www.gutenberg.org", "www.gutenberg.org", "gutenberg.pglaf.org"]


async def test_mirror_also_covers_gutendex_and_gutenberg_both_down():
    handler, seen = server(gutendex_up=False, main_up=False)
    meta, text = await GutendexSource(mock_client(handler)).fetch_book("1342")
    assert text == BODY
    # 책 정보는 평문 머리말에서 읽는다
    assert meta.title == "Pride and Prejudice" and meta.authors == ("Jane Austen",) and meta.has_fulltext
    assert seen[-1] == "gutenberg.pglaf.org"


async def test_mirror_is_not_asked_when_gutenberg_answers():
    handler, seen = server(gutendex_up=True, main_up=True)
    _, text = await GutendexSource(mock_client(handler)).fetch_book("1342")
    assert text == BODY and "gutenberg.pglaf.org" not in seen


async def test_error_surfaces_when_every_server_is_down():
    handler, _ = server(gutendex_up=True, main_up=False, mirror_up=False)
    with pytest.raises(httpx.HTTPStatusError):
        await GutendexSource(mock_client(handler)).fetch_book("1342")


async def test_html_error_page_served_with_200_is_not_taken_for_the_book():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "gutendex.com":
            return httpx.Response(500)
        if req.url.host == "www.gutenberg.org":
            return httpx.Response(200, html="<html><title>Please wait</title></html>")
        return httpx.Response(200, text=RAW)

    _, text = await GutendexSource(mock_client(handler)).fetch_book("1342")
    assert text == BODY
