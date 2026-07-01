# 청약 알리미 (chungyak-alimi)

공공 오픈API(청약홈·LH) 기반으로 청약 정보를 수집·정제·저장하고, 내 조건에 맞는 신규 공고를
텔레그램으로 알리며 웹 대시보드로 조회하는 개인용 서비스. 라즈베리파이 4(헤드리스)에 배포한다.

> 봇차단 우회 크롤링이 아니라 **정부 공식 오픈API**를 사용한다. Kafka·Redis 없이 `cron(systemd timer) + PostgreSQL`.

## 아키텍처

```
[수집] 청약홈/LH 공식 API ──(httpx)──▶ [정제] pydantic 정규화 + filters.yaml 매칭
   └─ systemd timer (하루 1~2회) ─────┐
[저장] PostgreSQL · PBLANC_NO UNIQUE upsert(신규감지) ◀─┘
   ├──▶ [알림] 신규 & 매칭 → 텔레그램
   └──▶ [웹] FastAPI + Jinja + HTMX ──▶ Cloudflare Tunnel + Access(인증)
```

## 개발 환경

```bash
cp .env.example .env        # 값 채우기 (커밋 금지)
docker compose up -d db     # PostgreSQL 기동
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## 시크릿 관리 (중요)

- 로컬: 모든 시크릿은 `.env` 에만 둔다. `.gitignore` 로 커밋이 차단된다.
- CI/CD: **GitHub Actions Secrets** 로 주입한다. 레포에 키를 절대 커밋하지 않는다.
  - `ODCLOUD_API_KEY`, `TG_BOT_TOKEN`, `TG_CHAT_ID`, `POSTGRES_PASSWORD`
  - 배포용: `PI_HOST`, `PI_USER`, `PI_SSH_KEY` (Pi pull/배포 시)

## 배포 (Pi)

로컬 개발 → GitHub → Pi 가 pull. 상세는 `docs/plan.md` 및 `.github/workflows/` 참고.

## 상태

- [x] Phase 0 — 스캐폴딩 + DB
- [ ] Phase 1 — 수집(청약홈 분양정보)
- [ ] Phase 2 — 저장(upsert·신규감지)
- [ ] Phase 3 — 주택형/LH 확장
- [ ] Phase 4 — 정제(필터 매칭)
- [ ] Phase 5 — 알림(텔레그램)
- [ ] Phase 6 — 웹 대시보드
- [ ] Phase 7 — 배포(systemd + Cloudflare Tunnel)
- [ ] Phase 8 — 문서
