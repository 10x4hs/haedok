"""Starboy 비트 그리드 — BPM, 다운비트, 프레이즈 시작, 드롭 세기를 재고 영상 창(window)을 고른다.

    uv run --with librosa --with numpy --with scipy python demo/music/analyze.py

입력: demo/music/starboy.mp3 (저작권 음원이라 저장소에 넣지 않는다 — README 참고)
출력: demo/music/beats.json (커밋), demo/music/track.wav (창만 잘라 페이드 넣은 것, 커밋하지 않음)

창 고르는 규칙
1. 길이 32–41초, 시작은 프레이즈(4마디) 시작 다운비트.
2. 레이스 절정(역전 프리즈)은 이야기의 70–85% 지점에 와야 한다. 그 구간에 들어오는 프레이즈 경계 가운데
   드롭 점수(직후 2마디와 직전 2마디의 중·고역 에너지 차 + 음색 변화)가 가장 큰 곳을 절정으로 삼는다.
3. 위 조건을 만족하는 (시작, 절정) 쌍 가운데 절정 드롭 점수가 가장 크고, 길이가 넉넉한(>=38초) 것을 고른다.
"""

import json
import subprocess
from pathlib import Path

import librosa
import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE / "starboy.mp3"
SR = 22050
HOP = 256
FPS = 30
MIN_LEN, MAX_LEN = 32.0, 41.0
CLIMAX_ZONE = (0.70, 0.85)
FADE_IN, FADE_OUT = 0.12, 0.9


def main() -> None:
    y, sr = librosa.load(SRC, sr=SR, mono=True)
    dur = len(y) / sr
    _, frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=HOP, start_bpm=186, tightness=400)
    bt = librosa.frames_to_time(frames, sr=sr, hop_length=HOP)
    # 박 간격을 직선 회귀로 다듬어 흔들림 없는 그리드로 만든다
    idx = np.arange(len(bt))
    period, offset = np.polyfit(idx, bt, 1)
    bpm = 60.0 / period

    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    f = librosa.fft_frequencies(sr=sr, n_fft=2048)
    t = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=HOP)
    low = S[f < 150].sum(0)
    high = S[f > 2000].sum(0)
    kick = np.maximum(0, np.diff(low, prepend=low[0]))
    snare = np.maximum(0, np.diff(high, prepend=high[0]))

    def at(x, tt):
        i = np.searchsorted(t, tt)
        return float(x[max(0, i - 2) : i + 3].max()) if i < len(x) else 0.0

    grid = offset + period * np.arange(-int(offset / period), int((dur - offset) / period) + 1)
    grid = grid[(grid >= 0) & (grid < dur)]
    k_on = np.array([at(kick, g) for g in grid])
    s_on = np.array([at(snare, g) for g in grid])
    # 하프타임(93 BPM) 한 마디 = 8박. 스네어가 2·4박(우리 박으로 2·6번째)에, 킥이 1박에 온다 →
    # 8가지 위상 가운데 '킥 - 스네어'가 가장 큰 곳이 다운비트.
    scores = []
    for ph in range(8):
        backbeat = s_on[(ph + 2) % 8 :: 8].mean() + s_on[(ph + 6) % 8 :: 8].mean()
        d = k_on[ph::8].mean() - s_on[ph::8].mean() + backbeat
        scores.append(d)
    phase = int(np.argmax(scores))
    downbeats = grid[phase::8]

    # 마디별 에너지(중·고역)와 음색(MFCC) — 드롭 점수
    mid = S[(f >= 250) & (f < 8000)]
    mf = librosa.util.normalize(librosa.feature.mfcc(S=librosa.power_to_db(S**2), sr=sr, n_mfcc=13), axis=1)
    bar = period * 8

    def seg(a, b):
        m = (t >= a) & (t < b)
        return 20 * np.log10(mid[:, m].mean() + 1e-9), mf[:, m].mean(1)

    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    loud = float(np.percentile(rms, 75))

    def is_loud(a, b):
        return rms[np.searchsorted(t, a) : np.searchsorted(t, b)].mean() >= 0.6 * loud

    drops = []
    for d in downbeats:
        # 앞 2마디가 페이드인이면 '드롭'이 아니라 음악이 켜지는 것뿐이라 뺀다
        if d - 2 * bar < 0 or d + 2 * bar > dur or not is_loud(d - 2 * bar, d - bar):
            drops.append(0.0)
            continue
        e0, m0 = seg(d - 2 * bar, d)
        e1, m1 = seg(d, d + 2 * bar)
        timbre = 1 - float(np.dot(m0, m1) / (np.linalg.norm(m0) * np.linalg.norm(m1)))
        drops.append(round(max(0.0, e1 - e0) + 40 * timbre, 3))
    drops = np.array(drops)

    # 프레이즈 = 4마디. 위상은 드롭 점수 합이 가장 큰 쪽
    ph4 = int(np.argmax([drops[p::4].sum() for p in range(4)]))
    phrase_idx = list(range(ph4, len(downbeats), 4))
    phrases = downbeats[phrase_idx]

    # 음악이 실제로 들어오는 첫 프레이즈(페이드인 꼬리 이후)부터 시작 후보
    candidates = []
    for s in [p for p in phrases if p > 0]:
        si = np.searchsorted(t, s)
        if rms[si : si + 20].mean() < 0.6 * loud:
            continue
        for di, d in enumerate(downbeats):
            if di not in phrase_idx or d <= s:
                continue
            for length in np.arange(MAX_LEN, MIN_LEN - 0.01, -0.1):
                if s + length > dur - 0.05:
                    continue
                pos = (d - s) / length
                if CLIMAX_ZONE[0] <= pos <= CLIMAX_ZONE[1]:
                    candidates.append((float(drops[di]), float(min(length, 41.0)), float(s), float(d)))
                    break
    if not candidates:
        raise SystemExit("no window fits")
    # 절정 드롭이 가장 센 것 → 같으면 더 긴 것
    candidates.sort(key=lambda c: (round(c[0], 1), c[1]), reverse=True)
    drop, length, start, climax = candidates[0]
    length = round(min(length, dur - start - 0.05), 2)

    strongest = float(downbeats[int(np.argmax(drops))])
    out = {
        "source": "demo/music/starboy.mp3",
        "source_duration": round(dur, 3),
        "bpm": round(bpm, 3),
        "beat": round(period, 5),
        "bar": round(bar, 5),
        "window": {"start": round(start, 3), "length": length, "fade_in": FADE_IN, "fade_out": FADE_OUT},
        "climax": {"audio": round(climax, 3), "video": round(climax - start, 3), "drop": round(drop, 3)},
        "strongest_drop": {
            "audio": round(strongest, 3),
            "video": round(strongest - start, 3),
            "drop": round(float(drops.max()), 3),
        },
        "note": (
            "starboy.mp3 is a 45 s excerpt. Its strongest drop sits near the start, too early for the race "
            "climax; the window takes the strongest phrase boundary inside the 70-85% story zone instead "
            "and puts the other big drop on the first chapter change."
        ),
        # 영상 시간축(0 = 창 시작)으로 옮긴 그리드
        "beats": [round(float(b) - start, 4) for b in grid if start <= b < start + length],
        "downbeats": [round(float(b) - start, 4) for b in downbeats if start <= b < start + length],
        "phrases": [round(float(b) - start, 4) for b in phrases if start <= b < start + length],
        "drops": {
            str(round(float(d) - start, 3)): float(s)
            for d, s in zip(downbeats, drops, strict=True)
            if start <= d < start + length and s > 0
        },
    }
    (HERE / "beats.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{length:.3f}",
            "-i",
            str(SRC),
            "-af",
            f"afade=t=in:d={FADE_IN},afade=t=out:st={length - FADE_OUT:.3f}:d={FADE_OUT}",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(HERE / "track.wav"),
        ],
        check=True,
    )
    print(json.dumps({k: out[k] for k in ("bpm", "window", "climax", "strongest_drop")}, ensure_ascii=False))
    print("downbeats", out["downbeats"])
    print("drops", out["drops"])
    print("candidates", [tuple(round(x, 2) for x in c) for c in candidates[:6]])


if __name__ == "__main__":
    main()
