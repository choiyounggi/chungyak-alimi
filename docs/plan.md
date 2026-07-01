# 구현 계획 (요약)

전체 설계·스키마·Phase 상세는 아키텍처 노트를 따른다. 아래는 레포용 축약본.

## 스택
- 수집/정제: Python 3.13 + httpx + pydantic v2
- 저장: PostgreSQL 16 (docker) + SQLAlchemy 2
- 스케줄: systemd timer (하루 1~2회)
- 알림: 텔레그램 Bot API
- 웹: FastAPI + Jinja2 + HTMX
- 외부노출: Cloudflare Tunnel + Access(인증)

## 데이터 소스 (공식 오픈API)
| 서비스 | 엔드포인트 |
|--------|-----------|
| 청약홈 분양정보 | `api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail` |
| 청약홈 주택형별(분양가·면적) | `.../getAPTLttotPblancMdl` |
| 청약홈 경쟁률 | `.../ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet` |
| LH 공급정보 | `apis.data.go.kr/B552555/lhLeaseNoticeSplInfo1/getLeaseNoticeSplInfo1` |

## DB 스키마
- `notice` — 공고 (PK `pblanc_no`, 신규감지 `rcrit_pblanc_de`, 지역 `area_nm`, `raw jsonb`)
- `notice_house_type` — 주택형별 (면적 `suply_area`, 분양가 `suply_price`)
- `match_result` — 필터 매칭 결과
- `notify_log` — 알림 발송 이력 (중복발송 차단)

## Phase
0. 스캐폴딩 + DB  ·  1. 수집(청약홈)  ·  2. 저장(upsert·신규감지)  ·  3. 주택형/LH 확장
4. 정제(filters.yaml 매칭)  ·  5. 알림(텔레그램)  ·  6. 웹 대시보드  ·  7. 배포(systemd + Cloudflare Tunnel)  ·  8. 문서

## 배제 (오버엔지니어링)
Kafka·Redis·크롤링 우회·공인IP 직접노출 → 전부 미적용. 근거는 규모(하루 수십~수백 건·단일 소비자·비실시간).
