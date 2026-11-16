"""접촉 인화지(contact sheet) — 장면마다 한 장, 그리고 2초마다 한 장(dense).

    uv run --with pillow python demo/compose/contact.py

입력: demo/out/frames/*.jpg, demo/out/frames.json  →  demo/out/contact.jpg, demo/out/contact-dense.jpg
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "out"
TW, TH = 216, 384  # 1080x1920의 1/5


def font(size: int):
    for cand in ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Monaco.ttf"):
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            continue
    return ImageFont.load_default()


def sheet(items: list[tuple[int, str]], cols: int, path: Path, fps: int) -> None:
    rows = (len(items) + cols - 1) // cols
    pad, label = 8, 22
    im = Image.new("RGB", (cols * (TW + pad) + pad, rows * (TH + label + pad) + pad), (14, 12, 15))
    d = ImageDraw.Draw(im)
    f = font(14)
    for i, (frame, name) in enumerate(items):
        x = pad + (i % cols) * (TW + pad)
        y = pad + (i // cols) * (TH + label + pad)
        th = Image.open(OUT / "frames" / f"{frame:05d}.jpg").resize((TW, TH), Image.LANCZOS)
        im.paste(th, (x, y + label))
        d.text((x + 2, y + 3), f"{(frame - 1) / fps:5.2f}s {name}", fill=(255, 158, 196), font=f)
    im.save(path, quality=88)
    print(path, im.size, len(items), "frames")


def main() -> None:
    meta = json.loads((OUT / "frames.json").read_text())
    fps = meta["fps"]
    frames = meta["frames"]
    shot_of = {fr["f"]: fr["shot"] or "-" for fr in frames}
    # 장면마다 가운데 프레임
    per_scene: dict[str, list[int]] = {}
    for fr in frames:
        per_scene.setdefault(fr["shot"] or "-", []).append(fr["f"])
    scenes = sorted(((v[len(v) // 2], k) for k, v in per_scene.items()))
    sheet(scenes, 7, OUT / "contact.jpg", fps)
    dense = [(f, shot_of[f]) for f in range(1, len(frames) + 1, 2 * fps)]
    sheet(dense, 10, OUT / "contact-dense.jpg", fps)


if __name__ == "__main__":
    main()
