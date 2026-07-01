# 청약 알리미 (chungyak-alimi)

공공 오픈API(청약홈·LH)로 청약 정보를 수집·정제·저장하고, 내 조건에 맞는 신규 공고를
텔레그램으로 알리며 웹 대시보드로 조회하는 개인용 서비스. 라즈베리파이 4(헤드리스)에 배포·운영.

> 봇차단 우회 크롤링이 아니라 **정부 공식 오픈API**를 사용한다. Kafka·Redis 없이 `systemd timer + PostgreSQL`.

🌐 **https://chungyak.duckdns.org** (HTTPS · 로그인)

## 아키텍처

```
[수집] 청약홈(분양정보/주택형) + LH(공고/공급/상세) ──(httpx)
   │
[정제] pydantic 정규화 + filters.yaml 매칭(지역/유형/특공/분양가/기간)
   │  systemd timer, 하루 2회(08/20시)
[저장] PostgreSQL · PBLANC_NO upsert(신규감지·중복제거)
   ├──▶ [알림] 신규 & 매칭 → 텔레그램(중복발송 차단)
   └──▶ [웹] FastAPI + Jinja(로그인) ──▶ Caddy(Let's Encrypt HTTPS)
```

## 데이터 소스 (공식 오픈API)

| 서비스 | 엔드포인트 | 제공 |
|--------|-----------|------|
| 청약홈 분양정보 | `api.odcloud.kr/.../getAPTLttotPblancDetail` | 공고·일정·규제 |
| 청약홈 주택형 | `.../getAPTLttotPblancMdl` | 면적·분양가·특공별 세대 |
| LH 공고목록 | `apis.data.go.kr/B552555/lhLeaseNoticeInfo1` | 공고·지역·마감 |
| LH 상세정보 | `.../lhLeaseNoticeDtlInfo1` | 상세주소·일정·서류제출·공고전문 |
| LH 공급정보 | `.../lhLeaseNoticeSplInfo1` | 면적·세대수 |

> LH 분양가는 API 미제공("공고문 참조") — 면적·세대·일정·주소는 제공.

## 기능

- **수집·정제**: 관심 조건(`config/filters.yaml`) 매칭. 접수마감 지난 공고 제외.
- **알림**: 신규 매칭 공고를 텔레그램으로. 한 공고는 한 번만(dedup).
- **대시보드**(당근 스타일, 세션 로그인): 마감임박순 목록 + D-day. 제목 클릭 → **상세페이지**(주택형별 모집·특공별 세대·일정·규제·상세주소 + **카카오 지도·V-World 필지 폴리곤**).
- **자동 운영**: 하루 2회 배치 + DuckDNS IP 자동갱신(30분).

## 개발 환경

```bash
cp .env.example .env        # 값 채우기(커밋 금지)
docker compose up -d db     # PostgreSQL
python3.13 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
./.venv/bin/pytest -q       # 테스트
./.venv/bin/python -m src.pipeline --no-notify   # 배치 1회(알림 없이)
./.venv/bin/python -m uvicorn src.web.app:app    # 웹
```

> 로컬에서 5432가 SSH터널 등에 점유됐으면 `.env`의 `DB_HOST_PORT`(예: 55432)로 조정.

## 시크릿 관리

- 로컬: 모든 시크릿은 `.env`(`.gitignore`로 차단). `.env.example`엔 placeholder만.
  - `ODCLOUD_API_KEY`, `TG_BOT_TOKEN`/`TG_CHAT_ID`, `POSTGRES_PASSWORD`
  - 웹: `WEB_USER`/`WEB_PASSWORD`(로그인), `SESSION_SECRET`(세션 서명), `SESSION_HTTPS_ONLY`(프로덕션 true)
  - 지도: `KAKAO_JS_KEY`(도메인 제한), `VWORLD_KEY`(필지 폴리곤)
- CI/CD: **GitHub Actions Secrets** — 위 값 + 배포용 `PI_HOST`/`PI_USER`/`PI_PORT`/`PI_SSH_KEY`.

## 배포 / CICD

로컬 개발 → **브랜치 → PR** → main 머지 → **자동 배포**.

- **CI**: push마다 ruff + pytest (main 머지 게이트)
- **CD**: main 머지 → GitHub Actions가 Pi에 SSH pull + web 재시작
- **운영(Pi)**: systemd `chungyak-collect.timer`(배치)·`chungyak-web.service`(웹)·`duckdns.timer`(IP갱신), Caddy(HTTPS)
- **main 보호**: PR 필수·CI 통과 필수·force push/삭제 금지

## 상태

- [x] 수집(청약홈+LH) · 저장(신규감지) · 정제(필터+기간)
- [x] 알림(텔레그램) · 웹 대시보드 · 상세페이지
- [x] 배포(Pi systemd) · HTTPS(Caddy+Let's Encrypt) · CICD · DNS 자동갱신
