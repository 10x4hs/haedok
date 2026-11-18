# 아키텍처

지금 모습을 먼저 적고, 아래로 갈수록 그 모듈이 생긴 순서대로 결정 기록이 이어져요. 처음 MVP 기획은 [PRD.md](PRD.md)에 있어요.

## 지금 구조 (2026-11)

요청은 네 층을 위에서 아래로 지나가요. 규칙은 셋째 층(서비스)에만 둬요.

| 층 | 어디 | 하는 일 |
|---|---|---|
| 1 화면 | `app/templates/` · `app/static/` | Jinja2 서버 렌더링 + 바닐라 JS(빌드 없음). 기능별 CSS/JS는 따로 파일(`chat.css`·`chat.js`, `posts.js`, `goals.js`, `board.css` …) |
| 2 라우트 | `app/web.py` · `app/*.py` 라우터 · `app/api/` | 주소마다 함수 하나. 로그인·CSRF·기능 스위치를 확인하고 서비스에 넘긴다 |
| 3 서비스 | `app/services/` | 15초 판정, 스포일러 가드, 대결, 마일리지, 목표, 모집, 채팅, 게시물, 메일 |
| 4 DB | `app/models.py` + `app/models_*.py` · `migrations/` | SQLAlchemy 2(async). 배포는 Postgres(Neon), 로컬·테스트는 SQLite |

```
app/
  main.py              앱 · lifespan(공용 httpx 클라이언트, SQLite면 create_all) · 미들웨어(CSP nonce, 세션, 요청 로그)
                       · 오류 화면 · 라우터 등록 · /health
  core/                config.py(설정·기능 스위치) · csp.py · logging.py · ratelimit.py(DB 고정 창)
                       · appearance.py(테마·배경·강조 색)
  db/                  DeclarativeBase(제약 이름 규칙) · async 세션
  models.py            기본 스키마(사용자·가입 인증·비밀번호 재설정·메일 기록·책·청크·서재·읽기 기록·방·presence·문장 공유·반응·포인트 원장…)
  models_goals.py      reading_goals · reading_day_marks
  models_board.py      recruit_posts · recruit_reports
  models_mileage.py    mileage_spends · reward_items · reward_redemptions
  models_chat.py       friendships · chat_threads · chat_messages · chat_reactions · chat_reads · chat_reports
  models_posts.py      reading_posts · post_likes · post_comments(답글 parent_id) · post_reports · post_photos
                       · post_saves · post_comment_likes · post_comment_reports · profile_bios
  web.py               화면: 랜딩 · 로그인/가입/인증/비밀번호 · 홈 · 탐색/서가/검색 · 서점 · 리더 · 서재 · 올리기
                       · 대결 목록/방/초대 · 순위 · 내 정보
  auth.py              세션 · BCrypt · CSRF · 6자리 코드(HMAC 지문) · 비밀번호 재설정
  intro.py             공개 페이지 /guide · /docs · /developers (+ /intro → /guide 308)
  public_facts.py      공개 페이지의 숫자를 코드 상수에서 읽는다(15초, 마일리지 표, 업로드 한도, 테스트·마이그레이션 수)
  board.py             /together (모집 글 목록·쓰기·참가·나가기·닫기·신고) · /admin/together
  chat.py              /friends · /chat · /chat/dm/{id} · /chat/room/{id} · /admin/chat-reports
                       · 메뉴의 안 읽음 점(ChatMenuDot 미들웨어)
  mileage.py           /mileage (보호권·리워드 교환·요청 거두기) · /admin/rewards
  posts.py             /feed(친구 · 모두) · /posts/new · /p/{id} · /u/{id} · /profile(+ /profile/edit) · /media/p/{id}/{n}.jpg
                       · /admin/posts
  admin.py             /admin (현황·사용자·대결방·메일) — 기능별 관리 화면은 각 라우터에
  seo.py · pwa.py      robots·sitemap·OG / manifest·service worker·assetlinks
  realtime.py          책 단위 인메모리 브로커(로컬 WebSocket 전용)
  api/                 books · me · goals · points · social(반응·친구 위치·presence·대결 순위·WS) · chat
  services/            pace · books · social · races · race_list · points · goals · board · mileage · chat · posts
                       · post_photos · mail · uploads · search · chunker · bookstore · bestsellers · palette
  sources/             외부 도서 소스 어댑터 (어댑터 패턴)
    base.py            BookSource(ABC) · BookMeta · SourceError
    kakao/naver/google_books.py   메타데이터 전용
    gutendex/wikisource.py        퍼블릭 도메인 전문 (fetch_book 구현)
    registry.py        키가 있는 소스만 우선순위 순서로 활성화
  data/                catalog.json(고전) · store.json(서점) · bestsellers.json(매일 갱신) · popular·home_picks …
api/index.py           Vercel 진입점
migrations/            Alembic (async, 18개 — README의 표 참고)
```

### 기능 스위치

v3 기능은 `app/core/config.py`의 설정 하나로 끈다. 끄면 메뉴·링크가 숨고 주소는 404.

| 설정(환경 변수) | 기능 |
|---|---|
| `feature_goals` (`FEATURE_GOALS`) | 하루 목표·잔디·연속 — `/api/v1/me/goal`, 홈·내 정보의 목표 칸 |
| `feature_board` (`FEATURE_BOARD`) | 같이 읽을 사람 모집 `/together` |
| `feature_chat` (`FEATURE_CHAT`) | 친구·채팅 `/friends` · `/chat` |
| `feature_mileage` (`FEATURE_MILEAGE`) | 마일리지 상점 `/mileage` · `/admin/rewards` (적립 자체는 계속된다) |
| `feature_posts` (`FEATURE_POSTS`) | 게시물 `/feed` · `/posts/new` · `/p/{id}` · `/u/{id}` |

## 핵심 결정

| 결정 | 이유 |
|---|---|
| **어댑터 패턴** — 소스마다 `search`, 전문 소스만 `fetch_book` | 출판사 제휴 시 파일 하나 추가로 확장 |
| **전문 화이트리스트를 코드로 강제** (`FULLTEXT_SOURCES`) | 저작권 원칙이 실수로 깨지지 않도록. 메타데이터 소스가 `has_fulltext=True`를 줘도 병합 단계에서 꺼짐 |
| **부분 실패 허용** — `asyncio.gather(return_exceptions=True)` + 소스별 `wait_for` | 한 API가 죽어도 검색은 동작 |
| **오류는 타입명만 노출** | httpx 예외 메시지에 API 키가 담긴 URL이 포함될 수 있음 |
| **청크 = 위치 단위** | 판본·기기마다 달라지는 페이지 대신 결정적 분할 결과를 공유 좌표로 사용 |
| **포인트는 append-only 원장** | 감사 추적, 잔액 = SUM |
| **테스트는 네트워크 0** | `httpx.MockTransport`로 외부 API 대체, DB는 in-memory SQLite |

## API

| Method | Path | 설명 |
|---|---|---|
| GET | `/health` | 헬스체크 |
| GET | `/api/v1/books/search?q=&limit=` | 통합 검색 → `items[]`, `errors{source: ExceptionType}` |
| POST | `/api/v1/books/import` | `{source, external_id}` 퍼블릭 도메인 전문 가져오기 (400: 비허용 소스, 422: 본문 없음, 502: 상위 장애) |
| GET | `/api/v1/books/{id}/chunks/{index}` | 청크 본문 + `min_dwell_ms` |

## 추가된 구성 (2026-10)

```
app/auth.py            이메일 세션 인증 — BCrypt(12), 30분 유휴 만료, 로그인 시 세션 재발급, CSRF
app/web.py             모바일 웹 앱 화면(Jinja2) — 홈 3D, 탐색, 서재, 대결, 리더, 화면 꾸미기
app/api/me.py          화면 꾸미기 저장 (users.settings)
app/api/social.py      반응 · 친구 진행 · 대결 순위 · WebSocket /api/v1/ws/books/{id}
app/realtime.py        책 단위 인메모리 브로커
app/services/races.py  대결방(rooms) — 초대 코드, 정원 8, 순위 = 검증된 청크 수
app/services/social.py 반응 + 스포일러 가드, 같은 방 범위(circle)
app/services/uploads.py EPUB·PDF·TXT → 본문, DRM 거부, 본문 지문(같은 책 판별)
app/core/appearance.py 테마 · 배경 30종 · 강조 색 12종 (looks.css와 짝)
```

| 결정 | 이유 |
|---|---|
| **체류시간은 서버가 잰다** — 쪽을 펼친 시각(reading_events)부터 "다 읽음" 요청까지 | 클라이언트의 verified 값을 믿으면 API 한 번으로 레이스 점수를 올릴 수 있다 |
| **WebSocket 이벤트에 반응 본문을 싣지 않는다** | 받는 쪽이 자기 스포일러 가드가 걸린 API로 다시 읽게 해서, 소켓으로 스포일러가 새지 않게 |
| **반응은 같은 방(circle) 안에서만** | 모르는 사람의 반응이 섞이지 않게(PRD ROOM-03) |
| **화면 꾸미기는 계정에 저장, 서버가 렌더링 때 적용** | 모든 기기 동일, 첫 화면부터 깜빡임 없음 |
| **3D는 CSS transform + 작은 JS** (책 상자, 롤러 홈, StPageFlip) | 빌드 단계 없이 Python 서버만으로 배포 |

| Method | Path | 설명 |
|---|---|---|
| PUT | `/api/v1/books/{id}/progress` | `{chunk_index, verified}` — 펼침 기록 / 서버 체류시간 판정, 같은 방에 `progress.updated` |
| GET·POST | `/api/v1/books/{id}/reactions` | 반응 목록(가드 적용) / 남기기, 같은 방에 `reaction.added` |
| GET | `/api/v1/books/{id}/social` | 같은 방 친구 진행 · 봉인된 반응 수 |
| GET | `/api/v1/races/{id}/standings` | 대결 순위 |
| PUT | `/api/v1/me/settings` | 테마 · 배경 · 강조 색 |
| WS | `/api/v1/ws/books/{id}` | 실시간 이벤트 (로그인 세션 필요, 아니면 4401) |

## 포인트 원장 + 순위 (2026-11)

```
app/services/points.py   적립 규칙 · 멱등 award() · 주간/전체 순위 · 연속 일수
app/api/points.py        GET /api/me/points (= /api/v1/me/points): 총합 · 이번 주 · 연속 일수 · 최근 원장 20줄
app/templates/leaderboard.html  GET /leaderboard?range=week|all — 상위 50 + 내 순위, 같은 방 친구 표시, 빈 상태
```

| 적립 지점 | 점수 | ref_id | 어디서 |
|---|---|---|---|
| 체류시간 검증을 통과한 쪽 | +1 | `book:chunk` | `PUT /api/v1/books/{id}/progress` 가 `verified_chunk` 를 올린 뒤 |
| 문단에 처음 남긴 반응 (하루 20번) | +1 | `book:chunk:anchor` | `POST .../reactions` |
| 대결에서 문장 공유 | +3 | `quote.id` | `POST /api/v1/races/{id}/quotes` |
| 대결에서 친구 추월 (방·사람마다 한 번) | +5 | `room:other` | 쪽 적립 직후, 아까는 뒤였고 지금은 앞선 사람 |
| 대결 책 완주 (방마다 한 번) | +20 | `room` | 쪽 적립 직후 `verified_chunk >= total-1` |
| 7일 연속 읽기 (7·14·21일째) | +30 | KST 날짜 | 그날 첫 쪽 적립 때만 계산 |

| 결정 | 이유 |
|---|---|
| **points_log 는 append-only, 잔액은 SUM** | 행을 고치지 않으니 감사가 쉽다. 멱등성은 unique (user_id, source, ref_id) 제약이 지킨다(hardening, 마이그레이션 `c2d4e6f8a013` 이 기존 중복을 먼저 지움). `award()` 는 먼저 찾아보지 않고 SAVEPOINT 안에서 넣어 보고, IntegrityError 면 "이미 받음" — 동시 요청이 겹쳐도 한 번만, 호출한 쪽의 다른 변경은 그대로 |
| **모든 날짜 경계는 KST** — 주간은 월요일 00:00, 하루 상한·연속 일수도 KST 날짜 | 저장은 UTC, 경계만 `week_start()` / `day_start()` 가 KST→UTC 로 바꿔서 쿼리 |
| **응답은 그대로** — 적립은 기존 API 응답 뒤에 붙고 아무것도 바꾸지 않는다 | 리더·대결 화면의 계약을 건드리지 않기 위해 |
| **연속 일수는 KST 날짜별 GROUP BY 쿼리 하나** | 최근 400일 안의 "쪽 읽은 KST 날짜"를 한 번에 읽고 파이썬이 이어진 날을 센다. KST 날짜 식만 방언별(Postgres `to_char(timezone('Asia/Seoul', …))`, SQLite `strftime(…, '+9 hours')`) — 상수를 리터럴로 둬서 SELECT·GROUP BY 가 같은 식이 된다 |

## 보안 헤더 · 실시간 폴백 (hardening, 2026-11)

```
app/core/csp.py          요청마다 nonce, 정책 문자열, Jinja 전역 csp_nonce(NonceProxy)
app/main.py              observe_request 미들웨어가 request.state.csp_nonce + contextvar 를 채우고 CSP 헤더를 단다
app/static/presence.js   haedokLive(): 친구 소식 채널(WebSocket → 폴링 폴백) + presence 박동
tests/js/live_fallback.test.cjs  가짜 시계·소켓으로 폴백·백오프·숨김 정지를 확인(pytest 가 node 로 돌린다)
```

| 결정 | 이유 |
|---|---|
| **CSP 는 강제(`CSP_REPORT_ONLY=false`), script-src 에 nonce, `'unsafe-inline'` 없음** | 인라인 `<script>` 는 모두 `nonce="{{ csp_nonce }}"`, `onclick=`·`onsubmit=` 같은 인라인 속성은 쓰지 않는다(관리자 삭제 확인은 `data-confirm` + 리스너, 오프라인 화면은 `offline.js`). 테스트가 모든 화면 HTML 을 파싱해 확인한다 |
| **style-src 는 `'unsafe-inline'` 유지** | `style=""` 속성(테마 변수 등)이 많고, 스타일 주입은 스크립트 실행보다 위험이 훨씬 작다 |
| **500 화면은 nonce·헤더를 직접 맞춘다** | 예외 처리기가 미들웨어 바깥(ServerErrorMiddleware)에서 돌아 미들웨어 헤더가 붙지 않는다 |
| **REALTIME_WS 가 꺼지면 곧바로 폴링, 켜져도 4초 안에 안 열리면 폴링** | Vercel 함수도 소켓을 열 수 있지만 브로커가 인스턴스 메모리라 다른 인스턴스의 소식을 못 받으므로 VERCEL_ENV 가 있으면 기본으로 끈다(/social 의 `live.ws`). 리더 폴링은 3초, 실패하면 두 배씩 최대 2분. 소켓이 열려 있어도 30초 안전망 폴링. 소켓은 30초·60초 뒤 다시 시도하고 3번 실패하면 그 화면에서는 포기. 소켓은 인증 뒤 DB 세션을 바로 반납한다 |
| **가려진 탭은 타이머 자체를 멈춘다** | 숨김 동안 아무 요청·타이머가 없고, 다시 보이면 곧바로 한 번 묻는다. presence 박동(5초)도 같은 규칙에 실패 시 60초까지 늦춘다 |
| **폴링 응답도 스포일러 가드를 지난다** | `/social` 은 친구 위치와 봉인 개수만, 반응 본문은 `/reactions` 가 검증 위치로 거른다 — 소켓 이벤트와 같은 원칙 |

## 다음 Phase에서 채울 자리

- 소셜 로그인(Authlib)을 `app/auth.py` 옆에 붙인다 (oauth_accounts 테이블은 이미 있음)
- 서버를 여러 대로 늘리면 `app/realtime.py`의 Broker를 Redis Pub/Sub 구현으로 교체

## 온보딩 · SEO · 공유 (2026-11)

```
app/seo.py             robots.txt · sitemap.xml · /og/default.png (scripts/build_og.py 로 미리 그린 PNG)
app/templates/base.html  description / og:* / twitter:card / canonical — 페이지는 description·og_title·og_image 블록만 덮어쓴다
app/templates/join_preview.html  로그인 전 초대 미리보기 (GET /join/{code}) — 공개 + noindex
app/templates/_welcome.html      첫 가입 뒤 홈의 3단계 안내 카드 (세션 first_run, POST /welcome/dismiss 로 닫음)
```

| 결정 | 이유 |
|---|---|
| **초대 미리보기는 로그인 없이 공개, 대신 `noindex`** | 카카오톡·슬랙 봇은 OG 태그만 읽는다. 로그인 벽이 있으면 미리보기 카드가 깨진다 |
| **가입의 `next`는 세션에만 둔다** (`/signup?next=/join/CODE` → 인증 뒤 이동) | 폼·메일에 안 실어도 되고, `_safe_next`로 이 사이트 안 경로만 받는다 |
| **첫 방문 카드는 세션 표시(`first_run`)** | 스키마 변경 없이 한 번만 보이고, 닫으면 끝 |
| **카카오 SDK를 쓰지 않는다** — `navigator.share` + 클립보드 대체 | 카카오 링크 공유 SDK는 앱 키·도메인 등록이 필요하다. 기기 공유 시트로 카카오톡에 보내면 OG 카드가 그대로 뜬다 |
| **OG 이미지는 정적 PNG 한 장** (`app/static/og/default.png`) | Pillow·한글 글꼴을 서버리스 번들에 넣지 않으려고. 방별 이미지는 og:title에 책 제목만 넣는다 |
| **공개 주소는 `SITE_URL` → `VERCEL_PROJECT_PRODUCTION_URL` → `VERCEL_URL` → 요청 base** | 미리보기 배포에서도 절대 주소의 og:image가 깨지지 않게 |

## v3 기능 (2026-11) — 목표 · 모집 · 마일리지 · 친구/채팅 · 게시물 · 공개 페이지

다섯 기능을 따로따로 만들어 합쳤다. 그래서 모듈 경계가 같다: 모델은 `app/models_<기능>.py`, 규칙은 `app/services/<기능>.py`, 화면 라우터는 `app/<기능>.py`, CSS/JS도 기능별 새 파일, 마이그레이션은 기능마다 하나(13~17번), 그리고 기능 스위치 하나.

### 하루 목표 · 잔디 (`services/goals.py`, `api/goals.py`, `static/goals.js`)

| 결정 | 이유 |
|---|---|
| **읽기 날짜는 KST 새벽 4시에 바뀐다** | 자정을 넘겨 읽은 쪽이 다음 날로 넘어가 연속이 끊기지 않게, 밤늦게 읽은 쪽은 그 전날로 친다 |
| **센다 = 체류시간을 통과한 쪽만**, 같은 (책, 쪽)은 처음 인정된 날 한 번 | 잔디도 15초 규칙을 그대로 따른다 |
| **보호권(최대 2개)·무료 복구(한 달에 한 번)는 화면을 볼 때 한꺼번에 정산(lazy)** | 크론 없이 서버리스에서 돈다. `settled_through`로 한 번만, (user, day) unique로 같은 날을 두 번 메우지 않는다 |
| **7일 연속 마일리지는 그대로 자정(KST) 경계** | 적립 규칙(points)은 바꾸지 않았다 |

### 같이 읽을 사람 모집 (`board.py`, `services/board.py`)

| 결정 | 이유 |
|---|---|
| **모집 글 하나 = 대결방 하나** | 방 만들기·참가는 `races.create_race` / `races.join`을 그대로 거친다 |
| **정원은 `seats < capacity`일 때만 올리는 UPDATE 한 문장** | Postgres는 행을 잠그고 조건을 다시 보고, SQLite는 쓰기를 한 줄로 세워서 마지막 자리는 한 명만 들어간다 |
| **신고된 글은 관리자가 내린다** (`/admin/together`) | 글은 닫힘(closed)이 되고, 이미 들어온 사람의 방은 남는다. 방장이 지울 때도 방에 혼자일 때만 방을 같이 지운다 |

### 마일리지 · 제휴 리워드 (`mileage.py`, `services/mileage.py`)

| 결정 | 이유 |
|---|---|
| **잔액 = 모은 것(`points_log`) − 쓴 것(`mileage_spends`)**, 순위는 계속 '모은 것' | 적립 원장은 append-only 그대로 두고, 쓰기는 별도 원장 |
| **동시에 눌러도 더 쓰지 않게**: 사용자 행 잠금(Postgres) + 사용자별 순번 `(user_id, seq)` unique + 폼마다 `idem_key` unique + 기다리는 교환 요청은 사람당 하나(부분 unique 인덱스) + 재고는 조건부 UPDATE | 잠금이 없는 SQLite에서도 unique 제약이 두 번째 요청을 막는다 |
| **리워드는 관리자가 확인한 뒤 가입 이메일로** — 가입 7일 뒤부터, 한 달 2번 | 전화번호·주소를 받지 않는다. 부정 적립을 사람이 한 번 본다 |
| **예시 상품(is_sample)은 '제휴 준비 중'** | 진짜 제휴 전에도 화면을 보여 줄 수 있게. 그림은 `app/static/rewards/`(출처 CREDITS.md) 또는 허용한 호스트만 |

### 친구 · 채팅 (`chat.py`, `services/chat.py`, `api/chat.py`, `static/chat.js`)

| 결정 | 이유 |
|---|---|
| **두 사람 사이 friendships 한 행(작은 id, 큰 id)** — 신청·수락·거절·취소·끊기·차단 | 방향이 다른 두 행이 어긋나지 않는다 |
| **방 채팅은 두 채널: `# 책 이야기`(쪽 도장) · `# 수다`(도장 없음)** | 스포일러 걱정 없이 떠들 곳과, 책 이야기를 할 곳을 나눈다 |
| **도장은 보낼 때 서버가 그 사람의 `verified_chunk`로 찍는다**, 덜 읽은 독자에게는 자리표시만 | 판정은 반응과 같은 `social.page_open`. 따라잡으면 다음 폴링에 본문이 온다. 답장 인용·반응·공지·목록 미리보기에도 잠긴 본문은 나가지 않는다 |
| **고치기는 없다**(지우고 다시 보낸다) | 도장이 낡지 않는다 |
| **개인 채팅은 수락한 친구끼리, `||스포일러||`는 흐리게만** | 같은 책을 읽는다는 보장이 없어서 도장 대신 보내는 사람이 가린다 |
| **폴링 3초 → 실패 시 두 배씩 60초, 가려진 탭은 타이머 없음** | 아래 실시간 원칙과 같다. 메뉴의 안 읽음 점은 `ChatMenuDot` 미들웨어가 화면을 그릴 때 붙인다 |

### 읽은 책 게시물 (`posts.py`, `services/posts.py`, `static/posts.js`)

| 결정 | 이유 |
|---|---|
| **모든 읽기에서 서버가 판정** — 지운 글·차단(양쪽)·'친구만' 글은 404 | 있는지조차 알리지 않는다 |
| **책은 스냅샷으로 저장, 개인이 올린 파일은 게시물의 책이 될 수 없다** | 비공개 파일 제목이 새지 않게 |
| **같은 사람이 같은 책을 하루(KST)에 한 번** | 같은 글이 피드에 줄줄이 쌓이지 않게 |
| **친구 관계는 채팅 기능의 friendships를 읽는다** | 채팅이 꺼져 있어도 표는 있다 |
| **하단 '나' 탭은 `/profile`(내 게시물 격자)로** | 인스타그램 프로필의 생김새를 따른다. 계정 설정은 `/me`, 게시물이 꺼지면 나 탭도 `/me` |
| **사진 1~5장은 DB에 JPEG 바이트로**(`post_photos`, `/media/p/{id}/{n}.jpg`로 내보낸다) | 서버리스라 파일 저장소가 없다. 프로필 사진과 같은 방식 |
| **자르기는 브라우저가 한다** — 게시물 전체를 1:1 또는 4:5로, 캔버스에 1080px로 다시 그려 보낸다(`posts-compose.js`) | 런타임에 Pillow를 넣지 않는다. 다시 그린 JPEG에는 EXIF가 남지 않는다 |
| **서버는 JPEG를 그리지 않고 마커만 걷는다**(`services/post_photos.py`) — APP1~15(EXIF · GPS · XMP · ICC)와 COM, EOI 뒤 꼬리를 버리고 SOF에서 크기를 읽는다. 한 장 400 KB, 하루 30장 | 캔버스를 건너뛴 요청이 와도 위치가 새지 않게 |
| **저장 · 댓글 좋아요 · 답글 · 댓글 신고 · 소개 한 줄은 각자 작은 표**(마이그레이션 `f66c5b0d2e06`) | 더하기만 하는 마이그레이션. 저장됨은 나만 본다 |
| **친구에게 보내기는 개인 채팅에 `/p/{id}` 한 줄** | 채팅이 볼 수 있는 사람에게만 첫 사진이 담긴 카드로 보여 준다. 채팅의 속도 제한을 그대로 쓴다 |

### 공개 페이지 (`intro.py`, `public_facts.py`)

| 결정 | 이유 |
|---|---|
| **`/guide`(사용법) · `/docs`(규칙) · `/developers`(만든 방법)는 로그인 없이 공개** | 처음 오는 사람과 "어떻게 만들었어?" 하는 사람에게 링크 하나로 |
| **화면의 숫자는 코드 상수에서 읽는다** (`pace.PAGE_DWELL_MS`, `points.RULES`, `uploads.ALLOWED` …, 테스트·마이그레이션 수는 import 때 센다) | 규칙이 바뀌면 문서도 같이 바뀐다. 배포 번들에 `tests/`가 없으면 그 줄만 뺀다 |
| **Swagger는 `/api/docs`로** | `/docs`는 사람이 읽는 규칙 문서가 쓴다 |

### 실시간 원칙 (모든 기능 공통)

배포(Vercel)에서는 WebSocket을 쓰지 않는다. `app/realtime.py` 브로커가 인스턴스 메모리 안에만 있어서, 다른 인스턴스에 붙은 친구의 소식이 오지 않기 때문이다(`REALTIME_WS`가 비어 있으면 `VERCEL_ENV`가 있을 때 꺼진다).

| 화면 | 주기 | 백오프 | 코드 |
|---|---|---|---|
| 리더 친구 소식 `/social` | 3초 (소켓이 열려 있으면 30초 안전망) | 두 배씩 최대 2분 | `reader.js` → `presence.js`의 `haedokLive` |
| 같이 읽는 중(presence) 박동 | 5초 | 최대 60초, 리더 presence는 60초 · 대결방 presence는 30초 소식 없으면 뺀다 | `presence.js`, `books.PRESENCE_TTL`, `races.PRESENCE_TTL` |
| 채팅 | 3초 | 두 배씩 최대 60초 | `chat.js` |

공통: 가려진 탭은 타이머를 멈추고 다시 보이면 곧바로 한 번 묻는다. 폴링 응답도 스포일러 가드를 지난다.

### 베스트셀러 (`services/bestsellers.py`, `scripts/build_bestsellers.py`)

GitHub Actions(`bestsellers.yml`)가 매일 06:10 KST에 Yes24 분야별(소설·인문·에세이) 베스트를 읽어 `app/data/bestsellers.json`을 고치고, 테스트를 통과하고 순위가 바뀐 날에만 커밋한다 → Vercel이 다시 배포. 앱은 요청 중에 Yes24에 접속하지 않는다(그 경로는 Yes24 robots.txt가 막지 않는다).

### 메일 순서 (`services/mail.py`)

`transports()`: 배포(`VERCEL_ENV`)는 HTTP 먼저 — Apps Script(`GAS_MAIL_URL`+`GAS_MAIL_SECRET`) → Brevo → Resend → SMTP 465/587. 로컬은 SMTP 먼저. 아무것도 없으면 `log`(로컬은 코드를 로그에). 받는 사람에게 모두 실패하면 `ADMIN_EMAILS`에게 코드를 대신 보낸다(`_relay_to_admin`). 시도는 모두 `mail_logs`.
