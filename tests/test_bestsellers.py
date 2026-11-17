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


def test_author_and_title_noise_is_cleaned_and_bundles_are_skipped():
    def item(no, name, auth):
        return (
            f'<li data-goods-no="{no}"><a class="gd_name" href="#">{name}</a>'
            f'<span class="authPub info_auth">{auth}</span></li>'
        )

    page = (
        item(1, "[예스리커버] 너를 아끼며 살아라", "나태주 저")
        + item(2, "100일의 거짓말 대본집 세트", "작가 저")
        + item(3, "트렌드 코리아 2027", "김난도, 전미영 저 외 8명 정보 더 보기")
    )
    books = bestsellers.parse_yes24(page)
    assert [b["title"] for b in books] == ["너를 아끼며 살아라", "트렌드 코리아 2027"]  # 묶음 상품은 건너뛴다
    assert [b["author"] for b in books] == ["나태주", "김난도, 전미영"]
    assert [b["rank"] for b in books] == [1, 2]


def test_saved_file_has_three_books_per_category_and_one_leader_each():
    data = json.loads(bestsellers.DATA.read_text(encoding="utf-8"))
    assert data["source"] and data["updated"]
    books = bestsellers.load()
    names = [name for name, _ in bestsellers.CATEGORIES]
    assert names == ["소설", "인문", "에세이"]
    for name in names:
        ranks = [b["rank"] for b in books if b["category"] == name]
        assert ranks[:1] == [1] and ranks == sorted(ranks)
    leaders = bestsellers.leaders(books)
    assert [b["category"] for b in leaders] == names and all(b["rank"] == 1 for b in leaders)
    assert all(b["id"].startswith("y") and b["cover"].startswith("https://image.yes24.com/") for b in books)


def test_missing_file_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(bestsellers, "DATA", tmp_path / "none.json")
    assert bestsellers.load() == []
