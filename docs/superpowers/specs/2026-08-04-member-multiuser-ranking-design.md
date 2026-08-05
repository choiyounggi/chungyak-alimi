# 회원(멀티유저) 프로필 기반 맞춤 청약 순위 — 설계

- 날짜: 2026-08-04
- 상태: 승인됨 (설계 확정, 구현 계획 대기)
- 관련: `src/scoring.py`, `src/db.py`, `src/web/app.py`, `config/profile.yaml`, `config/filters.yaml`

## 1. 배경 / 현재 상태

- 앱은 단일 사용자 전제. 순위 판정은 단일 `config/profile.yaml`(`load_profile`)을
  배치가 읽어 `match_result`(pblanc_no별 `matched`, `fail_reasons`, `my_rank`)에 저장하고,
  웹은 그 값을 뱃지·정렬로만 소비한다.
- 인증은 선택적 basic-auth 하나(`settings.web_user`/`web_password`)뿐 — 계정 개념 없음.
- 북마크는 전역(`bookmark` 테이블, pblanc_no 단독 PK).
- `judge_rank`(민영 1·2순위)는 **통장 가입기간 + 예치금 + 규제지역(세대주/당첨이력)** 만 본다.
  **거주지역 vs 공고지역(해당지역/기타지역)** 개념도, **소득 본거지** 개념도 없다.
- `Profile.region`은 거주 시/도 단일 필드 1개. 예비신혼부부 두 사람의 각자 주거지/소득지를 담을 곳이 없다.

## 2. 목표 / 비목표

### 목표
- 정식 회원가입/로그인(멀티유저) 도입 — 다른 사람도 사용 가능.
- 회원별 리치 프로필을 DB에 저장하고, **로그인 회원 기준**으로 맞춤 순위/매칭을 보여준다.
- 예비신혼부부의 **거주지 ∪ 소득 본거지**가 공고 지역과 매칭되면 **해당지역 1순위**로 인정.
- 지역은 전체 오픈. 회원이 가입 시 설정한 관심 지역으로 기본 필터, "전체 보기"로 다른 지역도 열람.
- 북마크는 회원별.

### 비목표 (이번 스펙 제외)
- 회원별 공고 *필터 취향* 전반(가격·주택유형 등) — 지역 필터만 회원별, 나머지 필터는 전역 유지.
- 이메일 인증/비밀번호 재설정 메일 발송, 소셜 로그인, 권한(admin) 체계.
- 회원×공고 순위 사전계산 배치(온더플라이 계산으로 대체).

## 3. 도메인 정책 주의 (중요)

실제 청약 제도에서 "해당지역 우선공급"은 **주민등록상 거주지** 기준이며, "소득 본거지"는
표준 해당지역 요건이 아니다. 본 스펙의 규칙 — *"거주지 또는 소득 본거지 지역이 공고 지역과
매칭되면 1순위로 인정"* — 은 **사용자(영기)가 지정한 정책**이다. 공식 규칙 재현이 아니라
사용자 정책으로 구현하며, 관련 코드/문서에 그 취지를 명시한다.

## 4. 확정된 설계 결정

| # | 결정 | 값 |
|---|------|-----|
| 1 | 순위 계산 방식 | **요청 시점 온더플라이**(scoring 순수함수) — 항상 최신, 배치 폭발 없음 |
| 2 | 지역 매칭 단위 | **시/도 기준 + 시 단위 예외 매핑**(성남 등) |
| 3 | 인증 | basic-auth 제거, **회원 계정으로 대체** |
| 4 | 북마크 | **회원별**로 마이그레이션 |
| 5 | 지역 필터 | **회원별** — 가입 시 관심 지역 설정, "전체 보기" 토글로 타 지역 열람 |

## 5. 데이터 모델 (Phase 0)

### `member`
| 컬럼 | 타입 | 비고 |
|------|------|------|
| id | int PK (autoincrement) | |
| email | String unique not null | 로그인 ID |
| password_hash | String not null | bcrypt |
| created_at | DateTime server_default now() | |

### `member_profile` (member와 1:1, `member_id` FK+unique)
기존 `scoring.Profile` 전 필드 + 신규 필드. 저장은 정규 컬럼 우선, 값 목록/중첩은 필요한 만큼 컬럼화.

- 기존 이전: `birth_date`, `marriage_date`, `engaged`, `is_household_head`,
  `household_all_homeless`, `homeless_since`, `dependents`, `won_within_5y`, `children_minor`,
  `real_estate_manwon`, 통장(`account_opened`, `account_balance_manwon`),
  소득(`income_monthly_manwon`, `income_base_manwon`, `income_dual`),
  생애최초(`fl_ever_owned_house`, `fl_income_tax_5y`, `fl_currently_earning`)
- **신규**:
  - `car_value_manwon` (차량가액, int)
  - `household_head_owns_home` (현재 같이 사는 세대의 세대주가 자가인지, bool)
  - `household_type` (enum: `newlywed`/`pre_newlywed`/`youth`/`general`)
  - `is_first_home` (생애 첫집 여부, bool) — `fl_*`와 함께 정합성 유지
  - `residence_regions` (각자 주거지 목록; 신혼/예비신혼이면 2인, JSON/ARRAY[str])
  - `income_base_regions` (각자 소득 본거지 목록; JSON/ARRAY[str])
  - `interest_regions` (관심 지역 목록 = 기본 지역 필터; JSON/ARRAY[str])

### `bookmark` 변경
- `member_id` 컬럼 추가 → PK = (`member_id`, `pblanc_no`).
- 기존 전역 북마크 데이터는 영기 계정으로 마이그레이션(단일 사용자 이관).

### 어댑터
- `member_profile` 행 → `scoring.Profile` 로 변환하는 함수(`profile_from_member(row)`).
  거주지/소득본거지는 신규 순위 로직이 직접 사용(§7).

## 6. 인증 (Phase 1)

- 라우트: `GET/POST /register`, `GET/POST /login`, `POST /logout`.
- bcrypt 해싱(passlib 또는 bcrypt). 세션 로그인(기존 SessionMiddleware/`SESSION_SECRET` 활용).
- 회원 전용 페이지(대시보드/북마크/프로필) 보호: 미로그인 → `/login` 리다이렉트.
- 기존 basic-auth 경로 제거 및 관련 설정 정리.
- **기존 단일 사용자 이관**: `config/profile.yaml` → 영기 계정(`member` + `member_profile`) 시드
  스크립트/마이그레이션 제공. 배포 시 1회 실행.
- 경계/에러: 중복 이메일 가입(409), 잘못된 로그인(401/폼 에러), 빈 입력 검증,
  비로그인 접근(리다이렉트), 로그아웃 후 세션 무효화.

## 7. 순위 로직 (Phase 2) — Phase 0 후 Phase 1과 병렬 가능

- `judge_rank`(또는 신규 헬퍼)에 **해당지역 판정** 추가:
  - 회원의 `residence_regions ∪ income_base_regions` 집합을 구성.
  - `notice.area_nm`(공고 지역)이 이 집합 중 하나와 **지역 매칭(§8)** 되면 "해당지역"으로 간주.
  - 해당지역이면 기존 1·2순위 판정 결과에 "해당지역 1순위" 자격을 부여(비해당지역은 기타지역).
- 세대유형이 신혼/예비신혼일 때 두 사람 지역을 모두 고려. 그 외 유형은 본인 거주지만.
- **정책 표기**: 소득 본거지 기반 인정은 사용자 지정 정책임을 docstring/주석에 명시.
- 순수함수 유지 — 기존 `judge_notice`/`score_points` 등과 동일하게 today 주입, DB 비의존.

## 8. 지역 매칭 규칙

- 1차: 시/도 단위 정규화 후 비교(예: "서울특별시"↔"서울", "경기도"↔"경기").
- 2차: 시 단위 예외 매핑 테이블(예: "성남" → 경기 소속이지만 소득본거지 정책상 별도 매칭 허용).
- `notice.area_nm`의 실제 값 분포를 Phase 0에서 표본 조사해 정규화 규칙을 확정하고 표로 문서화.
- 경계값: 빈 값/미상 지역 → 매칭 실패(기타지역)로 안전 처리.

## 9. 회원별 대시보드 & 입력 폼 (Phase 3) — Phase 1·2 후

- **프로필 입력/수정 폼**: `member_profile` 전 필드. 유형(신혼/예비신혼)에 따라 2인 지역 입력 동적 표시.
- **대시보드**: 로그인 회원 프로필로 **요청 시점 순위 계산**. 기본 필터 = `interest_regions`.
  "전체 보기" 토글 시 타 지역 포함.
- **회원별 북마크**: 현재 회원 `member_id` 기준으로 PUT/DELETE/목록.
- **1순위 필터 칩**: 회원 기준 `my_rank`로 필터.
- 지도/뷰포트·칩 필터 기존 동작 유지(회귀 금지).

## 10. 수집 파이프라인 점검

- 지역 전체 오픈에 맞춰 수집기가 **전 지역을 커버**하는지 Phase 0에서 점검.
  현재 수집 범위가 특정 지역/필터에 묶여 있으면 보강 항목으로 별도 기록(이번 스펙 밖일 수 있음).

## 11. 단계 & 의존성

```
Phase 0 (데이터 모델)  →  Phase 1 (인증)  ┐
                       →  Phase 2 (순위) ┘ →  Phase 3 (대시보드/폼)
```
- Phase 0이 나머지 전부의 선행. Phase 1과 2는 서로 독립 → 병렬 구현 가능(파일 범위 분리:
  1은 auth 라우트/`member`, 2는 `scoring.py`/순위 헬퍼).

## 12. 테스트 전략 (각 Phase TDD)

- 정상 + 에러 + 경계값 필수(파일당 ≥3 케이스, 에러/경계 각 ≥1).
- Phase 0: 모델 CRUD, 어댑터 변환, 북마크 마이그레이션 멱등.
- Phase 1: 가입/로그인/로그아웃, 중복 이메일, 잘못된 로그인, 비로그인 리다이렉트.
- Phase 2: 거주지 매칭 1순위, 소득본거지 매칭 1순위, 둘 다 불일치→기타지역, 빈/미상 지역 경계.
- Phase 3: 회원별 대시보드 순위/지역필터, "전체 보기" 토글, 회원별 북마크 격리(타회원 북마크 비노출).
- DB 필요한 테스트는 기존 `_db_available` 게이트 패턴 준수.

## 13. 위험 / 열린 질문

- 지역 매칭 정규화는 `notice.area_nm` 실제 값에 의존 → Phase 0 표본 조사 후 확정.
- 멀티유저 도입에 따른 기존 전역 북마크/프로필 이관은 1회성 마이그레이션으로 처리, 배포 순서 주의
  (웹이 배치보다 먼저 뜨는 배포에서도 스키마 준비되도록 `init_db` 멱등 유지).
- 비밀번호 재설정/이메일 인증 부재는 의도적(비목표). 필요 시 후속 스펙.
