# 해독 데모 영상 파이프라인

랜딩의 “40초 영상으로 보기”에 들어가는 9:16 시연 영상(1080x1920, 30 fps, 39.5 s)을 만드는 곳이에요.
예전 렌더 프로젝트가 저장소 밖에 있다가 통째로 사라져서, 이번에는 캡처·합성·인코딩을 모두 여기에 둬요.

```
demo/
  music/    analyze.py → beats.json(커밋) · track.wav(무시)   음원 starboy.mp3는 커밋하지 않아요
  capture/  capture.py · demian.txt                            앱을 새 DB로 띄워 스틸·클립을 찍어요
  shots/    *.jpg (780x1688) · boxes.json                      스틸, 요소 위치(합성 초점)
  clips/    *.webm (30fps VP9) · *.json                        짧은 동작 클립 + 순간 표시(marks/events)
  compose/  index.html · render.mjs · encode.sh · check_blank.py · contact.py · fonts/
  out/      frames/ · frames.json · haedok-demo.mp4 · haedok-demo-web720.mp4 · 포스터 · 접촉 인화지 (무시)
```

## 음원

- 곡: The Weeknd — *Starboy* (첫 데모에 쓴 곡). 저작권 음원이라 저장소에 넣지 않아요 (`demo/music/*.mp3`, `*.wav`는 `.gitignore`).
- 원본 위치: `/Users/prime/Desktop/인프/Starboy.mp3` (45 s로 잘라 둔 발췌본, 186 BPM 하프타임) → `demo/music/starboy.mp3`로 복사해서 써요.

## 처음부터 다시 만들기

```sh
# 0) 준비 (저장소 루트)
uv venv .venv && uv pip install -e ".[dev]" --python .venv/bin/python
uv pip install playwright pillow --python .venv/bin/python      # 캡처용
(cd demo/compose && npm install)                                  # playwright-core
cp "/Users/prime/Desktop/인프/Starboy.mp3" demo/music/starboy.mp3

# 1) 박자 그리드 + 영상 창 → demo/music/beats.json, demo/music/track.wav
uv run --with librosa --with numpy --with scipy python demo/music/analyze.py

# 2) 화면 캡처 (앱을 포트 8078에 새 SQLite로 띄우고 끝나면 내려요. 위키문헌에서 책을 받으니 네트워크 필요)
.venv/bin/python -u demo/capture/capture.py

# 3) 프레임 렌더 → demo/out/frames/ (약 1185장)
node demo/compose/render.mjs
node demo/compose/render.mjs --at 1.3,10.3,31.0      # 몇 장만 미리보기 → demo/out/preview/

# 4) 빈 화면 검사 (반드시 0건)
uv run --with pillow --with numpy python demo/compose/check_blank.py

# 5) 인코딩 → 마스터 · 웹용(≤ 6 MB) · 포스터 · 접촉 인화지
bash demo/compose/encode.sh

# 6) 사이트에 반영
cp demo/out/haedok-demo-web720.mp4 app/static/demo/haedok-demo.mp4
cp demo/out/haedok-demo-poster.jpg app/static/demo/haedok-demo-poster.jpg
```

렌더러는 `~/Library/Caches/ms-playwright/chromium_headless_shell-1254/…/chrome-headless-shell`을 써요.
다른 경로면 `CHROME_SHELL=/path/to/chrome-headless-shell node demo/compose/render.mjs`.

## 박자와 창 (beats.json)

- 185.96 BPM(하프타임 한 마디 = 8박 = 2.581 s). 창은 오디오 5.438 s부터 39.5 s — 페이드인이 끝나고 박이 들어오는 첫 프레이즈예요.
- 이 발췌본에서 가장 센 드롭은 15.76 s(영상 10.325 s)라 레이스 절정을 두기엔 너무 일러요.
  그래서 `analyze.py`는 이야기의 70–85 % 구간에 드는 프레이즈 경계 중 가장 센 곳(영상 30.974 s = 96박)을 역전 프리즈로 잡고,
  가장 센 드롭은 1장(내 책) → 2장(함께)으로 넘어가는 휩팬에 써요.

## 장면 (박 = 0.32265 s)

| 박 | 초 | 장면 | 캡션 |
|---|---|---|---|
| 0–8 | 0.00–2.58 | 훅: 친구가 같은 쪽에 들어오는 순간(펄스) | 친구가 지금 같은 쪽 |
| 8–16 | 2.58–5.16 | 서점 → 구매 카드(펄스) | 읽고 싶은 책을 사고 |
| 16–24 | 5.16–7.74 | PDF 고르기 → 올리기(박마다 진행) | 내 PDF 그대로 |
| 24–32 | 7.74–10.33 | 서재 → 쪽 넘김(박에 맞춤) | 편하게 읽어요 |
| 32 | 10.33 | **휩팬** (가장 센 드롭) | |
| 32–40 | 10.33–12.91 | 초대 코드 → 친구가 한 박에 한 글자씩 입력 | 코드 하나로 초대 |
| 40–48 | 12.91–15.49 | 참가 화면 → 대결 화면에 친구 등장(펄스) | 친구가 들어왔어요 |
| 48–56 | 15.49–18.07 | 같은 쪽 표시 → 친구 칩으로 밀어 들어가기 | 지금 같은 페이지 |
| 56–66 | 18.07–21.29 | 문장 고르기 → 대결방에 공유 토스트 | 밑줄 친 문장 공유 |
| 66–85.5 | 21.29–27.59 | 레이스 화면 → 한 걸음씩(박에 맞춤) | 각자의 속도로 경주 / 한 쪽씩 따라잡고 |
| 85.5–96 | 27.59–30.97 | 가속 컷 1.94 → 0.97 → 0.48 s | 조금만 더 |
| 96–102 | 30.97–32.91 | **역전** 프리즈 + 3 % 펀치(펄스) | 역전! |
| 102 | 32.91 | **휩팬** | |
| 102–112 | 32.91–36.14 | 화면 꾸미기 3종 → 랜딩 | 내 방식대로 읽기 |
| 112– | 36.14–39.50 | 로고 · 같은 책을, 함께. · haedok.vercel.app (펄스, 3.4 s 유지) | |

심장박동(3.5 % 한 번, 0.3 s)은 훅 · 구매 확인 · 친구 참가 · 역전 · 로고 다섯 번뿐이에요.

## 규칙 (예전 스타일 가이드에서)

- 훅 먼저(1–3 s 안에 제품 동작), 로고는 끝에만. 캡션은 2–5단어, 2–4 s 고정, 그 아래에서 UI가 바뀌어요.
- 폰은 크게(무대 높이의 88 %), 디테일은 작은 폰을 띄우지 말고 폰 안으로 밀어 들어가서 보여 줘요.
- 다운비트에 컷, UI 동작(코드 입력·업로드 진행·쪽 넘김)은 박에. 크로스페이드 없음.
- 빈 화면 금지: `check_blank.py`가 모든 프레임에서 폰 화면 평균 밝기 > 32를 확인해요.
  쪽 넘김 클립의 둘째 넘김은 장 경계라 0.4 s 동안 빈 ‘다음 쪽으로’ 화면이 찍혀 있어서, 합성에서 그 구간을 건너뛰고 박에 맞춰 새 쪽으로 바로 끊어요.

## 데미안 본문

업로드 시연용 PDF는 캡처 때 `demo/capture/demian.txt`로 만들어요. 헤세의 독일어 원문(1919, 퍼블릭 도메인)을
이 저장소에서 직접 옮긴 문장이고, 기존 한국어 번역본은 쓰지 않았어요.
