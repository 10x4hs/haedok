"""체류시간(dwell time) 검증 — 퀴즈를 대체하는 치팅 방지 규칙 (PRD BTL-01).

기준: 쪽(청크)마다 그 쪽 본문을 '빠르지만 진짜로 읽는' 속도로 읽는 데 걸리는 시간.
  필요 시간 = 한글·한자·가나 글자 ÷ 분당 1000자 + 그 밖의 단어 ÷ 분당 400단어, 3~60초로 자름.
글자 크기·화면 크기는 넣지 않는다 — 쪽은 서버가 나눈 고정된 본문이라 크기를 바꿔도 읽을 글자는
같고, 임계 글자 크기 위에서는 읽기 속도가 크기와 무관하다(Legge & Cheung 2004).
서버가 잰 시간으로만 판정하고, 리더(reader.js)는 서버가 내려준 min_dwell_ms를 그대로 센다.
시연용 배율(DWELL_SCALE)은 app.services.books.required_dwell_ms 가 자른 뒤에 곱한다.

근거(자세한 표는 README의 '진도 검증'과 조사 노트):
- 영어 성인 묵독 평균 논픽션 238·픽션 260 wpm, 대부분 175–320 wpm (Brysbaert 2019,
  J. Memory and Language 109:104047, 메타분석 190개 연구).
- 한국어 묵독 226 어절/분(같은 논문 Table 5) × 어절당 2.7–2.9음절 ≈ 분당 630음절.
  낭독 133 어절/분 × 2.8 ≈ 370음절/분 — IReST 17개 언어 낭독 평균 370음절/분과 맞는다
  (Trauzettel-Klosinski & Dietz 2012, IOVS 53:5452).
- 평균의 1.5배: 정상 범위 상단(평균의 약 1.25배)보다 빠르고, 훑어읽기(Carver 450 wpm ≈ 1.8배)·
  '두 배 속독은 이해를 잃는다'(Rayner 외 2016, Psychol. Sci. Public Interest 17:4)보다 느리다.
  그래서 진짜 빠른 독자는 통과하고, 훑어 넘기기는 걸린다.
"""

import math

# 한글·한자·가나: 공백 뺀 글자(문장부호 포함) 분당 1000자 — 한국어 평균 ~630음절/분 × 1.5, 부호 몫 더함
CJK_CHARS_PER_MIN = 1000
# 라틴 문자 등: 단어 분당 400개 — 픽션 평균 260 wpm × 1.5 (Brysbaert 2019)
WORDS_PER_MIN = 400
# 하한: 제목만 있는 쪽·마지막 짧은 쪽도 이만큼은 본다(1000자/분이면 50자). 연타로 넘기기 방지
MIN_PAGE_MS = 3_000
# 상한: 보통 쪽(한국어 ~930자 ≈ 56초, 영어 ~315단어 ≈ 47초)은 이미 아래. 못 쪼갠 긴 문단만 여기서 멈춘다
MAX_PAGE_MS = 60_000
# 탭을 켜두고 방치해 체류시간을 부풀리는 것 방지 (PRD EXC-04) — 그 쪽 필요 시간의 10배까지만
DWELL_CAP_MULTIPLIER = 10

_CJK_RANGES = (
    (0x1100, 0x11FF),  # 한글 자모
    (0x3040, 0x30FF),  # 히라가나·가타카나
    (0x3130, 0x318F),  # 한글 호환 자모
    (0x3400, 0x4DBF),  # 한자 확장 A
    (0x4E00, 0x9FFF),  # 한자
    (0xAC00, 0xD7A3),  # 한글 음절
    (0xF900, 0xFAFF),  # 한자 호환
)


def _is_cjk(c: str) -> bool:
    o = ord(c)
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES)


def counted_chars(text: str) -> int:
    """공백을 뺀 글자 수 — 통계용."""
    return sum(not c.isspace() for c in text)


def reading_units(text: str) -> tuple[int, int]:
    """(글자, 단어). 공백으로 나눈 토큰에 한글·한자·가나가 있으면 그 토큰의 글자 수를,
    아니면 글자·숫자가 있는 토큰을 단어 하나로 센다. 문장부호만 있는 토큰은 세지 않는다.
    섞인 글도 각자의 속도로 더해지고, 책의 언어 표시가 틀려도 결과가 같다."""
    chars = words = 0
    for token in text.split():
        if any(_is_cjk(c) for c in token):
            chars += len(token)
        elif any(c.isalnum() for c in token):
            words += 1
    return chars, words


def min_dwell_ms(text: str = "", language: str | None = None) -> int:
    """한 쪽을 '다 읽었다'고 인정하는 최소 체류시간(DWELL_SCALE 곱하기 전)."""
    del language  # 글자 종류는 토큰마다 본다
    chars, words = reading_units(text)
    ms = math.ceil(chars * 60_000 / CJK_CHARS_PER_MIN + words * 60_000 / WORDS_PER_MIN)
    return max(MIN_PAGE_MS, min(ms, MAX_PAGE_MS))


def credited_dwell_ms(dwell_ms: int, min_ms: int) -> int:
    return max(0, min(dwell_ms, min_ms * DWELL_CAP_MULTIPLIER))


def is_verified(total_dwell_ms: int, min_ms: int) -> bool:
    """같은 청크 재방문 시 누적 체류시간으로 판정한다."""
    return total_dwell_ms >= min_ms
