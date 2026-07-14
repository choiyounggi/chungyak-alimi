# chungyak-alimi (Korean Housing-Subscription Notifier)

**English** | [한국어](README.ko.md)

A personal service that collects, normalizes, and stores Korean housing-subscription
(청약) data from official public open APIs (ApplyHome · LH), notifies me on Telegram
when a new notice matches my criteria, and serves a web dashboard. Deployed on a
headless Raspberry Pi 4.

> No bot-evading scraping — it uses the **government's official open APIs**.
> No Kafka, no Redis: just `systemd timer + PostgreSQL`.

🌐 **https://chungyak.duckdns.org** (HTTPS · login)

## Architecture

```
[Collect] ApplyHome (sale notices / unit types) + LH (notices / supply / detail) ──(httpx)
   │
[Normalize] pydantic normalization + filters.yaml matching (region/type/special-supply/price/period)
   │  systemd timer, twice a day (08:00 / 20:00)
[Store] PostgreSQL · PBLANC_NO upsert (new-notice detection · dedup)
   ├──▶ [Notify] new & matching → Telegram (duplicate sends blocked)
   └──▶ [Web] FastAPI + Jinja (login) ──▶ Caddy (Let's Encrypt HTTPS)
```

## Data sources (official open APIs)

| Service | Endpoint | Provides |
|--------|-----------|------|
| ApplyHome sale notices | `api.odcloud.kr/.../getAPTLttotPblancDetail` | Notices, schedules, regulations |
| ApplyHome unit types | `.../getAPTLttotPblancMdl` | Area, sale price, special-supply unit counts |
| LH notice list | `apis.data.go.kr/B552555/lhLeaseNoticeInfo1` | Notices, regions, deadlines |
| LH notice detail | `.../lhLeaseNoticeDtlInfo1` | Detailed address, schedule, document submission, full notice text |
| LH supply info | `.../lhLeaseNoticeSplInfo1` | Area, unit counts |

> LH sale prices are not provided by the API ("see the official notice") — area,
> units, schedules, and addresses are.

## Features

- **Collect & normalize**: matches my criteria (`config/filters.yaml`); notices past
  their application deadline are excluded.
- **Notify**: new matching notices go to Telegram. Each notice fires exactly once (dedup).
- **Dashboard** (Karrot-style, session login): list sorted by deadline with D-day;
  click a title → **detail page** (per-unit-type recruitment, special-supply unit
  counts, schedule, regulations, detailed address + **Kakao map · V-World parcel polygon**).
- **Hands-off operation**: twice-daily batch + DuckDNS IP auto-refresh (every 30 min).

## Development

```bash
cp .env.example .env        # fill in the values (never commit)
docker compose up -d db     # PostgreSQL
python3.13 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
./.venv/bin/pytest -q       # tests
./.venv/bin/python -m src.pipeline --no-notify   # one batch run (no notifications)
./.venv/bin/python -m uvicorn src.web.app:app    # web
```

> If port 5432 is taken locally (an SSH tunnel, etc.), adjust `DB_HOST_PORT` in
> `.env` (e.g. 55432).

## Secrets

- Local: every secret lives in `.env` (blocked by `.gitignore`). `.env.example`
  holds placeholders only.
  - `ODCLOUD_API_KEY`, `TG_BOT_TOKEN`/`TG_CHAT_ID`, `POSTGRES_PASSWORD`
  - Web: `WEB_USER`/`WEB_PASSWORD` (login), `SESSION_SECRET` (session signing),
    `SESSION_HTTPS_ONLY` (true in production)
  - Maps: `KAKAO_JS_KEY` (domain-restricted), `VWORLD_KEY` (parcel polygons)
- CI/CD: **GitHub Actions Secrets** — the above plus deploy credentials
  `PI_HOST`/`PI_USER`/`PI_PORT`/`PI_SSH_KEY`.

## Deploy / CI-CD

Local dev → **branch → PR** → merge to main → **auto-deploy**.

- **CI**: ruff + pytest on every push (merge gate for main)
- **CD**: merge to main → GitHub Actions SSHes into the Pi, pulls, restarts the web service
- **Ops (Pi)**: systemd `chungyak-collect.timer` (batch) · `chungyak-web.service` (web)
  · `duckdns.timer` (IP refresh), Caddy (HTTPS)
- **main protection**: PR required · CI must pass · force push/deletion blocked

## Status

- [x] Collection (ApplyHome + LH) · storage (new-notice detection) · normalization (filters + periods)
- [x] Notifications (Telegram) · web dashboard · detail pages
- [x] Deployment (Pi systemd) · HTTPS (Caddy + Let's Encrypt) · CI/CD · DNS auto-refresh
