# README 에셋 다시 만들기

README에 들어가는 이미지는 두 종류예요. 모두 `docs/images/` 아래에 있어요.

| 에셋 | 경로 | 만드는 법 |
|---|---|---|
| 소셜 배너 1200×630 | `docs/images/banner.png` | `scripts/readme_assets.sh banner` |
| 앱 스크린샷 390×844 (DPR 2) | `docs/images/app/*.jpg` · `docs/images/admin/*.jpg` | 아래 "스크린샷" 절 |

## 배너

```bash
uv pip install pillow --python .venv/bin/python      # 배너
scripts/readme_assets.sh
```

- 배너는 `scripts/readme_banner.py`가 Pillow로 그려요. 한글·한자 폰트는 macOS의 Apple SD Gothic Neo를 기본으로 찾고, 리눅스에서는 `FONT=/path/to/NotoSansCJK-Bold.ttc`로 알려 주세요. 색은 `app/static/app.css`의 `--accent`·`--glow`를 그대로 써요.

## 스크린샷

실제 앱을 띄우고 Playwright가 UI를 조작해 시드 데이터(책 1권, 대결방 1개, 참가자 3명, 반응 몇 개, 가입 대기 1명)를 만든 뒤 찍어요. 그래서 리더·대결·관리자 화면에 진짜 데이터가 보여요.

```bash
uv pip install playwright pillow --python .venv/bin/python
.venv/bin/playwright install chromium

# 1) 빈 SQLite로 앱 띄우기 — 체류시간 1%, 관리자 계정 지정
export DATABASE_URL="sqlite+aiosqlite:///$PWD/readme-shots.db"
DWELL_SCALE=0.01 ADMIN_EMAILS=admin@haedok.app SECRET_KEY=readme-shots \
  .venv/bin/uvicorn app.main:app --port 8077 &

# 2) 계정 세 개 심기 (지우=관리자 · 하린 · 민준, 비밀번호 password123)
.venv/bin/python scripts/readme_shots.py --seed "$DATABASE_URL"

# 3) 조작 + 촬영 (PNG가 폴더에 쌓여요). 책은 위키문헌에서 실제로 받아요 — 네트워크 필요
BASE=http://127.0.0.1:8077 .venv/bin/python scripts/readme_shots.py /tmp/readme-shots
```

스크립트가 하는 일: 지우가 로그인해 「운수 좋은 날」을 열고 대결을 만들어요 → 하린·민준이 초대 코드로 참가해 각자 몇 쪽 읽고 반응을 남겨요 → 서연이 가입 폼을 채워 6자리 인증 화면까지 가요 → 랜딩·가입·인증·홈·리더·대결·탐색·내 정보·관리자 4종을 찍어요.

PNG를 README에 넣을 때는 JPEG(품질 88부터 시작해 250 KB 이하가 될 때까지 낮춤)로 바꿔요. 파일 이름 대응은 아래와 같아요.

| PNG | JPG |
|---|---|
| `landing` · `signup` · `verify` | `app/0-landing` · `app/1-signup` · `app/1b-verify` |
| `home` · `explore` · `reader` · `race` · `look` | `app/2-home` · `app/3-explore` · `app/5-reader` · `app/7-race` · `app/8-look` |
| `admin-dashboard` · `admin-users` · `admin-rooms` · `admin-mail` | `admin/1-dashboard` · `admin/2-users` · `admin/3-rooms` · `admin/4-mail` |

```python
# python - <<'PY' ... PY 로 실행
from PIL import Image
import io, pathlib
src, dst = pathlib.Path("/tmp/readme-shots"), pathlib.Path("docs/images")
names = {"landing": "app/0-landing", "signup": "app/1-signup", "verify": "app/1b-verify", "home": "app/2-home",
         "explore": "app/3-explore", "reader": "app/5-reader", "race": "app/7-race", "look": "app/8-look",
         "admin-dashboard": "admin/1-dashboard", "admin-users": "admin/2-users",
         "admin-rooms": "admin/3-rooms", "admin-mail": "admin/4-mail"}
for a, b in names.items():
    im = Image.open(src / f"{a}.png").convert("RGB")
    for q in range(88, 50, -4):
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= 250_000:
            break
    (dst / f"{b}.jpg").write_bytes(buf.getvalue()); print(b, q, buf.tell() // 1024, "KB")
```

### 손으로 찍는 화면

- `app/1-login.jpg` — `/login`에서 이메일만 채운 상태(단계형 폼의 1단계).
- `app/4-store.jpg`, `app/6-react.jpg` — 서점 상세와 반응 시트. 디자인이 바뀔 때만 다시 찍어요.
- `app/9-pc-home.jpg`, `app/10-pc-reader.jpg`, `app/11-pc-store.jpg` — 1440×900 데스크톱 뷰포트.

### 규칙

- 휴대폰 화면은 390×844, `device_scale_factor=2`, `color_scheme=dark`, `locale=ko-KR`.
- 실제 사용자 데이터로 찍지 않아요. 계정·반응은 모두 스크립트가 만든 가짜예요.
- 파일 이름의 숫자는 README에서의 순서예요. 새 화면은 번호를 이어서 붙여 주세요.
