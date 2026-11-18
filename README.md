**한국어** | [English](README.en.md)

<div align="center">

<img width="1459" height="939" alt="偕讀 해독 — 같은 책을, 함께." src="https://github.com/user-attachments/assets/229d5e2d-7b83-4420-9d7a-b09f198fa6d6" />

# 같은 책을, 함께.

**偕** 함께 해 + **讀** 읽을 독 — 친구와 같은 책을 각자의 속도로 읽고, 문단마다 반응을 남기고, 누가 더 읽었는지 겨뤄요.<br>
읽은 시간은 서버가 재고, 아직 안 읽은 쪽의 이야기는 서버가 가려 줘요.

[![CI](https://github.com/10x4hs/haedok/actions/workflows/ci.yml/badge.svg)](https://github.com/10x4hs/haedok/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Vercel](https://img.shields.io/badge/Vercel-deployed-000000?logo=vercel&logoColor=white)

[**앱 열기 → haedok.vercel.app**](https://haedok.vercel.app) · [사용법](https://haedok.vercel.app/guide) · [Docs(규칙)](https://haedok.vercel.app/docs) · [Developer(만든 방법)](https://haedok.vercel.app/developers) · [구조](docs/architecture.md)

<br>

<a href="app/static/demo/haedok-demo.mp4"><img src="app/static/demo/haedok-demo-poster.jpg" alt="해독 41초 소개 영상 — 눌러서 보기" width="280"></a>

<sub>▶ 41초 영상 — 가입부터 읽기, 반응, 대결까지 한 번에 봐요 (<a href="app/static/demo/haedok-demo.mp4">haedok-demo.mp4</a>)</sub>

</div>

## 목차

[핵심 기능](#핵심-기능) · [화면 둘러보기](#화면-둘러보기) · [어떻게 동작하나](#어떻게-동작하나) · [기술 스택과 이유](#기술-스택과-이유) · [로컬에서 실행](#로컬에서-실행) · [환경 변수](#환경-변수) · [DB 마이그레이션](#db-마이그레이션) · [테스트](#테스트) · [배포](#배포) · [폴더 구조](#폴더-구조) · [기여 · 라이선스](#기여--라이선스)

## 핵심 기능

<table>
<tr>
<td width="200" align="center"><img src="docs/images/app/5-reader.jpg" alt="리더 — 운수 좋은 날 본문과 아래쪽 15초 고리" width="180"></td>
<td>
<h3>📖 같이 읽기 · 읽기 인증</h3>
고전은 앱 안 리더로 바로 읽어요(위키문헌 · 구텐베르크의 퍼블릭 도메인 작품). 한 쪽을 펼쳐 두고 <b>15초</b>가 지나면 그 쪽이 ‘다 읽음’으로 인정돼요 — 글자 수나 화면 크기와 상관없이 모든 쪽이 같아요. 시간은 화면이 아니라 서버가 재요. 산 전자책이 DRM 없는 EPUB · PDF · TXT면 올려서 같은 리더로 읽고, 올린 파일은 올린 사람만 열어요. PC에서는 종이책처럼 두 쪽을 펼쳐요.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/6-react.jpg" alt="문단 반응 시트 — 이모지 다섯 개와 한 줄 입력칸" width="180"></td>
<td>
<h3>💬 문단 반응 · 스포일러 가드</h3>
마음에 남은 문단을 눌러 이모지(❤️ 😂 😮 😢 👏)나 140자 한 줄을 남겨요. 같은 대결방 친구끼리만 보이고, 내가 아직 안 읽은 쪽의 반응은 <b>“🔒 N개”</b>로 개수만 보여요. 가리는 일은 서버가 응답을 만들 때 해서, 응답을 열어 봐도 본문이 없어요.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/7-race.jpg" alt="대결방 — 네 명이 달리는 레이스 트랙과 순위" width="180"></td>
<td>
<h3>🏁 대결</h3>
책과 기간(1 · 3 · 7 · 14일)을 고르면 6자리 초대 코드가 생기고, 한 방에 8명까지 들어와요. 순위는 서버가 인정한 쪽 수로 매기고, 레이스 트랙에서 누가 지금 같이 읽고 있는지 보여요. 대결 탭은 내 방 · 끝난 방 · 모집 중인 방을 한 목록으로 보여 줘요.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/8-me.jpg" alt="내 정보 — 오늘 3/5쪽 목표 고리와 17주 잔디" width="180"></td>
<td>
<h3>🌱 하루 목표 · 잔디</h3>
하루 목표를 3 · 5 · 10 · 20쪽 중에 고르면, 인정된 쪽이 쌓인 날마다 잔디가 생기고 연속 기록이 이어져요. 하루는 한국 시간 <b>새벽 4시</b>에 바뀌어요(밤늦게 읽은 쪽은 그 전날로 쳐요). 못 읽은 날은 기록 보호권(최대 2개)이 자동으로 메우고, 한 달에 한 번은 다음 날 두 배를 읽어 빠진 하루를 이을 수 있어요.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/13-together.jpg" alt="같이 읽을 사람 모집 게시판" width="180"></td>
<td>
<h3>🙋 같이 읽을 사람 모집 (<code>/together</code>)</h3>
같이 읽을 친구가 없으면 모집 글을 띄워요. 책 · 하루 쪽 수 · 정원 · 한 줄 소개를 적으면 글 하나가 대결방 하나가 되고, 누르면 바로 참가해요. 마지막 자리를 동시에 눌러도 한 명만 들어가요.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/16-chat-room.jpg" alt="방 채팅 — 책 이야기 채널과 잠긴 메시지" width="180"></td>
<td>
<h3>👋 친구 · 채팅</h3>
친구를 맺으면 인스타그램 DM처럼 <b>개인 채팅</b>을, 같은 대결방에서는 디스코드처럼 <b>방 채팅</b>을 해요. 방은 <code># 책 이야기</code>와 <code># 수다</code> 두 채널이에요. 책 이야기에는 보낸 사람이 읽은 쪽이 도장으로 찍혀서, 나보다 더 읽은 사람의 말은 내가 그 쪽까지 읽어야 열려요. 답장 · 반응 · 공지 · 신고 · 차단도 있어요.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/18-mileage.jpg" alt="마일리지 — 잔액, 기록 보호권, 제휴 리워드" width="180"></td>
<td>
<h3>🪙 마일리지 · 제휴 리워드</h3>
인정된 쪽(+1), 문단에 처음 남긴 반응(+1, 하루 20번), 문장 공유(+3), 친구 추월(+5), 대결 책 완주(+20), 7일 연속 읽기(+30)로 마일리지가 쌓여요. 모은 마일리지는 기록 보호권(50P)이나 제휴 리워드로 바꾸고, 리워드는 운영자가 확인한 뒤 가입한 이메일로 보내요. 주간 · 전체 순위도 있어요.
</td>
</tr>
<!-- SLOT:posts-feature — 게시물 기능을 맡은 작업이 머지되면 아래 이미지를 그 화면으로 바꿔 주세요(예: docs/images/app/24-feed.jpg). -->
<tr>
<td align="center"><img src="docs/images/app/8-me.jpg" alt="(게시물 스크린샷 자리 — 머지 뒤 교체)" width="180"></td>
<td>
<h3>📷 읽은 책 게시물 · 프로필</h3>
읽은 책을 사진과 함께 올리고, 한 줄 감상과 별점, 마음에 남은 문장을 남겨요. 친구와 가입한 사람들이 피드에서 보고 좋아요 · 댓글을 달아요. 하단의 <b>나</b> 탭은 내 게시물이 모인 프로필(<code>/profile</code>)로 열려요. 공개 범위(전체 · 친구만)를 고를 수 있고, 지우거나 신고할 수 있어요.
</td>
</tr>
<!-- /SLOT:posts-feature -->
<tr>
<td align="center"><img src="docs/images/app/3-explore.jpg" alt="탐색 — 이어 읽기 카드, 유명한 책 표지 띠, 인기 순위" width="180"></td>
<td>
<h3>🧭 탐색 · 서점</h3>
탐색에는 이어 읽기 카드, 유명한 책으로만 채운 표지 띠와 오늘의 추천, 장르별 서가, 인기 순위가 있어요. 고전 1,300여 권은 바로 읽고, 신간은 서점 화면에서 책 정보와 구매 링크를 보여 줘요. 홈 맨 앞에는 매일 아침 갱신되는 분야별 베스트셀러 1위가 걸려요.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/20-guide.jpg" alt="공개 사용법 페이지" width="180"></td>
<td>
<h3>🌐 공개 페이지 · 로그인 화면</h3>
로그인 없이 읽는 <a href="https://haedok.vercel.app/guide">사용법</a>(8단계), <a href="https://haedok.vercel.app/docs">Docs</a>(15초 규칙 · 마일리지 표 · 업로드 한도), <a href="https://haedok.vercel.app/developers">Developer</a>(네 층 구조 · 고른 기술과 이유)가 있어요. 이 페이지의 숫자는 손으로 적지 않고 코드의 상수에서 읽어요(<code>app/public_facts.py</code>). 로그인 · 가입 화면은 책 표지 벽 배경 위에 떠 있어요.
</td>
</tr>
</table>

## 화면 둘러보기

| 랜딩 | 로그인 | 가입 | 6자리 인증 | 홈 |
|:---:|:---:|:---:|:---:|:---:|
| <img src="docs/images/app/0-landing.jpg" alt="랜딩" width="150"> | <img src="docs/images/app/1-login.jpg" alt="로그인" width="150"> | <img src="docs/images/app/1-signup.jpg" alt="가입" width="150"> | <img src="docs/images/app/1b-verify.jpg" alt="6자리 인증" width="150"> | <img src="docs/images/app/2-home.jpg" alt="홈 — 3D로 쌓인 책과 오늘 목표" width="150"> |
| **서점** | **대결 목록** | **친구** | **메시지** | **개인 채팅** |
| <img src="docs/images/app/4-store.jpg" alt="서점 상세" width="150"> | <img src="docs/images/app/12-races.jpg" alt="대결 목록" width="150"> | <img src="docs/images/app/14-friends.jpg" alt="친구" width="150"> | <img src="docs/images/app/15-chat.jpg" alt="메시지 목록" width="150"> | <img src="docs/images/app/17-chat-dm.jpg" alt="개인 채팅" width="150"> |
| **마일리지 순위** | **Docs** | **Developer** | **게시물 피드** | **프로필** |
| <img src="docs/images/app/19-leaderboard.jpg" alt="마일리지 순위" width="150"> | <img src="docs/images/app/21-docs.jpg" alt="공개 Docs" width="150"> | <img src="docs/images/app/22-developers.jpg" alt="공개 Developer" width="150"> | <!-- SLOT:posts-feed docs/images/app/24-feed.jpg -->_(머지 뒤 추가)_ | <!-- SLOT:posts-profile docs/images/app/25-profile.jpg -->_(머지 뒤 추가)_ |

**PC · 태블릿에서는** 하단 탭 바가 왼쪽 사이드바가 되고, 리더는 두 쪽을 펼치고, 채팅은 목록과 대화가 나란히 보여요.

| PC 홈 | PC 리더 · 같이 읽는 친구 |
|:---:|:---:|
| <img src="docs/images/app/9-pc-home.jpg" alt="PC 홈"> | <img src="docs/images/app/10-pc-reader.jpg" alt="PC 리더"> |
| **PC 방 채팅** | **PC 대결방** |
| <img src="docs/images/app/11-pc-chat.jpg" alt="PC 방 채팅"> | <img src="docs/images/app/23-pc-race.jpg" alt="PC 대결방"> |

<details>
<summary><b>관리자 화면</b> — <code>ADMIN_EMAILS</code>에 적힌 계정만 <code>/admin</code>이 열려요(그 밖에는 404)</summary>
<br>

| 현황 | 사용자 | 대결방 | 메일 | 리워드 |
|:---:|:---:|:---:|:---:|:---:|
| <img src="docs/images/admin/1-dashboard.jpg" alt="관리자 현황" width="150"> | <img src="docs/images/admin/2-users.jpg" alt="사용자" width="150"> | <img src="docs/images/admin/3-rooms.jpg" alt="대결방" width="150"> | <img src="docs/images/admin/4-mail.jpg" alt="메일" width="150"> | <img src="docs/images/admin/5-rewards.jpg" alt="리워드 관리" width="150"> |

- **현황** — 가입자 · 대기 중인 인증 · 대결방 · 책 · 업로드 수, 최근 24시간 메일 성공/실패.
- **사용자** — 가입 대기를 바로 승인하거나 지우고, 가입한 사용자를 찾아 지워요.
- **대결방 · 메일** — 열린 방과 책, 메일 설정 점검과 최근 발송 기록.
- **리워드 · 모집 신고 · 채팅 신고 · 게시물 신고** — 교환 요청 처리와 상품 관리, 신고된 글 닫기 · 가리기.

</details>

<sub>스크린샷은 모두 스크립트가 만든 예시 계정(지우 · 하린 · 민준 · 서연 · 도윤 · 예린)으로 찍었어요. 다시 만드는 법은 <a href="docs/README-assets.md">docs/README-assets.md</a>.</sub>

## 어떻게 동작하나

### 네 층

요청은 늘 위에서 아래로 내려가요. 규칙은 셋째 층(서비스)에만 있어서, 화면을 바꿔도 규칙은 그대로예요.

```mermaid
flowchart TB
    U["휴대폰 · PC 브라우저<br/>PWA · Android TWA"] --> V
    D[("4 DB<br/>Postgres(Neon) · 로컬·테스트는 SQLite<br/>app/models*.py · migrations/")]
    subgraph Vercel["Vercel Python Function (api/index.py)"]
      V["1 화면<br/>Jinja2 템플릿 + 바닐라 JS<br/>app/templates · app/static"] --> R
      R["2 라우트<br/>로그인 · CSRF 확인<br/>app/web.py · app/api/ · app/chat.py · app/posts.py …"] --> S
      S["3 서비스 — 규칙이 사는 곳<br/>15초 판정 · 스포일러 가드 · 대결 · 마일리지 · 목표 · 채팅<br/>app/services/"]
    end
    S --> D
    S -->|가입 · 비밀번호 코드 메일| M["Apps Script · Brevo · Resend · SMTP"]
    S -->|고전 전문| W["위키문헌 · Gutendex"]
    G["GitHub Actions(매일)"] -->|bestsellers.json 커밋| Vercel
```

예를 들어 ‘다 읽음’ 한 번은 이렇게 지나가요: 리더가 15초를 채우면 `verified: true`만 보내요(시간은 안 보내요) → `PUT /api/v1/books/{id}/progress`가 로그인 · CSRF를 확인 → `books.record_progress()`가 쪽을 펼친 시각부터 지금까지를 재서 판정 → 인정되면 진도와 마일리지가 올라가요.

### 읽기 인증 — 시간은 서버가 재요

- 쪽을 펼칠 때(`verified=false`)와 ‘다 읽음’ 요청(`verified=true`) 시각을 둘 다 서버가 `reading_events`에 적어요. 그 사이가 15초(`app/services/pace.py`의 `PAGE_DWELL_MS`)를 넘어야 인정해요.
- 바로 다음 쪽만 인정하고, 한 쪽에 오래 켜 둔 시간은 10배(`DWELL_CAP_MULTIPLIER`)까지만 기록해요.
- `DWELL_SCALE`은 시연용 배율이에요(0.06이면 쪽마다 0.9초). 운영은 1.0이에요.

### 스포일러 가드

- 반응 목록(`social.reactions_for`)은 DB에 있는 **내 진도**로 걸러요. 안 읽은 쪽이면 응답에 본문이 아예 없고 개수만 있어요.
- 방 채팅의 `# 책 이야기`는 보낼 때 서버가 보낸 사람의 인정된 쪽을 도장으로 찍고, 덜 읽은 사람에게는 “n쪽을 읽으면 열려요” 자리표시만 보내요. 답장 인용 · 공지 · 목록 미리보기에도 잠긴 본문은 나가지 않아요. 개인 채팅의 `||스포일러||`는 누를 때까지 흐리게만 보여 줘요.
- 실시간 소식(소켓 · 폴링)에도 반응 본문은 싣지 않아요. 받는 쪽이 가드가 걸린 API로 다시 읽어요.

### 실시간 — Vercel에서는 WebSocket 대신 폴링

Vercel 함수는 인스턴스마다 메모리가 따로라, 메모리 브로커(`app/realtime.py`)로는 다른 인스턴스에 붙은 친구의 소식이 오지 않아요. 그래서 배포에서는 폴링해요(`REALTIME_WS`가 비어 있으면 로컬은 소켓, Vercel은 폴링).

| 화면 | 주기 | 실패할 때 |
|---|---|---|
| 리더의 친구 위치 · 반응 수 (`/social`) | 3초 | 두 배씩 최대 2분 |
| 같이 읽는 중(presence) 박동 | 5초 | 두 배씩 최대 60초, 60초 소식 없으면 빠짐 |
| 채팅 새 메시지 | 3초 | 두 배씩 최대 60초 |

탭이 가려지면 타이머를 멈추고, 다시 보이면 곧바로 한 번 물어요(`app/static/presence.js`, `app/static/chat.js`).

### 메일 — 순서대로 시도하고, 다 안 되면 관리자에게

가입 · 비밀번호 재설정 6자리 코드는 설정된 수단을 차례로 시도해요(`app/services/mail.py`의 `transports`).

- **배포(Vercel)**: Google Apps Script(`GAS_MAIL_URL` + `GAS_MAIL_SECRET`) → Brevo → Resend → SMTP
- **로컬 · 일반 서버**: SMTP(465 → 587) → Apps Script → Brevo → Resend
- 아무것도 없으면 로컬은 코드를 서버 로그에 남기고, 모두 실패하면 `ADMIN_EMAILS`의 관리자에게 코드를 대신 보내 전해 주게 해요. 모든 시도는 `mail_logs`에 남아요(관리자 화면 → 메일).
- 코드는 서버 비밀키를 섞은 HMAC 지문으로만 저장하고, 10분 동안 5번까지 넣어 볼 수 있어요.

### 베스트셀러 — 하루 한 번 데이터 파일만 바꿔요

[`bestsellers.yml`](.github/workflows/bestsellers.yml)이 매일 06:10(KST)에 `scripts/build_bestsellers.py`로 Yes24 분야별(소설 · 인문 · 에세이) 베스트를 읽어 `app/data/bestsellers.json`을 고치고, 테스트를 통과하고 순위가 바뀐 날에만 커밋해요. main에 올라가면 Vercel이 다시 배포해요. 앱은 요청을 처리하는 동안 Yes24에 접속하지 않아요.

### 기능 스위치

새 기능은 설정 하나로 끌 수 있어요. 끄면 입구가 숨고 주소는 404예요.

| 변수 | 끄면 사라지는 것 |
|---|---|
| `FEATURE_GOALS` | 하루 목표 · 잔디 · 연속(`/api/v1/me/goal`, 홈 · 내 정보의 목표 칸) |
| `FEATURE_BOARD` | 같이 읽을 사람 모집(`/together`) |
| `FEATURE_CHAT` | 친구 · 채팅(`/friends`, `/chat`) |
| `FEATURE_MILEAGE` | 마일리지 상점(`/mileage`, `/admin/rewards`) — 적립은 계속돼요 |
| `FEATURE_POSTS` | 읽은 책 게시물(`/feed`, `/posts/new`, `/p/{id}`, `/u/{id}`) |

### 꼭 지키는 규칙 세 가지

1. **본문은 합법적인 것만** — 앱이 보여 주는 본문은 저작권이 끝난 작품(`FULLTEXT_SOURCES`: gutendex · wikisource)뿐이에요. 신간은 정보와 구매 링크만 두고, 올린 파일은 올린 사람만 열어요.
2. **읽은 시간은 서버가 잰다** — 넘기기만 해서는 진도도, 대결 점수도, 마일리지도 오르지 않아요.
3. **스포일러는 버그** — 안 읽은 쪽의 이야기는 반응에서도, 채팅에서도, 실시간 소식에서도 새지 않아요.

더 자세한 결정 기록은 [docs/architecture.md](docs/architecture.md)에 있어요.

## 기술 스택과 이유

| 고른 것 | 왜 | 대신 치른 값 |
|---|---|---|
| **Python 3.12 · FastAPI (async)** | DB · 메일 · 책 API를 기다리는 동안 다른 요청을 처리해요. 로그인 · DB 세션은 `Depends`로 넣어요 | 화면 쪽 도구는 직접 골라야 해요 |
| **Jinja2 서버 렌더링 + 바닐라 CSS/JS** | 첫 화면이 HTML로 바로 와요. 빌드 단계가 없어 Python 서버만으로 배포돼요. 3D 책 · 책장 넘김([StPageFlip](https://github.com/Nodlik/StPageFlip)) · 폴링은 작은 JS 파일이 맡아요 | 화면 상태는 손으로 맞춰요 |
| **PostgreSQL(Neon) + SQLAlchemy 2 + Alembic** | unique 제약과 트랜잭션이 규칙의 일부예요 — 같은 마일리지를 두 번 주지 않는 것도, 마지막 모집 자리를 한 명만 받는 것도 DB가 지켜요 | 로컬 · 테스트용 SQLite와 둘 다 맞춰야 해요 |
| **Vercel Python Function** | 요청이 올 때만 돌아서 안 쓸 때 비용이 없어요. GitHub에 올리면 바로 배포돼요 | 실시간은 폴링, 요청 본문이 4.5MB에서 끊겨 업로드는 4MB |
| **pytest + GitHub Actions** | 규칙이 깨지면 머지 전에 실패해요. 마이그레이션은 진짜 Postgres 16에서 올렸다 내렸다 다시 올려 봐요 | 기능마다 테스트를 같이 써요 |

그 밖에: 세션 쿠키(itsdangerous) + BCrypt + CSRF, 요청마다 nonce를 쓰는 CSP, DB 고정 창(fixed window) 속도 제한, Sentry(선택), PWA + Android TWA.

## 로컬에서 실행

Python 3.12 이상과 [uv](https://docs.astral.sh/uv/)가 있으면 돼요. SQLite로 돌리면 앱이 시작할 때 표를 알아서 만들어요.

```bash
git clone https://github.com/10x4hs/haedok.git && cd haedok
uv venv .venv && uv pip install -e ".[dev]" --python .venv/bin/python
export DATABASE_URL="sqlite+aiosqlite:///$PWD/scratch-dev.db" DWELL_SCALE=0.06   # 시연용: 쪽마다 0.9초
.venv/bin/uvicorn app.main:app --port 8011     # http://localhost:8011 · API 문서는 /api/docs
```

화면을 예시 데이터로 채워 보려면(앱이 한 번 떠서 표가 생긴 뒤):

```bash
.venv/bin/python scripts/readme_shots.py --seed "$DATABASE_URL"    # 지우 · 하린 · 민준, 비밀번호 password123
for s in board races chat goals mileage posts; do .venv/bin/python scripts/seed_$s.py "$DATABASE_URL"; done
```

`harin@example.com` / `password123`로 로그인하면 친구 · 대결 · 모집 · 채팅 · 잔디 · 마일리지가 다 채워진 화면을 볼 수 있어요. 로그인은 단계형이라 이메일을 넣고 Enter, 비밀번호를 넣고 Enter예요. 관리자 화면을 보려면 `ADMIN_EMAILS=admin@haedok.app`을 넣고 띄워요.

Postgres로 돌리려면 `docker compose up -d db`로 DB를 띄우고 `DATABASE_URL=postgresql+asyncpg://haedok:haedok@localhost:5432/haedok .venv/bin/alembic upgrade head`를 한 번 해 주세요. `docker compose up`은 앱(8000번)까지 같이 띄우고 시작할 때 마이그레이션도 해요. (Alembic 마이그레이션은 Postgres용이에요 — SQLite에는 쓰지 않아요.)

## 환경 변수

`.env` 파일이나 Vercel 환경 변수로 넣어요. 예시는 [`.env.example`](.env.example), 기준은 [`app/core/config.py`](app/core/config.py)예요(변수 이름은 대소문자 무시).

| 변수 | 기본값 | 역할 |
|---|---|---|
| `DATABASE_URL` | 로컬 Postgres | `postgres://…?sslmode=require`(Neon)도, `sqlite+aiosqlite:///…`도 돼요. asyncpg 형식으로 알아서 바꿔요 |
| `SECRET_KEY` | 개발용 값 | 세션 쿠키 서명 키. **Vercel에서 기본값이면 앱이 시작을 거부해요** |
| `SESSION_IDLE_MINUTES` | `30` | 아무것도 안 하면 로그인이 풀리는 시간(‘로그인 상태 유지’는 30일) |
| `COOKIE_SECURE` | `false` | HTTPS 배포에서는 `true` |
| `ADMIN_EMAILS` | 비어 있음 | `/admin`을 열 수 있는 이메일, 쉼표로 여러 개. 비어 있으면 아무도 못 열어요. 메일이 모두 실패하면 코드를 대신 받는 주소이기도 해요 |
| `DWELL_SCALE` | `1.0` | 15초 규칙의 배율(시연용). **운영은 1.0** |
| `MAX_UPLOAD_MB` | `20` | 업로드 상한. Vercel은 요청 본문을 4.5MB에서 끊으니 `4`로 둬요 |
| `GAS_MAIL_URL` · `GAS_MAIL_SECRET` | 비어 있음 | Google Apps Script 웹 앱으로 Gmail 발송(`scripts/gas_mailer.gs`). 배포에서 1순위 |
| `BREVO_API_KEY` · `BREVO_FROM` | 비어 있음 · 기본 발신 주소 | Brevo HTTP API. 도메인 없이 발신 주소 인증만으로 보내요(무료 300통/일) |
| `RESEND_API_KEY` · `RESEND_FROM` | 비어 있음 · `onboarding@resend.dev` | Resend HTTP API. 도메인을 검증해야 본인 외에도 보내요 |
| `SMTP_HOST` · `SMTP_PORT` · `SMTP_USER` · `SMTP_PASSWORD` | `smtp.gmail.com` · `465` · 비어 있음 | 로컬 · 일반 서버용 SMTP(Gmail은 앱 비밀번호). Vercel 함수에서는 끊겨요 |
| `KAKAO_REST_API_KEY` | 비어 있음 | 카카오 책 검색(공식, 무료). 통합 검색의 메타데이터 소스이고, 서점 검색과 `/store/{ISBN}`이 고른 책(`app/data/store.json`) 밖의 책도 찾게 해요 |
| `NAVER_CLIENT_ID` · `NAVER_CLIENT_SECRET` · `GOOGLE_BOOKS_API_KEY` | 비어 있음 | 메타데이터 검색 소스. 키가 없으면 그 소스만 꺼져요 |
| `WIKISOURCE_LANG` | `ko` | 위키문헌 언어 |
| `HTTP_TIMEOUT_SECONDS` | `3.0` | 바깥 API 하나의 타임아웃(느린 소스가 검색 전체를 막지 않게) |
| `BOOKSTORE_LIVE` | `false` | `true`면 키 없이 알라딘 검색 화면을 읽어요 — 알라딘 `robots.txt`가 막는 경로라 권하지 않아요 |
| `REALTIME_WS` | 비어 있음 | 친구 소식을 WebSocket으로 받을지. 비우면 로컬은 켜고 Vercel은 꺼요(폴링) |
| `FEATURE_GOALS` · `FEATURE_BOARD` · `FEATURE_CHAT` · `FEATURE_MILEAGE` · `FEATURE_POSTS` | `true` | 기능 스위치 — [위 표](#기능-스위치) |
| `SITE_URL` | 비어 있음 | 공개 주소(https://…). canonical · OG · 사이트맵 · 초대 미리보기에 써요. 비우면 `VERCEL_PROJECT_PRODUCTION_URL` → `VERCEL_URL` → 요청 주소 순 |
| `SENTRY_DSN` | 비어 있음 | Sentry 오류 수집(선택). 이메일 · 코드 · 비밀번호는 보내기 전에 지워요 |
| `LOG_LEVEL` · `LOG_JSON` | `INFO` · `true` | 로그 레벨, JSON 한 줄(서버) 또는 텍스트(로컬) |
| `CSP_REPORT_ONLY` | `false` | CSP는 요청마다 새 nonce로 강제해요. 위반을 살펴볼 때만 잠깐 `true` |
| `ANDROID_PACKAGE` · `ANDROID_CERT_SHA256` | 이 앱의 값 | Android TWA 서명 지문(`/.well-known/assetlinks.json`), 쉼표로 여러 개 |
| `APP_NAME` | `haedok` | 앱 이름 |
| `VERCEL_ENV` · `VERCEL_PROJECT_PRODUCTION_URL` · `VERCEL_URL` · `VERCEL` | Vercel이 넣어 줘요 | 직접 넣지 않아요. 메일 순서 · 실시간 기본값 · 공개 주소 · `SECRET_KEY` 확인에 써요 |

## DB 마이그레이션

Alembic 리비전은 [`migrations/versions/`](migrations/versions)에 있고, 이 순서로 이어져요(17개).

| # | 리비전 | 내용 |
|---|---|---|
| 1 | `4b551282b547` | 처음 스키마(사용자 · 책 · 청크 · 서재 · 읽기 기록 · 방 · 반응 · 포인트 원장) |
| 2 | `7c1e2f9a3b10` | 계정과 개인 업로드 |
| 3 | `9d4f6a1c2e33` | 화면 꾸미기 설정(`users.settings`) |
| 4 | `b3e8d2f4a611` | 프로필 사진 |
| 5 | `c5a1e9b7d204` | 서버 쪽 로그아웃용 세션 버전 |
| 6 | `e2f7a3c9b815` | 방 참가 · 책별 방 · 책별 독자 인덱스 |
| 7 | `a1f0c3d5e7b9` | 가입 이메일 인증 코드 |
| 8 | `f1d4c8a2b907` | 대결 presence와 문장 공유 |
| 9 | `0a7e5d3c9b21` | 메일 발송 기록(`mail_logs`) |
| 10 | `b7c2d9e4f601` | 속도 제한(DB 고정 창) |
| 11 | `c1a4e8d2f305` | 비밀번호 재설정 |
| 12 | `c2d4e6f8a013` | 포인트 원장 unique (user_id, source, ref_id) |
| 13 | `a11d0c5e7f01` | 하루 목표 · 보호권 · 복구 |
| 14 | `c33f2e7a9b03` | 모집 게시판(`recruit_posts`, `recruit_reports`) |
| 15 | `d44a3f8b0c04` | 마일리지(쓴 기록 · 리워드 상품 · 교환 요청 + 예시 상품) |
| 16 | `b22e1d6f8a02` | 친구와 채팅(친구 관계 · 대화 · 메시지 · 반응 · 읽음 · 신고) |
| 17 | `e55b4a9c1d05` | 읽은 책 게시물(게시물 · 좋아요 · 댓글 · 신고) |

새 리비전은 `alembic revision -m "…"`로 만들고, CI가 Postgres에서 `upgrade → downgrade base → upgrade`와 `alembic check`(모델과 차이 없음)를 돌려요.

**운영 DB(Neon)에 올리기** — 배포 전에 한 번, 직접 연결(pooler가 아닌 `DATABASE_URL_UNPOOLED`)로 올려요. 받아 온 비밀 파일은 바로 지워요.

```bash
vercel env pull --environment=production .env.production.local && \
env -u COOKIE_SECURE -u MAX_UPLOAD_MB \
  DATABASE_URL="$(grep '^DATABASE_URL_UNPOOLED=' .env.production.local | cut -d= -f2- | tr -d '"')" \
  .venv/bin/alembic upgrade head; \
rm -f .env.production.local
```

- 13~17번(v3 기능)은 더하기만 해요(새 표 · 인덱스, 예시 상품 행). 그래서 새 코드가 배포되기 전에 올려도 지금 돌고 있는 앱이 깨지지 않아요. 새 리비전도 이렇게 더하기만으로 써 주세요.
- 먼저 `.venv/bin/alembic current`로 지금 리비전을 확인하고, 문제가 생기면 `alembic downgrade -1`로 하나 되돌려요(모든 리비전에 `downgrade()`가 있어요).

## 테스트

```bash
.venv/bin/pytest -q && .venv/bin/ruff check .
```

지금 **509개** 테스트가 통과해요(2026-11-18, 약 95초). 외부 API는 `httpx.MockTransport` · respx로 흉내 내고 DB는 메모리 속 SQLite라 네트워크 없이 돌아요. 화면 HTML을 파싱해 CSP nonce · 인라인 핸들러를 확인하는 테스트와, `node`로 돌리는 JS 폴링 테스트(`tests/js/`)도 들어 있어요.

## 배포

- **Vercel** — `vercel.json`이 모든 요청을 `api/index.py`(FastAPI 앱 그대로)로 보내요. `app/templates`, `app/static`, `app/data`만 함수에 싣고 `tests/`, `docs/`, `scripts/`는 빼요.
- **GitHub 자동 배포** — Vercel이 GitHub 저장소에 연결돼 있어서 `main`에 머지하면 프로덕션에 배포돼요.
- **CI** — [ci.yml](.github/workflows/ci.yml): `ruff` + `pytest`, 그리고 진짜 Postgres 16에서 마이그레이션 왕복과 `alembic check`.
- **매일 베스트셀러** — [bestsellers.yml](.github/workflows/bestsellers.yml)이 06:10(KST)에 데이터를 고쳐 커밋하면 그 커밋으로 다시 배포돼요.
- **배포 전 체크** — `SECRET_KEY` · `COOKIE_SECURE=true` · `MAX_UPLOAD_MB=4` · 메일 수단 하나 이상 · `ADMIN_EMAILS`, 새 리비전이 있으면 위의 운영 DB 마이그레이션을 먼저.
- Docker로 다른 곳에 올릴 때는 `Dockerfile`이 시작할 때 `alembic upgrade head`를 하고 8000번으로 떠요.

## 폴더 구조

```
app/
  main.py             FastAPI 앱 · 미들웨어(CSP · 세션 · 로그) · 오류 화면 · 라우터 등록
  web.py              화면 라우트(랜딩 · 가입/인증 · 홈 · 탐색 · 서점 · 리더 · 서재 · 대결 · 내 정보)
  intro.py            공개 페이지 /guide · /docs · /developers  (public_facts.py가 숫자를 코드에서 읽어요)
  board.py            모집 게시판 /together
  chat.py             친구 /friends · 채팅 /chat
  mileage.py          마일리지 /mileage · 관리자 리워드
  posts.py            게시물 /feed · /posts/new · /p/{id} · /u/{id} · /profile
  admin.py            /admin (현황 · 사용자 · 대결방 · 메일 · 신고)
  auth.py             세션 · BCrypt · CSRF · 6자리 코드 · 비밀번호 재설정
  seo.py · pwa.py     robots · sitemap · OG / manifest · service worker · assetlinks
  realtime.py         책 단위 메모리 브로커(로컬 WebSocket)
  models.py           기본 스키마 + models_goals · models_board · models_mileage · models_chat · models_posts
  api/                JSON API(books · me · goals · points · social · chat) · WebSocket
  services/           규칙: pace · books · social · races · race_list · points · goals · board · mileage
                      · chat · posts · mail · uploads · search · bookstore · bestsellers · chunker
  sources/            도서 소스 어댑터(gutendex · wikisource · kakao · naver · google_books)
  core/               설정 · CSP · 로그 · 화면 꾸미기
  templates/ static/ data/   Jinja2 · CSS/JS/이미지 · 서가 · 서점 · 베스트셀러 JSON
api/index.py          Vercel 진입점
migrations/           Alembic(17개)
scripts/              데이터 만들기(build_*) · 스크린샷(readme_shots.*, seed_*) · 배너 · Apps Script 메일러
tests/                pytest(+ tests/js)
docs/                 구조 · README 이미지 만들기 · PRD(처음 MVP 기록) · 예전 시안(archive)
```

## 기여 · 라이선스

- 이슈와 PR 모두 환영해요. 브랜치를 따서 작업하고, PR 전에 `.venv/bin/pytest -q && .venv/bin/ruff check .`가 통과하는지 봐 주세요. 표가 바뀌면 마이그레이션도 같이 넣어 주세요(더하기만, `downgrade()` 포함).
- 소스 코드의 라이선스는 아직 정하지 않았어요. 별도 표기가 있을 때까지 모든 권리는 저작자에게 있어요.
- 본문은 [위키문헌](https://ko.wikisource.org)과 [Project Gutenberg](https://www.gutenberg.org)(Gutendex API)의 퍼블릭 도메인 작품이에요. 책장 넘김은 [StPageFlip](https://github.com/Nodlik/StPageFlip), 데이터베이스는 [Neon](https://neon.tech), 리워드 그림 출처는 `app/static/rewards/CREDITS.md`에 있어요.
- 표지 이미지는 서점 · 도서 API의 주소를 그대로 보여 주고 저장소에 올리지 않아요. 알라딘 전자책은 DRM 때문에 앱에서 열 수 없고, 앱에도 그렇게 안내해요.
- 화면 사용법은 [사용법](https://haedok.vercel.app/guide), 규칙의 숫자는 [Docs](https://haedok.vercel.app/docs), 처음 기획은 [docs/PRD.md](docs/PRD.md)와 [GOAL.md](GOAL.md)(처음 MVP 기록)에 있어요.
