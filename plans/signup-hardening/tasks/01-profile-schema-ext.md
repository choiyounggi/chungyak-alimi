# Task 01: 프로필 스키마 확장 + scoring.Profile 필드 + 어댑터

## Objective
온보딩에서 새로 받을 값(자차보유·납입횟수·지역별 거주기간·선호전형 복수선택·
예비신혼 파트너·온보딩 진행상태)을 `member_profile` 에 저장할 수 있게 하고,
`scoring.Profile` 이 그 값을 실어 나르게 한다. 기존 배포 DB에 멱등하게 반영된다.

## Wiki pages (read these first, only these)
- databases/schema-design/column-data-types.md — JSONB vs 별도 테이블, 날짜/정수 타입
- databases/schema-design/nullability-and-defaults.md — NOT NULL + server_default
- backend/python/boundaries/runtime-validation.md — pydantic 중첩 모델 경계

## Inputs
- 기존 `src/db.py` `MemberProfile`(197~250행), `init_db()`의 경량 ALTER 블록(255행~).
- 기존 `src/members.py` `_UPDATABLE` / `_LIST_FIELDS` / `profile_from_member`.
- 기존 `src/scoring.py` `Profile` / `AccountInfo` / `IncomeInfo` / `FirstLifeInfo`.
- 바인딩 결정: D1(신규 컬럼), D2(거주기간 표현), D3(residence_regions 파생 동기화),
  D4(선호전형), D5(파트너), D6(멱등 ALTER).

## Steps
1. `src/db.py` `MemberProfile` 에 컬럼 추가 — 전부 `nullable=False` + `server_default`:
   `owns_car` BOOL(false), `account_payment_count` INT(0),
   `residence_history` JSONB(`'[]'::jsonb`), `preferred_types` JSONB(`'[]'::jsonb`),
   `partners` JSONB(`'[]'::jsonb`), `onboarding_step` SMALLINT(0).
2. `init_db()` 의 기존 ALTER 목록에 `ALTER TABLE member_profile ADD COLUMN IF NOT EXISTS …
   NOT NULL DEFAULT …` 6줄 추가(멱등). 기존 `bookmark`/`notice` ALTER 는 건드리지 않는다.
3. `src/scoring.py` 에 `ResidencePeriod` / `PartnerInfo` 모델을 추가하고 `Profile` 에
   `owns_car` / `account_payment_count` / `residence_history` / `preferred_types` /
   `partners` 필드를 추가한다. **판정 함수(`judge_*`, `score_points`)는 절대 수정하지 않는다**
   — Task 03의 범위다.
4. `src/members.py`:
   - `_UPDATABLE` 에 신규 6개 컬럼 추가.
   - `update_profile` 이 `residence_history` 를 받으면 `residence_regions` 를
     `[h["region"] for h in residence_history if h.get("region")]` 로 **파생 동기화**한다(D3).
     호출자가 `residence_regions` 를 직접 준 경우와의 우선순위를 docstring에 명시.
   - `profile_from_member` 가 신규 필드를 `Profile` 로 옮긴다(JSONB dict → 중첩 모델 변환).
5. `preferred_types` 허용값 집합 상수(`PREFERRED_TYPES`)를 `src/scoring.py` 에 두고,
   `update_profile` 은 허용값만 남기고 걸러낸다(경계 방어).

## Deliverables
- `src/db.py` (컬럼 + init_db ALTER)
- `src/scoring.py` (Profile/ResidencePeriod/PartnerInfo/PREFERRED_TYPES — 모델 한정)
- `src/members.py` (_UPDATABLE, update_profile 파생 동기화, profile_from_member)
- `tests/test_profile_schema_ext.py` (신규)

## Verify
- `uv run pytest tests/test_profile_schema_ext.py tests/test_members.py tests/test_member_schema.py -q 2>&1 | tail -20` 통과.
- `uv run ruff check src tests 2>&1 | tail -5` clean.
- 테스트(`_db_available` 게이트): ① 정상 — `update_profile` 로 residence_history/
  preferred_types/partners 저장 후 재조회 값 일치, `residence_regions` 가 파생 동기화됨
  ② 에러 — `preferred_types` 에 허용 외 값(`"foo"`)이 오면 걸러져 저장되지 않음
  ③ 경계 — 빈 리스트/누락 키(`{"region": "서울"}` 만 있고 `since` 없음) 저장 시 예외 없이
  `since=None` 으로 왕복, `onboarding_step` 기본값 0
  ④ 멱등 — `init_db()` 를 두 번 호출해도 실패하지 않음.

## Out of scope
- 순위 판정 로직(Task 03), 온보딩 폼/라우트(Task 05), 대시보드 표시(Task 06).
