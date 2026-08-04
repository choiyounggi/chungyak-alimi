# multi-source-collectors — 공고 수집원 6개 확장 (LH·청약홈 + 마이홈·HUG·SH·GH)

Goal: 현재 청약홈·LH 2개인 수집원에 **마이홈포털·HUG 든든전세·SH·GH** 4개를 추가해
공고 커버리지를 LH/SH/GH/HUG/기타 전 기관으로 넓히고, **임대 공고까지** 필터·알림·
대시보드에 태운다. 기관 간 공고번호 충돌을 막기 위해 `notice.pblanc_no`를
`"<source>:<native_id>"` 글로벌 ID로 전환한다.

Acceptance criteria:
- `notice.pblanc_no`가 전부 `<source>:<native>` 형태이고, 자식 테이블 4개
  (`notice_house_type`·`match_result`·`notify_log`·`bookmark`)의 참조가 함께 이관된다.
  기존 북마크가 유지된다.
- 4개 신규 collector가 각각 독립 실행 가능하고, 하나가 실패해도 배치 전체가 중단되지 않는다.
- 임대 공고가 필터를 통과한다(현재는 `exclude_keywords`의 `영구임대`·`국민임대`가 전량 탈락시킴).
- 대시보드에 기관 필터 칩(전체/LH/SH/GH/HUG/기타)과 카드 기관 배지가 뜬다.
- 기존 179개 테스트 전부 통과 + 신규 테스트 통과, `ruff` 클린.

Stack: Python 3.13 / httpx + pydantic v2 / SQLAlchemy 2 + PostgreSQL(JSONB) /
FastAPI + Jinja2(서버 렌더, SPA 아님) / pytest. HTML 파싱은 **표준 라이브러리
`re` + `html.parser`만** 사용(신규 의존성 추가 없음 — D13).

## 검증된 소스 사실 (2026-08-04 실측)

| 소스 | 엔드포인트 | 실측 |
|---|---|---|
| 마이홈 임대 | `https://apis.data.go.kr/1613000/HWSPR02/rsdtRcritNtcList` | 372건, 32필드, `pnu`·`rentGtn`·`mtRntchrg`·`beforePblancId` 제공 |
| 마이홈 분양 | `.../HWSPR02/ltRsdtRcritNtcList` | 64건, 27필드(임대료 필드 없음) |
| HUG | `https://www.khug.or.kr/SelectListInfo.do?API_KEY=` | 300행 = **공고 1건의 물건 목록**, 9필드, 페이징 불가 |
| SH | `https://housing.seoul.go.kr/site/main/sh/publicLease/list` (+`/publicSale/01/list`) | 서버렌더 `<table>`, robots `Allow: /` |
| GH | `https://apply.gh.or.kr/co/coa/selectMainView.do` | 서버렌더, robots `Allow: /*`, NetFunnel 현재 주석 비활성 |

**절대 접근 금지** (robots.txt `Disallow: /`): `www.i-sh.co.kr`, `www.gh.or.kr`.

## Decisions

| # | Decision | Choice | Wiki basis |
|---|----------|--------|------------|
| D1 | 기관 간 ID 충돌 방지 | `notice.pblanc_no`를 **`f"{source}:{native_id}"` 글로벌 ID**로 전환. 복합 PK(`source`,`pblanc_no`)가 아니라 단일 문자열 PK 유지 — 자식 테이블 4개가 모두 `pblanc_no` 단일 컬럼 PK/참조라 컬럼 구조 변경 없이 값만 이관하면 됨 | databases-schema-design-primary-key-choice |
| D2 | 원본 ID 보존 | `notice.native_id` 컬럼(nullable TEXT) 신설. LH 상세/공급 보강이 `PAN_ID`를 그대로 필요로 하므로(`pipeline.py:57,125`) 글로벌 ID에서 파싱하지 않고 컬럼에서 읽는다 | databases-schema-design-requirements-to-tables `[no-wiki 보조]` |
| D3 | 신규 컬럼 추가 방식 | 4개 컬럼(`native_id`,`agency`,`rent_gtn`,`mt_rntchrg`) 모두 **nullable, 기본값 없음** → Postgres 11+ 메타데이터 전용, 테이블 재작성 없음. 기존 `init_db()`의 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 경량 마이그레이션 패턴(`db.py:146~148`)을 그대로 확장 | databases-schema-design-online-schema-changes, databases-schema-design-nullability-and-defaults |
| D4 | 임대보증금·월임대료 타입 | **`BigInteger`, 단위는 원(KRW 최소단위)**. 부동소수 금지. 기존 `lttot_top_amount`(만원 Integer)는 건드리지 않음 — 분양가와 임대료는 별개 축 | databases-schema-design-column-data-types |
| D5 | ID 이관 시점 | `init_db()` 안에서 `WHERE pblanc_no NOT LIKE '%:%'` 가드로 **멱등 실행**. 별도 수동 스텝이면 CD 자동배포 후 다음 배치 전에 잊힐 수 있고, 그 사이 배치가 돌면 접두/무접두 ID가 공존해 중복 행이 생긴다. 검증용 `scripts/migrate_global_id.py`는 같은 함수를 호출해 건수를 출력만 한다 | databases-schema-design-online-schema-changes (expand 단계는 즉시, backfill은 소규모 단일 트랜잭션 — 전체 수백 행 규모이므로 배치 분할 불필요) |
| D6 | 이관 순서 | 자식 4개 테이블을 **먼저** (`notice`가 아직 native ID인 상태에서 조인) 갱신하고, 마지막에 `notice`를 갱신. 역순이면 조인 키가 사라진다. 매칭 안 된 고아 행은 그대로 두고 건수를 로그 | `[no-wiki]` (조인 순서 산술) |
| D7 | 공급기관 축 | `source`(수집원: applyhome/lh/myhome/hug/sh/gh)와 **별개로** `notice.agency`(공급기관: `LH`/`SH`/`GH`/`HUG`/`기타`) 신설. 마이홈은 source=myhome이지만 agency는 행마다 LH/지방공사로 갈리므로 둘을 겸할 수 없다. 값 집합은 **닫힌 5종**, `TEXT` + 코드 상수(DB CHECK 제약은 두지 않음 — 단일 인스턴스 개인 서비스) | databases-schema-design-column-data-types (닫힌 소집합은 TEXT+제약) |
| D8 | agency 매핑 | applyhome→`기타`, lh→`LH`, hug→`HUG`, sh→`SH`, gh→`GH`, myhome→`suplyInsttNm == "LH"`면 `LH` 아니면 `기타` | `[no-wiki]` |
| D9 | 외부 응답 검증 | 4개 collector 모두 **pydantic 모델로 파싱한 뒤에만 필드 접근**. 사용하는 필드만 선언하고, 행 단위 `ValidationError`는 `logger.warning` 후 그 행만 스킵(배치 중단 금지) — 기존 `lh.py:354~357` 패턴과 동일 | backend-python-boundaries-runtime-validation |
| D10 | 외부 호출 타임아웃 | 모든 `httpx.Client`에 **`timeout=30.0` 명시**(기존 collector와 동일). **재시도는 넣지 않는다** — 배치가 하루 2회 돌고 다음 회차가 자연 재시도이며, 재시도 예산·백오프를 도입할 만한 실패율 근거가 없다. 실패는 `_safe()`가 소스 단위로 격리 | backend-common-reliability-timeouts-and-retries (재시도는 근거 있을 때만; 타임아웃은 항상) |
| D11 | 마이홈 페이징 | `numOfRows=100` 고정, `totalCount` 도달 또는 `pageNo>20`에서 중단. 일일 트래픽 1000콜/오퍼레이션이므로 회차당 임대 4콜+분양 1콜로 충분 | backend-common-api-design-pagination-contract `[부분]` |
| D12 | HUG 데이터 모델 | 300행을 **Notice 1건으로 집계**. `COLL_ANNO_DT`(공고일자)가 공고 식별자 → `native_id = COLL_ANNO_DT`. 개별 물건 목록은 `raw["_hug_items"]`에 보관. 물건별 식별자·번지가 없어 개별 행의 PK를 만들 수 없다(실측 확인) | `[no-wiki]` (소스 제약에서 유도) |
| D29 | HUG 집계 단위 | D12를 구체화 — **1 Notice = (공고일자 × 시도)**. `native_id = f"{COLL_ANNO_DT}-{AREA_DCD_NM}"`. 공고 1건으로 뭉치면 서울·인천·경기·부산이 한 `area_nm`에 눌려 지역 필터가 무의미해진다(실측 분포 서울118/인천116/경기62/부산4). `tot_suply_hshldco`는 해당 시도 물건 수, `rent_gtn`은 해당 시도 **최소** 보증금 — 기존 분양가 판정("하나라도 상한 이하면 통과")과 같은 정신 | `[no-wiki]` (실측 분포에서 유도) |
| D30 | HUG 300건 상한 | 응답이 정확히 300행이면 `logger.warning("HUG 응답이 상한(300)에 걸렸을 수 있음 — 일부 물건 누락 가능")`. 페이징 파라미터 7종이 모두 무시됨을 실측 확인했으므로 우회 시도는 하지 않는다 | backend-common-integrations-externally-owned-defaults |
| D13 | HTML 파싱 라이브러리 | 신규 의존성(bs4/lxml) **추가하지 않음**. 표준 `re` + `html.unescape`로 `<tr>`/`<td>` 추출. 두 사이트 모두 단순 테이블/목록이고, 의존성 추가는 공급망 검토 비용이 이득보다 크다 | security-dependencies-supply-chain (add-vs-write) |
| D14 | 스크래핑 결과의 신뢰 경계 | SH/GH가 준 문자열은 **외부 입력**으로 취급: `html.unescape` 후 태그 제거, 길이 상한(`house_nm` 200자, URL 500자), URL은 `https://` 스킴 + 해당 호스트만 허용(기존 `_safe_url` 패턴 확장). 저장 후 Jinja2 자동이스케이프가 출력 방어 | security-input-validation-at-trust-boundaries, frontend-security-xss-safe-rendering |
| D15 | GH NetFunnel 대응 | 응답 HTML에 `NetFunnel_Action(` **호출문이 주석 아닌 형태로** 존재하면 대기열이 켜진 것으로 보고 `logger.warning` 후 **빈 리스트 반환(우회 금지)**. `_safe()`가 이를 소스 실패로 격리 | backend-common-integrations-externally-owned-defaults (레포가 소유하지 않은 외부 기본값이 말없이 바뀌는 경로) |
| D16 | SH/GH 접수기간 | 목록 페이지에 접수 시작/종료가 없다(실측). **상세 크롤링은 스코프 제외** → `rcept_bgnde`/`rcept_endde`는 `None`. SH는 `모집상태 == "모집중"` 행만 수집해 `only_open`을 대체. GH는 상태 컬럼이 없으므로 전량 수집 | `[no-wiki]` (소스 제약) |
| D17 | 지역 정규화 | 신규 collector는 `from .lh import normalize_region`으로 기존 `REGION_MAP`을 재사용. 공용 모듈 추출은 하지 않음 — 지금 필요 없는 리팩터링이고 `lh.py`를 건드리지 않는 편이 안전 | `[no-wiki]` (최소 변경) |
| D18 | 임대 필터 정책 | `exclude_keywords`에서 `영구임대`·`국민임대` **제거**(연령제한인 `고령자`·`실버`만 유지). 임대 상한은 신규 `rent_deposit_max_manwon`(만원, null=무제한)로 `notice.rent_gtn` 대비 판정. `price_max_manwon`은 분양가 정보가 있을 때만 적용되는 기존 동작을 그대로 유지 | `[no-wiki]` (사용자 정책 결정: "임대까지") |
| D19 | 기관 필터 | `filters.yaml`에 `agencies: []`(빈 배열=전체) 추가. `notice.agency`가 목록에 없으면 탈락 사유 `기관:<agency>` | `[no-wiki]` |
| D20 | 순위 판정 소스 분기 | `scoring.py:242~244`의 `source == "lh"` 판정 불가 분기를 **`source != "applyhome"`**로 일반화. 신규 4소스 전부 공공·임대라 청약 순위 판정 대상이 아니다 | `[no-wiki]` |
| D21 | 대시보드 기관 필터 | 기존 다중선택 칩(특별공급) 구조를 그대로 재사용해 기관 칩 행을 추가. 카드 `data-agency` 속성 + 기존 JS 토글에 편입 — 서버 재조회 없음(레이스 없음) | frontend-data-fetching-race-conditions (재조회 안 함으로 레이스 제거), frontend-security-xss-safe-rendering |
| D22 | 첫 배포 알림 폭주 방지 | 신규 소스 첫 배치는 반드시 `python -m src.pipeline --backfill`로 실행(기존 `backfill_notified()` 재사용). 마이홈 372건이 한 번에 텔레그램으로 나가는 것을 막는다 | backend-common-jobs-idempotent-handlers `[부분]` |
| D23 | 테스트에서의 외부 호출 | 실제 네트워크 호출 **금지**. 각 collector 테스트는 실측 응답을 축약한 고정 픽스처를 `httpx.MockTransport`로 주입(기존 `tests/test_lh.py` 패턴). 소유하지 않은 외부 API는 페이크로 대체 | testing-mocking-what-to-mock, testing-data-test-data-and-isolation |
| D24 | 테스트 최소 케이스 | collector 테스트마다 **정상 1 + 파싱실패행 스킵 1 + 빈응답/경계 1** 이상. 스키마 태스크는 **이관 전/후 왕복 + 멱등 재실행 + 고아행** 케이스 포함 | testing-quality-minimum-case-set |
| D25 | 마이홈 정정공고 대체 | `find_superseded()`의 그룹핑 조건을 `source == "applyhome"`에서 **`n.house_manage_no`가 있으면 주택관리번호 그룹**으로 일반화. 마이홈 collector가 `house_manage_no = beforePblancId or pblancId`를 실으면 원공고·정정공고가 같은 그룹에 묶여 기존 최신판정 로직이 그대로 동작한다. LH는 `house_manage_no`가 항상 None이라 기존 이름 기반 경로 그대로 — 회귀 없음 | `[no-wiki]` (기존 기계장치 재사용) |
| D27 | 마이홈 행 단위 | 응답 1행 = **공고×단지**(`pblancId`+`houseSn`)이며 단지마다 주소·PNU·보증금이 다르다(실측: 동일 `pblancId`가 `houseSn` 1~7로 7행). 따라서 **1행 = 1 Notice**, `native_id = f"{pblancId}-{houseSn}"`. D25의 그룹 키도 반드시 `house_manage_no = f"{beforePblancId or pblancId}-{houseSn}"`로 **houseSn을 포함**해야 한다 — 포함하지 않으면 같은 공고의 형제 단지 6건이 서로를 "정정으로 대체"해 사라진다. 2단계 이상 정정 체인(A→B→C)은 스코프 제외(끊긴 체인은 중복 카드 1건으로 드러남) | `[no-wiki]` (실측 데이터 구조에서 유도) |
| D28 | 마이홈 `pnu` 활용 | 이번 스코프에서는 `raw["pnu"]`에 **보관만** 한다. `enrich_polygons()`를 주소 기반에서 PNU 기반으로 바꾸는 것은 `collectors/vworld.py`의 시그니처 변경을 부르므로 후속 작업으로 분리 | `[no-wiki]` (최소 변경) |
| D26 | 신규 소스의 정정공고 표기 | 마이홈은 `sttusNm == "정정공고"`일 때 `house_nm` 앞에 기존 접두사 `[정정공고]`를 붙인다. D25의 그룹 내 최신 판정이 정정 횟수를 세는 데 쓰고, 카드 표기도 기존과 통일된다 | `[no-wiki]` |

## Task order

| Task | Depends on | Parallel-ok |
|------|-----------|-------------|
| 01-schema-global-id | — | |
| 02-filter-policy-rent | 01 | ✅ (03~06과 병렬) |
| 03-collector-myhome | 01 | ✅ |
| 04-collector-hug | 01 | ✅ |
| 05-collector-sh | 01 | ✅ |
| 06-collector-gh | 01 | ✅ |
| 07-pipeline-wiring | 02,03,04,05,06 | |
| 08-web-agency-filter | 07 | |

## Deploy runbook (구현 후)

0. **Pi DB 백업을 먼저 뜬다** — `pg_dump`. 로컬 개발 DB는 전 테이블 0행이라
   ID 이관(D5)이 **실데이터에 대해 한 번도 실행된 적이 없다**. 단위 테스트는 1행 규모만
   커버한다. 프로덕션이 첫 실행이므로 되돌릴 수단을 먼저 확보한다.
1. main 머지 → Pi 자동배포 → 웹 서비스 재시작 시 `init_db()`가 ID 이관 수행(D5).
2. 이관 결과를 눈으로 확인한다:
   `python scripts/migrate_global_id.py` → 2회차 출력이 전부 `0`이어야 한다.
   `select count(*) from notice where pblanc_no not like '%:%'` → `0`.
   북마크가 살아있는지 웹에서 확인한다(자식 4테이블 이관 검증).
3. `python -m src.pipeline --backfill` **1회 수동 실행**(D22) — 알림 없이 수집·매칭만.
   이걸 건너뛰면 마이홈 372건이 한 번에 텔레그램으로 나간다.
4. 이후 정규 타이머(08:00/20:00) 진입.

## 독립 리뷰 결과 (2026-08-04, 4개 관점 병렬)

**고쳐서 반영한 것 — 전부 실물로 재현해 확인:**

| 지적 | 측정 | 조치 |
|---|---|---|
| API 키가 로그에 평문 노출(security) | 카나리 키로 재현 성공 — `httpx.HTTPStatusError` 메시지가 쿼리스트링째 담기고 `_safe()` 의 `logger.exception()` 이 저널에 남긴다. 기존 `httpx` 로거 WARNING 설정은 이 경로를 못 막는다 | `pipeline.SecretRedactingFormatter` 신설(포맷 단계 마스킹 — Filter 는 traceback 을 못 건드림). 신규 HUG 키뿐 아니라 기존 LH·청약홈 키에도 적용 |
| `houseSn` falsy 강제 변환(correctness) | 라이브 109건 중 18건이 `houseSn=0`. `0 or ""` → `""` 로 뭉개져 `pblanc_no` 가 `"20960-"` 로 깨졌다 | `None` 체크로 교체. 첫 프로덕션 적재 **전에** 고쳐야 했다 — 나중에 고치면 같은 공고가 `-0` 으로 재유입돼 전량 중복된다 |
| `global_id` 콜론 판정(adversarial) | 원본 ID 에 `":"` 가 있으면 접두를 건너뛰어 서로 다른 소스가 같은 PK 로 충돌 | 판정을 `":" in native` → `native.startswith(f"{source}:")` 로 강화 |
| 기관값 소스별 저장 미검증(testing) | `applyhome` 의 `"기타"` 만 어서션돼 있었다 | LH/HUG/SH/GH 파라미터 테스트 + 임대료 컬럼 DB 왕복 테스트 추가 |

**측정으로 반증한 것:**

- *"자식 테이블 4개 UPDATE 중 실패 시 불일치 상태로 남는다"* → `migrate_global_ids()` 는 `with engine.begin()` **단일 트랜잭션**이라 전체 롤백된다.
- *"템플릿이 `agency=None` 을 문자열 'None' 으로 렌더한다"* → `{{ n.agency or '기타' }}` 로 이미 처리돼 있다.
- *"수백만 행 규모에서 `LIKE` 성능 문제"* → 개인 서비스 실제 규모(수백 행)에 해당하지 않는다.
- adversarial 은 `houseSn` 항목을 *"현재는 코드 올바름"* 으로 판정했으나 실제로는 깨져 있었다 — correctness 가 잡았다. 한 관점만 돌렸으면 놓쳤을 결함이다.

**고치지 않고 남긴 알려진 한계:**

- **HUG 300건 상한**(D30) — 페이징 파라미터 7종이 전부 무시됨을 실측했다. 상한 초과분은 조회 수단이 없어 `tot_suply_hshldco`·`rent_gtn` 이 부정확할 수 있다. 경고 로그가 유일한 대응.
- **collector 의 행 단위 스킵이 조용하다** — `ValidationError` 로 건너뛴 행 수가 배치 결과에 집계되지 않는다. API 스키마가 바뀌면 대량 누락이 "성공"으로 보고될 수 있다. 후속 과제.
- **목 기반 테스트의 네트워크 사각지대** — GH TLS 결함이 목 테스트 8개를 전부 통과한 실증이 있다. 주기적 라이브 스모크가 필요하다.

> 리뷰 진행 중 adversarial 에이전트가 `/private/tmp` 에 파일을 생성했다 — CLAUDE.md 의 보안 정책 위반. 산출물은 텍스트로만 수용했다.

## 계획 자체의 알려진 오차

- Task 01의 Deliverables를 3개 파일로 잡았으나 실제로는 9개가 필요했다 — 글로벌 ID
  전환이 기존 테스트의 ID 리터럴을 전부 깨뜨린다. 이후 태스크에도 같은 과소 산정이
  있을 수 있으므로, Deliverables 목록을 상한이 아니라 **최소 목록**으로 읽을 것.
- Task 01 계획서의 `LIKE '%:%'`는 틀렸다. `exec_driver_sql`은 psycopg paramstyle을
  통과시키므로 `'%%:%%'`로 escape해야 한다(구현 중 발견·수정됨).
