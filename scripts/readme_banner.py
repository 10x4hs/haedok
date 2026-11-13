"""README 소셜 배너(1200x630) — 偕讀 워드마크와 한 줄 소개를 Pillow로 그린다.

    python scripts/readme_banner.py docs/images/banner.png
    FONT=/path/to/NotoSansCJK-Bold.ttc python scripts/readme_banner.py docs/images/banner.png

폰트는 FONT 환경변수 → macOS Apple SD Gothic Neo → Noto Sans CJK 순으로 찾는다.
"""

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 630
BG = (10, 10, 10)
ACCENT = (255, 179, 199)  # app.css --accent
GLOW = (255, 140, 180)
INK = (245, 242, 240)
MUTED = (170, 166, 170)

CANDIDATES = [
    os.environ.get("FONT"),
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
]


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for path in CANDIDATES:
        if path and Path(path).exists():
            # Apple SD Gothic Neo .ttc: 0 Regular, 3 Bold, 5 Heavy
            index = (3 if bold else 0) if path.endswith("AppleSDGothicNeo.ttc") else 0
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                return ImageFont.truetype(path, size)
    raise SystemExit("한글·한자 폰트를 찾지 못했어요. FONT=/path/to/font.ttc 로 알려 주세요")


def main(out: Path) -> None:
    img = Image.new("RGB", (W, H), BG)

    # 은은한 분홍 글로우 두 개 (앱의 --glow)
    glow = Image.new("RGB", (W, H), BG)
    g = ImageDraw.Draw(glow)
    g.ellipse((-150, 120, 520, 760), fill=(70, 36, 50))
    g.ellipse((780, -260, 1380, 300), fill=(58, 30, 44))
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    img = Image.blend(img, glow, 1.0)

    # 글래스 카드
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    c = ImageDraw.Draw(card)
    c.rounded_rectangle(
        (80, 70, W - 80, H - 70), radius=36, fill=(28, 30, 36, 150), outline=(255, 255, 255, 28), width=2
    )
    img = Image.alpha_composite(img.convert("RGBA"), card)
    d = ImageDraw.Draw(img)

    # 워드마크
    mark = font(168)
    d.text((140, 128), "偕讀", font=mark, fill=INK)
    mw = d.textlength("偕讀", font=mark)
    d.text((140 + mw + 22, 232), "해독", font=font(44, bold=False), fill=MUTED)

    # 한 줄 소개
    d.text((146, 352), "같은 책을, 함께.", font=font(72), fill=ACCENT)
    sub = "친구와 같은 책을 각자의 속도로 읽고, 반응을 남기고, 페이지 레이스를 해요"
    d.text((148, 448), sub, font=font(28, bold=False), fill=MUTED)

    # 오른쪽 아래 작은 태그
    tag = font(24, bold=False)
    url = "haedok.vercel.app"
    d.text((W - 150 - d.textlength(url, font=tag), H - 118), url, font=tag, fill=(120, 116, 122))

    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "PNG", optimize=True)
    print(f"{out} {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/images/banner.png"))
