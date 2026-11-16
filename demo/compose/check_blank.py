"""빈 화면 검사 — 렌더된 모든 프레임에서 폰 화면이 비어 있지 않은지, 통째로 검은 프레임이 없는지 본다.

    uv run --with pillow --with numpy python demo/compose/check_blank.py

- render.mjs가 남긴 demo/out/frames.json에 프레임마다 무대에 보이는 폰 화면 사각형이 있다.
  그 영역의 평균 밝기(Rec.601 luma, 0–255)가 32 이하이면 실패(지난 영상의 빈 폰은 ~26, 채운 화면은 40 이상).
- 프레임 전체가 검으면(최대 밝기 < 24) 실패. 단 처음 0.4 s와 마지막 페이드 구간은 봐준다.
실패하면 프레임 번호를 늘어놓고 1로 끝난다.
"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "out"
SCREEN_MIN = 32.0
BLACK_MAX = 24
HEAD = 0.4  # 처음 0.4 s
TAIL = 0.35  # 마지막 화면 페이드(0.3 s) + 여유


def luma(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def main() -> int:
    meta = json.loads((OUT / "frames.json").read_text())
    fps, length = meta["fps"], meta["length"]
    frames = meta["frames"]
    blank, black = [], []
    with_phone = 0
    lows = []
    for fr in frames:
        t = (fr["f"] - 1) / fps
        y = luma(Image.open(OUT / "frames" / f"{fr['f']:05d}.jpg"))
        if y.max() < BLACK_MAX and HEAD <= t < length - TAIL:
            black.append(fr["f"])
        for ph in fr["phones"]:
            with_phone += 1
            region = y[ph["y"] : ph["y"] + ph["h"], ph["x"] : ph["x"] + ph["w"]]
            m = float(region.mean())
            lows.append((m, fr["f"], fr["shot"]))
            if m <= SCREEN_MIN:
                blank.append((fr["f"], round(t, 3), fr["shot"], round(m, 1), ph["src"]))
    lows.sort()
    print(f"frames: {len(frames)} ({len(frames) / fps:.2f} s @ {fps} fps), phone-screen checks: {with_phone}")
    print("darkest phone screens:", ", ".join(f"#{f} {s} {m:.1f}" for m, f, s in lows[:5]))
    by_shot: dict[str, list[float]] = {}
    for m, _, s in lows:
        by_shot.setdefault(s, []).append(m)
    ranked = sorted(by_shot.items(), key=lambda x: min(x[1]))
    print("min luma by shot:", ", ".join(f"{s} {min(v):.0f}" for s, v in ranked))
    if blank:
        print(f"FAIL blank phone screens (luma <= {SCREEN_MIN}): {len(blank)} frames")
        for row in blank:
            print("  frame", *row)
    if black:
        print(f"FAIL fully black frames outside head/tail: {black}")
    if blank or black:
        return 1
    print(f"OK — 0 blank phone screens (all > {SCREEN_MIN}),")
    print(f"     0 black frames outside first {HEAD} s / final fade")
    return 0


if __name__ == "__main__":
    sys.exit(main())
