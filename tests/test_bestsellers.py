"""지금의 베스트셀러(app/services/bestsellers.py): Yes24 화면 읽기와 적어 둔 파일."""

import json
from pathlib import Path

from app.services import bestsellers

PAGE = (Path(__file__).parent / "fixtures" / "yes24_bestseller.html").read_text(encoding="utf-8")


def test_parses_real_bestseller_markup_in_rank_order():
    books = bestsellers.parse_yes24(PAGE, limit=3)
    assert [b["rank"] for b in books] == [1, 2, 3] and len({b["id"] for b in books}) == 3
    first = books[0]
    assert first["id"] == "y197068949" and first["title"] == "가호"
    assert first["author"] == "무라카미 하루키"  # '저/역' 표기에서 지은이만
    assert first["publisher"] == "문학동네" and first["price"] == 16650
    assert first["cover"] == "https://image.yes24.com/goods/197068949/XL"
    assert first["url"].startswith("https://www.aladin.co.kr/search/wsearchresult.aspx?SearchTarget=EBook")


def test_broken_or_empty_page_gives_nothing():
    assert bestsellers.parse_yes24("<html><body>점검 중</body></html>") == []


def test_saved_file_has_at_least_three_books_with_covers():
    data = json.loads(bestsellers.DATA.read_text(encoding="utf-8"))
    assert data["source"] and data["updated"]
    books = bestsellers.load()
    assert len(books) >= 3 and [b["rank"] for b in books[:3]] == [1, 2, 3]
    assert all(b["id"].startswith("y") and b["cover"].startswith("https://image.yes24.com/") for b in books)


def test_missing_file_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(bestsellers, "DATA", tmp_path / "none.json")
    assert bestsellers.load() == []
