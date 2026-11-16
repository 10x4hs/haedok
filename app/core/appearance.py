"""화면 테마 선택지.

두 갈래다.
- 테마(PRESETS): 바탕·표면·글자·강조 색을 한 벌로 묶은 것. 고르면 아래 직접 고르기 값보다 먼저 쓴다.
  색은 모두 서버에서 정해 <html style="--bg:…">로 내려가고, 대비(4.5:1)는 tests/test_appearance.py가 잰다.
- 직접 고르기(MODES·BACKGROUNDS·ACCENTS): 배경 모양은 static/looks.css `.fx[data-bg=…]`와 짝.

저장은 users.settings(JSON)에 {"theme", "mode", "bg", "accent"} — 열을 새로 만들지 않는다.
"""

MODES = {"auto": "자동", "light": "밝게", "dark": "어둡게"}

# 묶음 → [(id, 이름, 움직임 여부)]
BACKGROUNDS: dict[str, list[tuple[str, str, bool]]] = {
    "기본": [
        ("plain", "기본", False),
        ("ash", "잿빛", False),
        ("sand", "모래", False),
        ("sage", "세이지", False),
        ("fog", "안개", False),
    ],
    "책": [
        ("study", "서재", False),
        ("paper", "종이", False),
        ("inkwash", "수묵", False),
        ("leather", "가죽 장정", False),
        ("grid", "원고지", False),
        ("candle", "촛불 독서", True),
    ],
    "자연": [
        ("forest", "숲", False),
        ("ocean", "물결", True),
        ("hills", "언덕", False),
        ("desert", "사막", False),
        ("petals", "꽃잎", True),
        ("sunset", "노을", False),
        ("seaside", "바닷가", False),
    ],
    "날씨": [
        ("sunny", "맑음", False),
        ("clouds", "구름", True),
        ("rain", "비", True),
        ("snow", "눈", True),
        ("storm", "뇌우", True),
        ("dawn", "새벽", False),
    ],
    "우주": [
        ("stars", "별밤", True),
        ("aurora", "오로라", True),
        ("nebula", "성운", False),
        ("prism", "프리즘", False),
        ("flow", "흐르는 빛", True),
        ("lava", "용암", True),
    ],
}
BG_IDS = {bid for group in BACKGROUNDS.values() for bid, _, _ in group}

# 배경의 밝기. 글자색은 테마(밝게·어둡게)가 아니라 실제로 깔린 배경을 따라야 한다 —
# 밝은 테마에서 어두운 배경을 고르면 검은 글자가 검은 배경 위에 놓여 안 보였다.
# "auto" = 바탕이 테마 색(var(--bg))이라 테마를 그대로 따라간다.
# 나머지 배경은 모두 짙은 바탕(--base)이고, 밝은 테마의 밝기 필터를 거쳐도 어둡다.
TONES = ("auto", "dark", "light")
BG_TONE: dict[str, str] = {bid: "dark" for bid in BG_IDS} | {"plain": "auto"}

ACCENTS = {
    "mono": ("기본", "#fafafa"),
    "multi": ("여러 색", "#ff9ec4"),
    "blue": ("파랑", "#5aa0ff"),
    "indigo": ("남색", "#8b93ff"),
    "violet": ("보라", "#c39bff"),
    "pink": ("분홍", "#ffb3c7"),
    "red": ("빨강", "#ff7a6b"),
    "orange": ("주황", "#ffa95a"),
    "yellow": ("노랑", "#ffd95a"),
    "green": ("초록", "#7fd99a"),
    "teal": ("청록", "#5fd3d3"),
    "gray": ("회색", "#b7bcc6"),
}

DEFAULT = {"mode": "auto", "bg": "plain", "accent": "pink", "theme": ""}
SAVED_KEYS = ("theme", "mode", "bg", "accent")


# ---------- 색 계산 (WCAG 2.x 상대 휘도) ----------
def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def luminance(h: str) -> float:
    def ch(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, t: float) -> str:
    """a를 t만큼, b를 1-t만큼 섞는다 (CSS color-mix와 같은 뜻)."""
    return _hex(x * t + y * (1 - t) for x, y in zip(_rgb(a), _rgb(b), strict=True))


def _rgba(h: str, a: float) -> str:
    r, g, b = _rgb(h)
    return f"rgba({r},{g},{b},{a})"


def _readable(strong: str, weak: str, grounds: tuple[str, ...], start: float, need: float = 4.6) -> str:
    """strong을 start만큼 섞은 색부터, grounds 모두에서 need 대비가 나올 때까지 strong 쪽으로 당긴다."""
    t = start
    while t < 1:
        c = mix(strong, weak, t)
        if all(contrast(c, g) >= need for g in grounds):
            return c
        t += 0.02
    return strong


# ---------- 테마 ----------
# (id, 이름, 한 줄 분위기, 모드, 바탕, 표면, 강조, 글자)
_PRESET_ROWS = {
    "종이·책": [
        ("book", "종이책", "따뜻한 흰 종이", "light", "#FAF7F0", "#FFFFFF", "#E5708F", "#2B2724"),
        ("sepia", "세피아", "오래된 책", "light", "#F4ECD8", "#FBF5E6", "#B4543A", "#3B2F25"),
        ("classic", "고전", "누렇게 바랜 장정", "light", "#EADFC6", "#F4EBD6", "#7B4B2A", "#33281C"),
    ],
    "밤": [
        ("midnight", "심야 독서", "불 끄고 한 장 더", "dark", "#0F1115", "#181B21", "#FFB3C7", "#E6E8EC"),
        ("library", "도서관", "오크와 녹색 램프", "dark", "#1C2420", "#26312B", "#D9B26A", "#E8E4D8"),
        ("moonlight", "달빛", "조용한 푸른 밤", "dark", "#12151C", "#1B2029", "#B8C4FF", "#E1E5F0"),
    ],
    "자연": [
        ("woods", "숲속", "나뭇잎 사이 햇살", "light", "#EAF0E3", "#F6FAF1", "#4F7F52", "#25301F"),
        ("sea", "바다", "맑은 물빛", "light", "#E7F1F2", "#F5FAFA", "#2C8DA6", "#1D2D33"),
        ("rainy", "비 오는 날", "창밖에 빗소리", "dark", "#1B2128", "#252D36", "#7FB7C9", "#DDE4EA"),
    ],
    "계절": [
        ("blossom", "벚꽃", "봄날 분홍", "light", "#FBEFF2", "#FFF8FA", "#E5708F", "#34272B"),
        # 가을 강조색은 제안값 #C25A2B에서 한 톤 짙게 — 흰 글자가 4.5:1을 넘도록
        ("autumn", "가을", "단풍 든 오후", "light", "#F6EBDD", "#FCF4EA", "#B4511F", "#3A2A20"),
        ("snowday", "눈 오는 날", "하얀 겨울 아침", "light", "#F1F4F8", "#FFFFFF", "#5B8DEF", "#1E2633"),
    ],
    "하늘": [
        ("daybreak", "새벽", "해 뜨기 전 푸른빛", "dark", "#141A2B", "#1D2540", "#8FB4FF", "#E3E8F5"),
        ("dusk", "노을", "해 질 녘 주황", "dark", "#2A1D1F", "#3A282B", "#FF9E7A", "#F3E4DE"),
        ("cosmos", "우주", "별 사이 보랏빛", "dark", "#0B0E1A", "#151A2E", "#C7A6FF", "#E8EAF6"),
    ],
    "단정하게": [
        ("latte", "카페 라떼", "창가 자리 오후", "light", "#F1E7DB", "#FAF3EA", "#A8643C", "#3A2C22"),
        ("ink", "먹물", "먹과 한지", "light", "#F2EFE8", "#FAF8F3", "#2F2F2F", "#1F1F1F"),
        ("mono", "흑백", "가장 또렷하게", "dark", "#000000", "#121212", "#FFFFFF", "#F5F5F5"),
    ],
}

INK_DARK, INK_LIGHT = "#141414", "#FFFFFF"


def _tokens(mode: str, bg: str, surface: str, accent: str, text: str) -> dict[str, str]:
    """테마 다섯 색 → 앱이 쓰는 CSS 변수 전부. 흐린·강조 글자는 대비가 맞을 때까지 글자색 쪽으로 당긴다."""
    grounds = (bg, surface)
    ink = max((INK_DARK, INK_LIGHT), key=lambda c: contrast(accent, c))
    return {
        "--bg": bg,
        "--surface": surface,
        "--text": text,
        "--body": mix(text, bg, 0.88),
        "--dim": _readable(text, bg, grounds, 0.58),  # 글자 58%에서 시작
        "--line": mix(text, bg, 0.12),
        "--sunken": _rgba(text, 0.06),
        "--glass": _rgba(surface, 0.78 if mode == "light" else 0.7),
        "--glass-line": _rgba(text, 0.12 if mode == "light" else 0.16),
        "--accent": accent,
        "--accent-ink": ink,
        "--accent-strong": _readable(text, accent, grounds, 0.0),
        "--glow": _rgba(accent, 0.28),
    }


PRESETS: dict[str, dict] = {}
PRESET_GROUPS: dict[str, list[str]] = {}
for _group, _rows in _PRESET_ROWS.items():
    PRESET_GROUPS[_group] = []
    for _id, _name, _mood, _mode, _bg, _surf, _acc, _text in _rows:
        PRESETS[_id] = {
            "id": _id, "name": _name, "mood": _mood, "mode": _mode, "group": _group,
            "bg": _bg, "surface": _surf, "accent": _acc, "text": _text,
            "vars": _tokens(_mode, _bg, _surf, _acc, _text),
        }  # fmt: skip
        PRESET_GROUPS[_group].append(_id)


def style_of(tokens: dict[str, str]) -> str:
    """<html style="…">에 넣을 문자열."""
    return ";".join(f"{k}:{v}" for k, v in tokens.items())


def resolve(settings: dict | None) -> dict:
    """저장된 값 중 알려진 것만 받아들이고 나머지는 기본값으로.

    mode·bg·accent는 '직접 고르기'에 저장된 값(테마를 골라도 그대로 남는다).
    html_theme·fx_bg·accent_attr·accent_hex·bg_tone·style·label은 화면에 실제로 쓰는 값.
    """
    s = dict(DEFAULT)
    for key, allowed in (("mode", MODES), ("bg", BG_IDS), ("accent", ACCENTS), ("theme", PRESETS)):
        if settings and isinstance(settings.get(key), str) and settings[key] in allowed:
            s[key] = settings[key]
    preset = PRESETS.get(s["theme"])
    if preset:
        s |= {
            "html_theme": preset["mode"], "fx_bg": "theme", "accent_attr": "theme",
            "accent_hex": preset["accent"], "bg_tone": preset["mode"],
            "style": style_of(preset["vars"]), "label": preset["name"],
        }  # fmt: skip
    else:
        hexv = ACCENTS[s["accent"]][1]
        s |= {
            "html_theme": s["mode"], "fx_bg": s["bg"], "accent_attr": s["accent"],
            "accent_hex": hexv, "bg_tone": tone_of(s["bg"]),
            "style": f"--accent:{hexv}", "label": MODES[s["mode"]],
        }  # fmt: skip
    return s


def saved(look: dict) -> dict:
    """resolve() 결과에서 계정에 저장할 값만."""
    return {k: look[k] for k in SAVED_KEYS}


def tone_of(bg: str) -> str:
    """배경 id → "dark" | "light" | "auto". 모르는 id는 테마를 따른다."""
    return BG_TONE.get(bg, "auto")
