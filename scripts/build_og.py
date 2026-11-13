"""기본 OG 이미지(1200x630)를 만든다 — app/static/og/default.png 로 저장해 커밋한다.

실행: .venv/bin/python scripts/build_og.py  (Pillow가 필요하지만 런타임 의존성은 아니다)
글꼴: 저장소에 한글 글꼴이 없어 macOS 시스템의 Apple SD Gothic Neo를 쓴다.
다른 OS에서는 FONT 환경변수로 .ttf/.ttc 경로를 준다.
"""

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "og" / "default.png"
W, H = 1200, 630
CANDIDATES = (
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
)
FONT = os.environ.get("FONT") or next((p for p in CANDIDATES if Path(p).exists()), None)
if not FONT:
    raise SystemExit("한글 글꼴을 찾지 못했어요. FONT=경로 로 지정해 주세요")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # AppleSDGothicNeo.ttc: index 0 Regular … 4 Bold 근처. 없으면 Regular로
    try:
        return ImageFont.truetype(FONT, size, index=4 if bold else 0)
    except OSError:
        return ImageFont.truetype(FONT, size)


def main() -> None:
    # 어두운 유리 배경: 세로 그라데이션 + 강조색(핑크) 글로우 두 개
    img = Image.new("RGB", (W, H), "#0a0a0a")
    px = img.load()
    for y in range(H):
        t = y / H
        c = int(10 + 14 * t)
        for x in range(W):
            px[x, y] = (c, c, c + 4)
    glow = Image.new("RGB", (W, H), "#0a0a0a")
    g = ImageDraw.Draw(glow)
    g.ellipse((760, -160, 1360, 420), fill=(255, 179, 199))
    g.ellipse((-220, 330, 380, 820), fill=(120, 110, 220))
    glow = glow.filter(ImageFilter.GaussianBlur(170))
    img = Image.blend(img, glow, 0.42)

    d = ImageDraw.Draw(img)
    # 유리 카드 테두리
    d.rounded_rectangle((60, 60, W - 60, H - 60), radius=40, outline=(255, 255, 255, 40), width=2)
    # 워드마크 偕讀 + 해독
    d.text((110, 150), "偕讀", font=font(168, bold=True), fill="#ffffff")
    d.text((520, 236), "해독", font=font(60), fill="#ffb3c7")
    # 한국어 부제
    d.text((112, 370), "같은 책을 각자의 속도로 함께 읽어요", font=font(54, bold=True), fill="#f3f3f5")
    d.text((114, 450), "고전 · 전자책 · 친구와 페이지 레이스", font=font(32), fill=(170, 170, 180))
    d.text((W - 110 - 10, H - 110), "haedok", font=font(28), fill=(130, 130, 140), anchor="rs")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, optimize=True, compress_level=9)
    size = OUT.stat().st_size
    print(f"{OUT} {size / 1024:.0f} KB")
    if size > 300_000:
        img.convert("P", palette=Image.ADAPTIVE, colors=256).save(OUT, optimize=True)
        print(f"팔레트로 줄임 → {OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
