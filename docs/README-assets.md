# README 에셋 다시 만들기

README에 들어가는 이미지는 세 종류예요. 스크린샷은 손으로 찍지 않고 스크립트로 다시 만들 수 있어요.

| 에셋 | 경로 | 만드는 법 |
|---|---|---|
| 소셜 배너 1200×630 (영어 README) | `docs/images/banner.png` | `scripts/readme_assets.sh` |
| 앱 · 관리자 스크린샷 | `docs/images/app/*.jpg` · `docs/images/admin/*.jpg` | `scripts/readme_shots.sh` (아래) |
| 게시물 예시 사진(합성) | 촬영 때마다 `$OUT/photos` | `scripts/readme_photos.py` (아래) |
| 41초 소개 영상 · 포스터 | `app/static/demo/haedok-demo.mp4` · `haedok-demo-poster.jpg` | 랜딩에 쓰는 파일을 README가 링크해요 |

한국어 README 맨 위 그림은 GitHub에 올린 이미지(user-attachments)라 저장소 밖에 있어요.

## 배너

```bash
uv pip install pillow --python .venv/bin/python
scripts/readme_assets.sh
```

`scripts/readme_banner.py`가 Pillow로 그려요. 한글 · 한자 글꼴은 macOS의 Apple SD Gothic Neo를 기본으로 찾고, 리눅스에서는 `FONT=/path/to/NotoSansCJK-Bold.ttc`로 알려 주세요. 색은 `app/static/app.css`의 `--accent` · `--glow`를 그대로 써요.

## 스크린샷 — 한 줄로

```bash
uv run --no-project --with playwright playwright install chromium   # 처음 한 번
scripts/readme_shots.sh                                             # 몇 분 걸려요
```

- 필요한 것: 앱이 설치된 `.venv`, [uv](https://docs.astral.sh/uv/)(Playwright · Pillow를 임시로 받아 써요 — 앱 의존성에는 넣지 않아요), 네트워크(위키문헌 본문 · 책 표지).
- 이미 받아 둔 크로미움을 쓰려면 `CHROME=/path/to/chrome-headless-shell`, 포트는 `PORT=8077`(기본), 중간 PNG를 남길 폴더는 `OUT=/tmp/readme-shots`.
- 끝나면 `docs/images/app` · `docs/images/admin`의 JPG를 덮어써요. `git diff --stat docs/images`로 바뀐 것을 보고, 몇 장은 직접 열어 보세요.

### 스크립트가 하는 일

`scripts/readme_shots.sh`가 빈 SQLite로 앱을 두 번 띄워요(`DWELL_SCALE=0.01`, `ADMIN_EMAILS=admin@haedok.app`).

1. **이야기 세계** — `readme_shots.py --seed`로 계정 셋(지우=관리자 · 하린 · 민준, 비밀번호 `password123`)을 넣고, `readme_shots.py <폴더>`가 UI를 조작해요: 지우가 위키문헌에서 「운수 좋은 날」을 열고 대결을 만들어요 → 하린 · 민준이 초대 코드로 참가해 몇 쪽 읽고 반응을 남겨요 → 서연이 가입 폼을 채워 6자리 인증 화면까지 가요. 찍는 화면: 랜딩 · 로그인 · 가입 · 인증 · 리더 · 반응 시트 · 서점 · 관리자 4종 · PC 리더.
2. **시드 세계** — `readme_photos.py`로 게시물 사진 여섯 장을 그리고, 계정 셋을 넣고 `seed_board` → `seed_races` → `seed_chat` → `seed_goals` → `seed_mileage` → `seed_posts` → `seed_posts_ig` 순서로 심어요(네트워크 없이). 그러면 하린에게 친구 · 대결방 넷 · 모집 글 · 방/개인 채팅 · 17주 잔디 · 마일리지 · 사진 게시물(댓글 · 답글 · 저장 · 소개)이 생겨요. `readme_shots.py --world <폴더>`가 하린 시점으로 찍어요: 공개 페이지 3종 · 홈 · 내 정보 · 대결 목록 · 대결방 · 모집 · 친구 · 메시지 · 방 채팅 · 개인 채팅 · 마일리지 · 탐색 · 순위 · 소식 · 댓글 시트 · 프로필 · 게시물 · 모두(탐색) · 새 게시물 1단계 · 관리자 리워드 · PC 홈 · PC 방 채팅 · PC 대결방 · PC 게시물.
3. **JPEG** — `readme_shots.py --jpeg <폴더>`가 품질 88에서 시작해 250 KB 이하가 될 때까지 4씩 낮춰 저장해요.

### 파일 이름

숫자는 README에 나오는 순서예요. 새 화면은 `readme_shots.py`의 `JPEG_NAMES`에 이어서 붙여 주세요.

| PNG | JPG | 화면 |
|---|---|---|
| `landing` · `login` · `signup` · `verify` | `app/0-landing` · `app/1-login` · `app/1-signup` · `app/1b-verify` | 로그인 전 |
| `w-home` · `w-discover` · `store` | `app/2-home` · `app/3-explore` · `app/4-store` | 홈 · 탐색 · 서점(데미안) |
| `reader` · `react` · `w-race` · `w-me` | `app/5-reader` · `app/6-react` · `app/7-race` · `app/8-me` | 리더 · 반응 시트 · 대결방 · 내 정보(목표 · 잔디) |
| `pc-home` · `pc-reader` · `pc-chat` | `app/9-pc-home` · `app/10-pc-reader` · `app/11-pc-chat` | PC 1440×900 |
| `w-races` · `w-together` · `w-friends` | `app/12-races` · `app/13-together` · `app/14-friends` | 대결 목록 · 모집 · 친구 |
| `w-chat` · `w-chat-room` · `w-chat-dm` | `app/15-chat` · `app/16-chat-room` · `app/17-chat-dm` | 메시지 목록 · 방 채팅 · 개인 채팅 |
| `w-mileage` · `w-leaderboard` | `app/18-mileage` · `app/19-leaderboard` | 마일리지 · 순위 |
| `guide` · `docs` · `developers` | `app/20-guide` · `app/21-docs` · `app/22-developers` | 공개 페이지 |
| `pc-race` | `app/23-pc-race` | PC 대결방 |
| `w-feed` · `w-profile` · `w-post` · `w-comments` | `app/24-feed` · `app/25-profile` · `app/26-post` · `app/27-comments` | 친구 소식(사진 넘김 2/3) · 내 프로필 격자 · 게시물 · 댓글 시트(답글 펼침) |
| `w-create` · `w-explore-posts` · `pc-post` | `app/28-create` · `app/29-explore-posts` · `app/30-pc-post` | 새 게시물 1단계(사진 셋 고름) · 모두(탐색) 격자 · PC 게시물 |
| `admin-dashboard` · `admin-users` · `admin-rooms` · `admin-mail` · `admin-rewards` | `admin/1-dashboard` … `admin/5-rewards` | 관리자 |

게시물 사진 여섯 장(`desk` · `page` · `cafe` · `shelf` 4:5, `bed` · `park` 1:1)은 `scripts/readme_photos.py`가 앱의 표지 그림(`app/static/covers/showcase`)으로 책상 · 밑줄 그은 쪽 · 창가 · 책장 · 침대 · 벤치 장면을 HTML로 그려 만들어요 — 실제 사진이 아니에요. `seed_posts_ig.py`가 서버와 똑같이 검사하고 EXIF를 걷어 붙이고, 올리기 화면 촬영(`PHOTOS=<폴더>`)도 이 사진을 써요.

### 하나씩 돌리기

```bash
export DATABASE_URL="sqlite+aiosqlite:///$PWD/readme-shots.db"
DWELL_SCALE=0.01 ADMIN_EMAILS=admin@haedok.app SECRET_KEY=readme-shots \
  .venv/bin/uvicorn app.main:app --port 8077 &                       # 빈 DB면 표를 만들어요
.venv/bin/python scripts/readme_shots.py --seed "$DATABASE_URL"
for s in board races chat goals mileage posts; do .venv/bin/python scripts/seed_$s.py "$DATABASE_URL"; done
uv run --no-project --with playwright python scripts/readme_photos.py /tmp/photos
.venv/bin/python scripts/seed_posts_ig.py "$DATABASE_URL" /tmp/photos
PHOTOS=/tmp/photos BASE=http://127.0.0.1:8077 uv run --no-project --with playwright python scripts/readme_shots.py --world /tmp/shots
uv run --no-project --with pillow python scripts/readme_shots.py --jpeg /tmp/shots
```

이야기 세계(`readme_shots.py /tmp/shots`)는 시드 세계와 **다른 빈 DB**에서 돌려요 — `seed_board`가 「운수 좋은 날」을 가짜 본문으로 넣어서, 같은 DB면 리더에 진짜 본문이 안 나와요.

### 규칙

- 휴대폰 화면은 390×844, `device_scale_factor=2`(JPG는 780×1688), `color_scheme=dark`, `locale=ko-KR`. PC는 1440×900.
- 실제 사용자 데이터로 찍지 않아요. 계정 · 글 · 메시지는 모두 스크립트가 만든 예시예요(`@example.com`, `admin@haedok.app`).
- 표지 · 베스트셀러 그림은 서점 주소에서 그때그때 불러와서, 날마다 조금씩 달라질 수 있어요.
