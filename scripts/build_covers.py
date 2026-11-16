"""홈 띠에 쓰는 표지를 직접 그린다 — app/data/showcase.json의 책마다 600x900 webp (각 60KB 이하).

출판사 표지는 저작물이라 쓰지 않는다. 제목·저자를 Noto Serif KR로 조판하고, 책마다 고른 색과
옅은 종이 결만 얹은 타이포그래피 표지다. 글꼴(SIL OFL)은 저장소에 넣지 않고 FONT_DIR에서 읽는다.

준비: Noto Serif KR의 NotoSerifKR-Bold.otf / NotoSerifKR-Regular.otf를 받아 한 폴더에 둔다
      (https://github.com/notofonts/noto-cjk 의 Serif/SubsetOTF/KR).
실행: FONT_DIR=<그 폴더> uv run --no-project --with pillow python scripts/build_covers.py
"""

import io
import json
import os
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "app" / "data" / "showcase.json"
FONT_DIR = Path(os.environ.get("FONT_DIR", Path.home() / ".cache" / "haedok-fonts"))
W, H = 600, 900
PAD = 64
MAX_BYTES = 60_000

# 책마다 (바탕, 글자, 장식) — 어둡고 차분한 색, 밝은 화면에서도 무겁지 않게 채도를 낮췄다
PALETTE = {
    "seosi": ("#1c2740", "#efe6cf", "#d8b75e", "stars"),
    "lucky-day": ("#3a3f45", "#ece6da", "#9fb3c8", "rain"),
    "buckwheat": ("#e9e4d6", "#2f3a2c", "#8a9a7b", "moon"),
    "wings": ("#efece6", "#1f1f22", "#b24a3c", "rule"),
    "camellia": ("#7b2a2c", "#f5e9de", "#e7b7a4", "circle"),
    "spring": ("#cfdcc4", "#26331f", "#6f8a5a", "rule"),
    "potato": ("#5a4636", "#f1e6d4", "#c9a77c", "circle"),
    "ready-made": ("#20262c", "#e7e2d8", "#c7a14a", "rule"),
    "b-sagam": ("#efe1e1", "#3b2228", "#c06a7a", "rule"),
    "samryong": ("#2d3a33", "#eae3d3", "#c26a3a", "moon"),
    "hong-gildong": ("#e6dcc5", "#2a2219", "#9a3b2a", "seal"),
    "gatsby": ("#0f2a33", "#f1e7cf", "#d4af5a", "rule"),
    "metamorphosis": ("#4a4a3e", "#ece8dc", "#a7a37c", "circle"),
    "pride": ("#ead9d3", "#3a2a2c", "#a46a6e", "rule"),
    "sherlock": ("#2a2420", "#eee3cf", "#b78b4a", "circle"),
    "alice": ("#dfe7ef", "#1f2c3d", "#5c7da6", "dots"),
    "crime": ("#3b1d1f", "#efe2d6", "#b8584e", "rule"),
    "frankenstein": ("#24302c", "#e6eadf", "#8fb19a", "rain"),
    "siddhartha": ("#e8d9b8", "#3a2b15", "#b5772f", "moon"),
    "dorian": ("#2c2236", "#ece4ef", "#b79ac9", "circle"),
}


def rgb(hex_: str) -> tuple[int, int, int]:
    return tuple(int(hex_[i : i + 2], 16) for i in (1, 3, 5))


def mix(a: tuple, b: tuple, t: float) -> tuple[int, int, int]:
    return tuple(int(x * t + y * (1 - t)) for x, y in zip(a, b, strict=True))


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"NotoSerifKR-{weight}.otf"), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """띄어쓰기 단위로 줄을 나눈다. 한 낱말이 넓이를 넘으면 글자 단위로 자른다."""
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=f) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = word
        while draw.textlength(cur, font=f) > width:
            cut = len(cur)
            while cut > 1 and draw.textlength(cur[:cut], font=f) > width:
                cut -= 1
            lines.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        lines.append(cur)
    return lines


# 뜻이 끊기지 않게 직접 정한 줄바꿈 ("운수 좋은 / 날" 대신 "운수 / 좋은 날")
BREAKS = {
    "lucky-day": ["운수", "좋은 날"],
    "buckwheat": ["메밀꽃", "필 무렵"],
    "ready-made": ["레디메이드", "인생"],
    "samryong": ["벙어리", "삼룡이"],
    "pride": ["오만과", "편견"],
    "sherlock": ["셜록 홈즈의", "모험"],
}


def fit_title(draw: ImageDraw.ImageDraw, title: str, slug: str) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    for size in range(92, 40, -2):
        f = font("Bold", size)
        if slug in BREAKS:
            lines = BREAKS[slug]
            if all(draw.textlength(ln, font=f) <= W - PAD * 2 for ln in lines):
                return f, lines
            continue
        lines = wrap(draw, title, f, W - PAD * 2)
        if len(lines) <= 3:
            return f, lines
    return f, lines


def paper(bg: str, seed: int) -> Image.Image:
    """바탕색 + 아주 옅은 종이 결(잡음) + 위아래 은은한 그늘."""
    base = Image.new("RGB", (W, H), rgb(bg))
    rnd = random.Random(seed)
    noise = Image.new("L", (W // 2, H // 2))
    noise.putdata([rnd.randint(118, 138) for _ in range(noise.width * noise.height)])
    noise = noise.resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(0.6))
    grain = Image.merge("RGB", (noise, noise, noise))
    base = Image.blend(base, Image.composite(grain, base, Image.new("L", (W, H), 255)), 0.05)
    shade = Image.linear_gradient("L").resize((W, H))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(dark, base, shade.point(lambda v: int(v * 0.10)))


def motif(draw: ImageDraw.ImageDraw, kind: str, accent: tuple, y: int) -> None:
    if kind == "rule":
        draw.line([(PAD, y), (PAD + 72, y)], fill=accent, width=3)
    elif kind == "circle":
        draw.ellipse([PAD, y - 34, PAD + 68, y + 34], outline=accent, width=3)
    elif kind == "moon":
        draw.ellipse([PAD, y - 34, PAD + 68, y + 34], fill=accent)
    elif kind == "seal":  # 붉은 낙관
        draw.rounded_rectangle([PAD, y - 30, PAD + 60, y + 30], radius=6, fill=accent)
        draw.text((PAD + 30, y), "傳", font=font("Bold", 34), fill=rgb("#f4ead6"), anchor="mm")
    elif kind == "stars":
        rnd = random.Random(7)
        for _ in range(9):
            x, yy = rnd.randint(PAD, W - PAD), rnd.randint(y - 60, y + 40)
            r = rnd.choice((2, 2, 3, 4))
            draw.ellipse([x - r, yy - r, x + r, yy + r], fill=accent)
    elif kind == "rain":
        rnd = random.Random(3)
        for _ in range(26):
            x, yy = rnd.randint(PAD, W - PAD), rnd.randint(y - 70, y + 50)
            draw.line([(x, yy), (x - 6, yy + 22)], fill=accent, width=2)
    elif kind == "dots":
        for i in range(5):
            r = 10 - i
            x = PAD + i * 34
            draw.ellipse([x - r + 10, y - r, x + r + 10, y + r], fill=accent)


def render(book: dict, slug: str) -> bytes:
    bg, ink_hex, accent_hex, kind = PALETTE[slug]
    ink, accent = rgb(ink_hex), rgb(accent_hex)
    img = paper(bg, sum(map(ord, slug)))
    d = ImageDraw.Draw(img)
    soft = mix(ink, rgb(bg), 0.72)
    d.rectangle([24, 24, W - 25, H - 25], outline=mix(ink, rgb(bg), 0.3), width=1)  # 안쪽 테두리

    eyebrow = "한국 문학" if book["group"] == "ko" else "세계 고전"
    if book.get("year"):
        eyebrow += f"  ·  {book['year']}"
    d.text((PAD, 86), eyebrow, font=font("Regular", 22), fill=soft)

    f, lines = fit_title(d, book["title"], slug)
    y = 168
    for line in lines:
        d.text((PAD, y), line, font=f, fill=ink)
        y += int(f.size * 1.22)
    if book.get("original"):
        of = font("Regular", 24)
        for line in wrap(d, book["original"], of, W - PAD * 2)[:2]:
            d.text((PAD, y + 14), line, font=of, fill=soft)
            y += 34

    motif(d, kind, accent, max(y + 110, 560))

    d.text((PAD, H - 150), book["author"], font=font("Regular", 32), fill=ink)
    d.text((W - PAD, H - 80), "偕讀", font=font("Regular", 20), fill=soft, anchor="rs")

    for q in (82, 76, 70, 62, 54):
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=q, method=6)
        if buf.tell() <= MAX_BYTES:
            return buf.getvalue()
    return buf.getvalue()


def main() -> None:
    books = json.loads(DATA.read_text())
    for b in books:
        slug = Path(b["cover"]).stem
        out = ROOT / "app" / b["cover"].removeprefix("/")
        out.parent.mkdir(parents=True, exist_ok=True)
        data = render(b, slug)
        out.write_bytes(data)
        print(f"{slug:16} {len(data) // 1024:3} KB")


if __name__ == "__main__":
    main()
