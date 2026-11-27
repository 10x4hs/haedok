"""쪽마다 필요한 시간 = 쪽 본문의 글자(한국어)·단어(영어) ÷ 빠르지만 진짜 읽는 속도, 3~60초로 자름."""

from app.core.config import get_settings
from app.services import books as book_service
from app.services.pace import (
    CJK_CHARS_PER_MIN,
    DWELL_CAP_MULTIPLIER,
    MAX_PAGE_MS,
    MIN_PAGE_MS,
    WORDS_PER_MIN,
    credited_dwell_ms,
    is_verified,
    min_dwell_ms,
    reading_units,
)

KO_500 = ("가나다라마바사아자차 " * 50).strip()  # 공백 뺀 500자
EN_200 = " ".join(["reading"] * 200)  # 200단어


def test_constants_match_the_research():
    assert (CJK_CHARS_PER_MIN, WORDS_PER_MIN) == (1000, 400)
    assert (MIN_PAGE_MS, MAX_PAGE_MS) == (3_000, 60_000)
    assert DWELL_CAP_MULTIPLIER == 10


def test_korean_page_counts_non_space_characters():
    assert reading_units(KO_500) == (500, 0)
    assert min_dwell_ms(KO_500, "ko") == 30_000  # 500자 ÷ 1000자/분
    assert min_dwell_ms("가" * 750, "ko") == 45_000


def test_english_page_counts_words():
    assert reading_units(EN_200) == (0, 200)
    assert min_dwell_ms(EN_200, "en") == 30_000  # 200단어 ÷ 400단어/분
    assert min_dwell_ms(" ".join(["a"] * 300), "en") == 45_000


def test_punctuation_counts_in_korean_words_but_alone_is_nothing():
    # "말했다." 4자, "—" 은 문장부호만이라 0
    assert reading_units("그가 말했다. — 그렇지?") == (2 + 4 + 4, 0)


def test_mixed_text_adds_both_rates_and_ignores_the_language_label():
    text = KO_500 + " " + EN_200
    assert reading_units(text) == (500, 200)
    assert min_dwell_ms(text, "ko") == min_dwell_ms(text, "en") == min_dwell_ms(text) == 60_000
    # 한국어 속 영어 단어 한두 개
    assert reading_units("나는 Python 으로 썼다") == (2 + 2 + 2, 1)


def test_cjk_without_spaces_counts_each_character():
    assert reading_units("吾輩は猫である。名前はまだ無い。") == (16, 0)


def test_floor_for_short_pages():
    assert min_dwell_ms("", "ko") == MIN_PAGE_MS
    assert min_dwell_ms("제1장", "ko") == MIN_PAGE_MS
    assert min_dwell_ms("Chapter One", "en") == MIN_PAGE_MS


def test_ceiling_for_oversized_pages():
    assert min_dwell_ms("가" * 1001, "ko") == MAX_PAGE_MS
    assert min_dwell_ms("가" * 5000, "ko") == MAX_PAGE_MS
    assert min_dwell_ms("word " * 2000, "en") == MAX_PAGE_MS


def test_a_full_page_from_the_chunker_stays_under_the_ceiling():
    # 청커의 한국어 목표 1200자(공백 포함) ≈ 공백 뺀 930자 → 1분 안
    page = ("가나다 " * 300).strip()  # 1199자, 공백 뺀 900자
    assert len(page) < 1200 and min_dwell_ms(page, "ko") == 54_000


def test_dwell_scale_multiplies_after_the_clamp(monkeypatch):
    monkeypatch.setattr(get_settings(), "dwell_scale", 0.1)
    assert book_service.required_dwell_ms(KO_500, "ko") == 3_000
    assert book_service.required_dwell_ms("가" * 5000, "ko") == 6_000
    assert book_service.required_dwell_ms("", "ko") == 300
    monkeypatch.setattr(get_settings(), "dwell_scale", 1.0)
    assert book_service.required_dwell_ms(KO_500, "ko") == 30_000


def test_skimming_a_long_page_is_refused_but_a_fast_real_reader_passes():
    page = "가" * 900
    need = min_dwell_ms(page, "ko")
    assert not is_verified(2_000, need)  # 2초 만에 넘김
    # 규칙 속도(1000자/분) 그대로 읽은 빠른 독자는 통과
    fast_reader_ms = 900 * 60_000 // CJK_CHARS_PER_MIN
    assert is_verified(fast_reader_ms, need)
    # 그보다 조금이라도 빠르면 아직
    assert not is_verified(fast_reader_ms - 1, need)
    # 평균 독자(약 630자/분)는 넉넉히 통과
    assert is_verified(900 * 60_000 // 630, need)


def test_idle_tab_dwell_is_capped_relative_to_the_page():
    short, long = min_dwell_ms("가" * 100, "ko"), min_dwell_ms("가" * 900, "ko")
    assert credited_dwell_ms(10**9, short) == 6_000 * DWELL_CAP_MULTIPLIER
    assert credited_dwell_ms(10**9, long) == 54_000 * DWELL_CAP_MULTIPLIER
    assert credited_dwell_ms(-5, long) == 0
