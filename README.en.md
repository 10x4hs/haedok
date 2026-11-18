[한국어](README.md) | **English**

<div align="center">

<img src="docs/images/banner.png" alt="偕讀 Haedok — Same book, together." width="720">

# Same book, together.

**偕** together + **讀** read — read the same book with friends at your own pace, react to paragraphs, and race to see who reads more.<br>
The server keeps the reading time, and the server hides what you haven't read yet.

[![CI](https://github.com/10x4hs/haedok/actions/workflows/ci.yml/badge.svg)](https://github.com/10x4hs/haedok/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Vercel](https://img.shields.io/badge/Vercel-deployed-000000?logo=vercel&logoColor=white)

[**Open the app → haedok.vercel.app**](https://haedok.vercel.app) · [How to use](https://haedok.vercel.app/guide) · [Docs (rules)](https://haedok.vercel.app/docs) · [Developer](https://haedok.vercel.app/developers) · [Architecture](docs/architecture.md)

<br>

<a href="app/static/demo/haedok-demo.mp4"><img src="app/static/demo/haedok-demo-poster.jpg" alt="Haedok in 41 seconds — click to watch" width="280"></a>

<sub>▶ A 41-second video: sign-up, reading, reactions and a race (<a href="app/static/demo/haedok-demo.mp4">haedok-demo.mp4</a>). The app itself is in Korean.</sub>

</div>

## Features

<table>
<tr>
<td width="200" align="center"><img src="docs/images/app/5-reader.jpg" alt="Reader with the 15-second ring at the bottom" width="180"></td>
<td>
<h3>📖 Read together, verified</h3>
Public-domain classics (Wikisource, Project Gutenberg) open in the in-app reader. Keep a page open for <b>15 seconds</b> and it counts as read — the same for every page, whatever its length or your screen size. The server measures the time, not the browser. DRM-free EPUB · PDF · TXT files you bought can be uploaded and read in the same reader; only the uploader can open them. On a desktop the reader opens two pages like a paper book.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/6-react.jpg" alt="Paragraph reaction sheet" width="180"></td>
<td>
<h3>💬 Paragraph reactions · spoiler guard</h3>
Tap a paragraph to leave an emoji (❤️ 😂 😮 😢 👏) or a 140-character line. Only people in the same race room see them, and reactions on pages you haven't read show as <b>“🔒 N”</b> — a count only. The server leaves the text out of the response itself.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/7-race.jpg" alt="Race room with four readers on a track" width="180"></td>
<td>
<h3>🏁 Races</h3>
Pick a book and a length (1 · 3 · 7 · 14 days) to get a 6-character invite code; up to 8 people per room. Ranking is by server-verified pages, and the track shows who is reading right now. The 대결 tab lists my rooms, finished rooms and rooms that are recruiting in one calm list.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/8-me.jpg" alt="Profile with today's goal ring and a 17-week heatmap" width="180"></td>
<td>
<h3>🌱 Daily goal · heatmap</h3>
Choose 3 · 5 · 10 · 20 pages a day; every day with verified pages adds a square to the heatmap and extends your streak. The reading day changes at <b>4 a.m. KST</b>. A missed day is covered automatically by a streak freeze (hold up to 2), and once a month you can repair a single missed day by reading double the next day.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/13-together.jpg" alt="Board for finding people to read with" width="180"></td>
<td>
<h3>🙋 Find people to read with (<code>/together</code>)</h3>
Post a book, pages per day, seats and a one-line pitch; each post is a race room and one tap joins it. When two people tap the last seat at once, only one gets in.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/16-chat-room.jpg" alt="Room chat with a locked message" width="180"></td>
<td>
<h3>👋 Friends · chat</h3>
Friends get Instagram-style <b>direct messages</b>; race rooms get Discord-style <b>room chat</b> with two channels, <code># 책 이야기</code> (book talk) and <code># 수다</code> (chatter). Book-talk messages are stamped with the sender's verified page, so a message from someone further ahead stays locked until you reach that page. Replies, reactions, notices, reports and blocking are included.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/18-mileage.jpg" alt="Mileage balance, streak freeze and partner rewards" width="180"></td>
<td>
<h3>🪙 Mileage · partner rewards</h3>
Earn mileage for verified pages (+1), first reaction on a paragraph (+1, 20 a day), sharing a quote (+3), overtaking a friend (+5), finishing a race book (+20) and 7-day streaks (+30). Spend it on streak freezes (50) or partner rewards, which an admin checks and sends to your sign-up email. Weekly and all-time rankings too.
</td>
</tr>
<!-- SLOT:posts-feature — replace the image below with the posts screenshot after the posts work is merged (e.g. docs/images/app/24-feed.jpg). -->
<tr>
<td align="center"><img src="docs/images/app/8-me.jpg" alt="(posts screenshot slot — replaced after merge)" width="180"></td>
<td>
<h3>📷 Reading posts · profile</h3>
Share a book you read with a photo, a short review, a star rating and a favourite line. Friends and members see it in the feed and can like and comment. The <b>나</b> (me) tab opens your profile (<code>/profile</code>) with all your posts. Posts can be public or friends-only, and can be deleted or reported.
</td>
</tr>
<!-- /SLOT:posts-feature -->
<tr>
<td align="center"><img src="docs/images/app/3-explore.jpg" alt="Discover: continue reading, famous covers, rankings" width="180"></td>
<td>
<h3>🧭 Discover · bookstore</h3>
Continue-reading card, a cover strip and daily picks drawn only from famous books, genre shelves and popularity rankings. About 1,300 classics open instantly; new books get an info page with purchase links. The home screen leads with the #1 bestseller of each category, refreshed every morning.
</td>
</tr>
<tr>
<td align="center"><img src="docs/images/app/20-guide.jpg" alt="Public how-to page" width="180"></td>
<td>
<h3>🌐 Public pages · sign-in</h3>
<a href="https://haedok.vercel.app/guide">How to use</a> (8 steps), <a href="https://haedok.vercel.app/docs">Docs</a> (the 15-second rule, mileage table, upload limits) and <a href="https://haedok.vercel.app/developers">Developer</a> (four layers, choices and trade-offs) need no account. Their numbers are read from code constants (<code>app/public_facts.py</code>). Sign-in and sign-up float over a wall of book covers.
</td>
</tr>
</table>

## Screens

| Landing | Sign in | Sign up | 6-digit code | Home |
|:---:|:---:|:---:|:---:|:---:|
| <img src="docs/images/app/0-landing.jpg" alt="Landing" width="150"> | <img src="docs/images/app/1-login.jpg" alt="Sign in" width="150"> | <img src="docs/images/app/1-signup.jpg" alt="Sign up" width="150"> | <img src="docs/images/app/1b-verify.jpg" alt="Verify" width="150"> | <img src="docs/images/app/2-home.jpg" alt="Home" width="150"> |
| **Bookstore** | **Races** | **Friends** | **Messages** | **Direct message** |
| <img src="docs/images/app/4-store.jpg" alt="Bookstore" width="150"> | <img src="docs/images/app/12-races.jpg" alt="Races" width="150"> | <img src="docs/images/app/14-friends.jpg" alt="Friends" width="150"> | <img src="docs/images/app/15-chat.jpg" alt="Messages" width="150"> | <img src="docs/images/app/17-chat-dm.jpg" alt="Direct message" width="150"> |
| **Mileage ranking** | **Docs** | **Developer** | **Feed** | **Profile** |
| <img src="docs/images/app/19-leaderboard.jpg" alt="Ranking" width="150"> | <img src="docs/images/app/21-docs.jpg" alt="Docs" width="150"> | <img src="docs/images/app/22-developers.jpg" alt="Developer" width="150"> | <!-- SLOT:posts-feed docs/images/app/24-feed.jpg -->_(after merge)_ | <!-- SLOT:posts-profile docs/images/app/25-profile.jpg -->_(after merge)_ |

On a desktop the bottom tab bar becomes a sidebar, the reader opens two pages, and chat shows the list and the conversation side by side.

| Desktop home | Desktop reader |
|:---:|:---:|
| <img src="docs/images/app/9-pc-home.jpg" alt="Desktop home"> | <img src="docs/images/app/10-pc-reader.jpg" alt="Desktop reader"> |
| **Desktop room chat** | **Desktop race room** |
| <img src="docs/images/app/11-pc-chat.jpg" alt="Desktop chat"> | <img src="docs/images/app/23-pc-race.jpg" alt="Desktop race"> |

<details>
<summary><b>Admin</b> — <code>/admin</code> opens only for <code>ADMIN_EMAILS</code> (404 for everyone else)</summary>
<br>

| Overview | Users | Rooms | Mail | Rewards |
|:---:|:---:|:---:|:---:|:---:|
| <img src="docs/images/admin/1-dashboard.jpg" alt="Overview" width="150"> | <img src="docs/images/admin/2-users.jpg" alt="Users" width="150"> | <img src="docs/images/admin/3-rooms.jpg" alt="Rooms" width="150"> | <img src="docs/images/admin/4-mail.jpg" alt="Mail" width="150"> | <img src="docs/images/admin/5-rewards.jpg" alt="Rewards" width="150"> |

Also: board, chat and post reports.

</details>

<sub>All screenshots use example accounts created by scripts. See <a href="docs/README-assets.md">docs/README-assets.md</a> to regenerate them.</sub>

## How it works

- **Four layers** — templates + vanilla JS (`app/templates`, `app/static`) → routes that check login and CSRF (`app/web.py`, `app/api/`, `app/chat.py`, `app/posts.py`, …) → services where every rule lives (`app/services/`) → SQLAlchemy models (`app/models*.py`, Postgres on Neon, SQLite locally and in tests).
- **Reading verification** — the server records when a page was opened and when "done" arrives (`reading_events`); only the next page can be credited, only after 15 s (`PAGE_DWELL_MS`), and idle time is capped at 10× (`DWELL_CAP_MULTIPLIER`). `DWELL_SCALE` shrinks it for demos.
- **Spoiler guard** — reactions are filtered by your progress in the database (`social.reactions_for`); book-talk chat messages carry the sender's verified page and reach readers behind them only as a placeholder. Live updates never carry reaction text.
- **Polling, not WebSocket, on Vercel** — the in-memory broker (`app/realtime.py`) can't reach other function instances, so production polls: reader 3 s (backing off to 2 min), presence beat 5 s, chat 3 s (to 60 s). Hidden tabs stop polling. `REALTIME_WS` turns sockets on for a single server.
- **Mail chain** — on Vercel: Apps Script → Brevo → Resend → SMTP; locally: SMTP → Apps Script → Brevo → Resend; if all fail the code is relayed to `ADMIN_EMAILS`. Every attempt goes to `mail_logs`. Codes are stored only as HMAC fingerprints.
- **Bestsellers** — [bestsellers.yml](.github/workflows/bestsellers.yml) runs daily at 06:10 KST, rewrites `app/data/bestsellers.json` from Yes24 category lists, and commits only when the ranking changed; Vercel redeploys from that commit.
- **Feature flags** — `FEATURE_GOALS`, `FEATURE_BOARD`, `FEATURE_CHAT`, `FEATURE_MILEAGE`, `FEATURE_POSTS` (all `true` by default). Off hides the entry points and returns 404.

Design decisions in detail: [docs/architecture.md](docs/architecture.md) (Korean).

## Run it locally

```bash
git clone https://github.com/10x4hs/haedok.git && cd haedok
uv venv .venv && uv pip install -e ".[dev]" --python .venv/bin/python
export DATABASE_URL="sqlite+aiosqlite:///$PWD/scratch-dev.db" DWELL_SCALE=0.06   # demo: 0.9 s per page
.venv/bin/uvicorn app.main:app --port 8011     # http://localhost:8011 · API docs at /api/docs
```

With SQLite the app creates its tables on start-up. To fill it with example data (after the first start):

```bash
.venv/bin/python scripts/readme_shots.py --seed "$DATABASE_URL"    # 3 accounts, password password123
for s in board races chat goals mileage posts; do .venv/bin/python scripts/seed_$s.py "$DATABASE_URL"; done
```

Then sign in as `harin@example.com` / `password123` (email, Enter, password, Enter).

For Postgres: `docker compose up -d db`, then `DATABASE_URL=postgresql+asyncpg://haedok:haedok@localhost:5432/haedok .venv/bin/alembic upgrade head`. Alembic migrations are for Postgres only.

## Configuration

Every setting lives in [`app/core/config.py`](app/core/config.py); [`.env.example`](.env.example) lists them all with safe placeholders. The full table with defaults is in the [Korean README](README.md#환경-변수). The ones that matter for a deployment: `DATABASE_URL`, `SECRET_KEY` (the app refuses to start on Vercel with the default), `COOKIE_SECURE=true`, `MAX_UPLOAD_MB=4` (Vercel cuts request bodies at 4.5 MB), at least one mail transport (`GAS_MAIL_URL`+`GAS_MAIL_SECRET`, `BREVO_API_KEY`, `RESEND_API_KEY` or `SMTP_*`), `ADMIN_EMAILS`, and optionally `KAKAO_REST_API_KEY`, `SITE_URL`, `SENTRY_DSN`.

## Database migrations

17 Alembic revisions in [`migrations/versions/`](migrations/versions), from `4b551282b547` (initial schema) to `e55b4a9c1d05` (reading posts); the ordered list is in the [Korean README](README.md#db-마이그레이션). CI runs `upgrade → downgrade base → upgrade` and `alembic check` on Postgres 16. To migrate production (Neon) over the direct connection:

```bash
vercel env pull --environment=production .env.production.local && \
env -u COOKIE_SECURE -u MAX_UPLOAD_MB \
  DATABASE_URL="$(grep '^DATABASE_URL_UNPOOLED=' .env.production.local | cut -d= -f2- | tr -d '"')" \
  .venv/bin/alembic upgrade head; \
rm -f .env.production.local
```

## Tests

```bash
.venv/bin/pytest -q && .venv/bin/ruff check .
```

509 tests pass (measured 2026-11-18, about 95 s). External APIs are mocked with `httpx.MockTransport`/respx and the database is in-memory SQLite, so no network is needed.

## Deployment

Vercel routes every request to `api/index.py` (the FastAPI app) per `vercel.json`, bundling only `app/templates`, `app/static` and `app/data`. The project is connected to GitHub, so merging to `main` deploys to production. CI: [ci.yml](.github/workflows/ci.yml). A `Dockerfile` (runs `alembic upgrade head`, then serves on port 8000) is there for other hosts.

## Contributing · license

- Issues and PRs are welcome. Please make `pytest` and `ruff` pass, and ship a migration (additive, with `downgrade()`) when the schema changes.
- No license has been chosen yet; until one is, all rights are reserved by the author.
- Book texts are public-domain works from [Wikisource](https://ko.wikisource.org) and [Project Gutenberg](https://www.gutenberg.org) (Gutendex). Page flipping by [StPageFlip](https://github.com/Nodlik/StPageFlip), database by [Neon](https://neon.tech). Cover images are shown from bookstore/API URLs and are not stored in the repo.
- The original MVP plan is in [docs/PRD.md](docs/PRD.md) and [GOAL.md](GOAL.md).
