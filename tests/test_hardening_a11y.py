"""hardening: UX 감사 잔여 — 인용 블록의 옆줄(side-stripe) 장식 제거, 글자 대비 4.5:1."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/app.css").read_text()
DARK_BG, LIGHT_BG = "#0a0a0a", "#f4f4f6"


def rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def lum(c) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = map(ch, c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def mix(fg, a, bg):
    return tuple(f * a + b * (1 - a) for f, b in zip(fg, bg, strict=True))


def rules_for(selector: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"(?:^|[}\s,])" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)]


def tokens(block_pattern: str) -> dict[str, str]:
    block = re.search(block_pattern, CSS, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def test_quotes_have_no_side_stripe_border():
    for sel in (".react-quote", ".side-quote"):
        body = " ".join(rules_for(sel))
        assert body, sel
        assert not re.search(r"border-(left|inline-start)\s*:", body), (sel, body)


def test_text_tokens_meet_4_5_to_1_in_both_themes():
    dark = tokens(r":root\{(.*?)\}")
    light = tokens(r':root\[data-theme="light"\]\{(.*?)\}')
    # 밝은 테마 대비 보강 블록이 나중에 덮어쓴다
    light |= tokens(r':root\[data-theme="light"\]\{(--dim:[^}]*)\}')
    for name in ("text", "body", "dim", "ok", "bad"):
        assert contrast(rgb(dark[name]), rgb(DARK_BG)) >= 4.5, ("dark", name)
        assert contrast(rgb(light[name]), rgb(LIGHT_BG)) >= 4.5, ("light", name, light[name])
    # 알림 상자(.form-ok/.form-err)는 같은 색 14% 배경 위에 글자를 얹는다
    for name in ("ok", "bad"):
        fg = rgb(light[name])
        assert contrast(fg, mix(fg, 0.14, rgb(LIGHT_BG))) >= 4.5, ("light tint", name)


def test_accent_coloured_text_uses_the_strong_token():
    """밝은 테마의 --accent(#e2557f)는 3.3:1 이라 글자에는 --accent-strong 을 쓴다."""
    for sel in (".empty-note a", ".board li:first-child .pos", ".for-book .mono"):
        body = " ".join(rules_for(sel))
        assert "color:var(--accent-strong)" in body.replace(" ", ""), (sel, body)
    me = (ROOT / "app/templates/me.html").read_text()
    assert ".points-card em{font-style:normal;color:var(--accent-strong)" in me
