"""배경 밝기(톤)가 글자색을 정한다 — 밝은 테마 + 어두운 배경에서 검은 글자가 안 보이던 버그."""

import re
from pathlib import Path

from app.core import appearance
from tests.conftest import signup
from tests.test_api import upstream

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def test_every_background_has_a_known_tone():
    assert set(appearance.BG_TONE) == appearance.BG_IDS
    assert set(appearance.BG_TONE.values()) <= set(appearance.TONES)
    # '기본'만 테마 색 바탕이라 테마를 따르고, 나머지는 짙은 바탕이다
    assert appearance.tone_of("plain") == "auto"
    assert appearance.tone_of("stars") == "dark"
    assert appearance.tone_of("paper") == "dark"
    assert appearance.tone_of("없는-배경") == "auto"


def test_resolve_exposes_bg_tone_regardless_of_mode():
    for mode in appearance.MODES:
        look = appearance.resolve({"mode": mode, "bg": "nebula"})
        assert look["bg_tone"] == "dark"
    assert appearance.resolve(None)["bg_tone"] == "auto"
    assert appearance.resolve({"bg": "nope"})["bg_tone"] == "auto"


async def _set_look(c, **look) -> None:
    home = await c.get("/")
    token = re.search(r'<meta name="csrf" content="([^"]+)"', home.text).group(1)
    r = await c.put("/api/v1/me/settings", json=look, headers={"X-CSRF-Token": token})
    assert r.status_code == 200


async def test_html_carries_bg_tone_for_dark_background_in_light_mode(api):
    async with api(upstream) as c:
        await signup(c)
        await _set_look(c, mode="light", bg="stars", accent="mono")
        page = await c.get("/discover")
        await _set_look(c, mode="light", bg="plain")
        plain = await c.get("/discover")
    html = re.search(r"<html[^>]*>", page.text).group(0)
    assert 'data-theme="light"' in html and 'data-bg-tone="dark"' in html
    assert 'data-bg-tone="auto"' in re.search(r"<html[^>]*>", plain.text).group(0)
    assert 'class="ranklist"' in page.text


async def test_customize_swatches_carry_their_tone_for_live_preview(api):
    async with api(upstream) as c:
        await signup(c)
        me = await c.get("/me")
    assert 'data-bgpick="plain" data-tone="auto"' in me.text
    assert 'data-bgpick="aurora" data-tone="dark"' in me.text


def test_css_dark_tone_overrides_text_vars_and_mono_accent():
    css = (STATIC / "app.css").read_text()
    block = css[css.index('html:root[data-bg-tone="dark"]{') :]
    block = block[: block.index("}")]
    for var in ("--text:#fafafa", "--body:", "--dim:", "--accent-strong:var(--accent)"):
        assert var in block
    looks = (STATIC / "looks.css").read_text()
    # 밝은 테마의 '기본' 강조색(검정)은 어두운 배경에선 쓰지 않는다
    mono_rules = [ln for ln in looks.splitlines() if '[data-accent="mono"]{--accent:#0a0a0a' in ln]
    assert mono_rules and all('[data-bg-tone="light"]' in ln or ':not([data-bg-tone="dark"])' in ln for ln in mono_rules)


def test_home_helix_supports_pointer_drag():
    js = (STATIC / "helix.js").read_text()
    for needle in ("pointerdown", "pointermove", "pointerup", "pointercancel", "dragstart"):
        assert needle in js
    assert "touch-action:none" in (STATIC / "app.css").read_text()


# ---------- 화면 테마(한 벌 색) ----------
def test_presets_are_grouped_and_complete():
    ids = [tid for group in appearance.PRESET_GROUPS.values() for tid in group]
    assert len(ids) == len(set(ids)) == len(appearance.PRESETS) >= 18
    assert 5 <= len(appearance.PRESET_GROUPS) <= 6
    hexv = re.compile(r"^#[0-9a-fA-F]{6}$")
    for p in appearance.PRESETS.values():
        assert p["mode"] in ("light", "dark") and p["name"] and p["mood"]
        for k in ("bg", "surface", "accent", "text"):
            assert hexv.match(p[k]), (p["id"], k)
        # <html style>에 그대로 들어가므로 따옴표·꺾쇠가 섞이면 안 된다
        assert not re.search(r"[\"'<>]", appearance.style_of(p["vars"]))


def test_presets_meet_contrast():
    """글자·흐린 글자·강조 글자는 바탕과 표면 모두에서, 강조 버튼 글자는 강조색 위에서 4.5:1 이상."""
    c = appearance.contrast
    for p in appearance.PRESETS.values():
        v = p["vars"]
        for ground in (p["bg"], p["surface"]):
            assert c(p["text"], ground) >= 4.5, (p["id"], "text")
            assert c(v["--body"], ground) >= 4.5, (p["id"], "body")
            assert c(v["--dim"], ground) >= 4.5, (p["id"], "dim")
            assert c(v["--accent-strong"], ground) >= 4.5, (p["id"], "accent-strong")
        assert c(v["--accent-ink"], p["accent"]) >= 4.5, (p["id"], "accent-ink")
        # 강조색 자체(버튼·막대)는 바탕에서 구분돼야 한다
        assert c(p["accent"], p["bg"]) >= 2.6, (p["id"], "accent")


def test_contrast_math_matches_wcag():
    assert round(appearance.contrast("#000000", "#ffffff"), 1) == 21.0
    assert appearance.contrast("#777777", "#777777") == 1.0
    assert appearance.mix("#000000", "#ffffff", 0.5) == "#808080"


def test_resolve_theme_overrides_but_keeps_manual_picks():
    look = appearance.resolve({"theme": "sepia", "mode": "dark", "bg": "stars", "accent": "blue"})
    assert look["theme"] == "sepia" and look["html_theme"] == "light" and look["bg_tone"] == "light"
    assert look["fx_bg"] == "theme" and look["accent_attr"] == "theme" and look["label"] == "세피아"
    assert "--bg:#F4ECD8" in look["style"] and "--accent:#B4543A" in look["style"]
    # 직접 고르기 값은 그대로 남아 테마를 끄면 돌아온다
    assert appearance.saved(look) == {"theme": "sepia", "mode": "dark", "bg": "stars", "accent": "blue"}
    off = appearance.resolve({"theme": "", "mode": "dark", "bg": "stars", "accent": "blue"})
    assert off["theme"] == "" and off["fx_bg"] == "stars" and off["style"] == "--accent:#5aa0ff"
    assert appearance.resolve({"theme": "없는-테마"})["theme"] == ""
    assert appearance.resolve({"theme": ["sepia"]})["theme"] == ""


async def test_theme_persists_through_settings_api(api):
    async with api(upstream) as c:
        await signup(c)
        await _set_look(c, theme="midnight")
        page = await c.get("/me")
        html = re.search(r"<html[^>]*>", page.text).group(0)
        assert 'data-preset="midnight"' in html and 'data-theme="dark"' in html
        assert "--bg:#0F1115" in html and 'data-accent="theme"' in html
        assert '<div class="fx" id="fx" data-bg="theme"' in page.text
        assert "<em data-look-label>심야 독서</em>" in page.text
        assert 'data-theme-pick="midnight"' in page.text
        # 직접 고르면(theme="") 테마가 꺼진다
        await _set_look(c, theme="", mode="light", bg="paper", accent="green")
        page = await c.get("/me")
        html = re.search(r"<html[^>]*>", page.text).group(0)
        assert "data-preset" not in html and 'data-theme="light"' in html
        # 모르는 테마는 조용히 무시
        await _set_look(c, theme="nope")
        page = await c.get("/me")
        assert "data-preset" not in re.search(r"<html[^>]*>", page.text).group(0)


async def test_theme_sheet_copy_is_renamed(api):
    async with api(upstream) as c:
        await signup(c)
        me = await c.get("/me")
    assert "화면 테마" in me.text and "화면 꾸미기" not in me.text
    assert "어떤 분위기로 읽을까요" in me.text and "직접 고르기" in me.text


def test_reader_settings_sheet_controls():
    page = (STATIC.parent / "templates" / "reader.html").read_text()
    for needle in ('id="readSet"', "읽기 설정", 'id="smaller"', 'id="bigger"', 'data-font="serif"', 'data-font="sans"',
                   'data-lh="loose"', 'data-pad="tight"', 'data-tone="sepia"', 'data-tone="gray"', 'data-tone="night"',
                   'id="wake"', "/static/css/reader.css"):  # fmt: skip
        assert needle in page, needle
    js = (STATIC / "reader.js").read_text()
    assert "SIZES = [15, 16, 17, 18, 20, 22, 24]" in js
    assert "'wakeLock' in navigator" in js and "--r-lh" in js and "--r-font" in js
    css = (STATIC / "css" / "reader.css").read_text()
    assert "var(--r-lh" in css and "var(--r-pad" in css and "prefers-reduced-motion" in css


def test_theme_change_crossfades_220ms():
    css = (STATIC / "css" / "themes.css").read_text()
    assert "html.theme-fade::view-transition-old(*)" in css and "220ms" in css and "prefers-reduced-motion" in css
    assert "startViewTransition" in (STATIC / "app.js").read_text()
