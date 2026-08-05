# Task 02: member 데이터액세스 + Profile 어댑터 (src/members.py)

## Objective
`src/members.py`가 회원/프로필 조회·생성·수정 함수와, `MemberProfile` 행을
`scoring.Profile`로 변환하는 `profile_from_member(profile_row)` 어댑터를 제공한다.
순위 로직(Task 09)과 대시보드(Task 11)가 이 어댑터로 회원 프로필을 scoring에 넘긴다.

## Wiki pages (read these first, only these)
- wiki/backend/python/boundaries/runtime-validation.md — DB행→도메인모델 변환 시 타입/누락 처리

## Inputs
- Task 01 산출물: `src/db.py`의 `Member`, `MemberProfile`, `SessionLocal`.
- `src/scoring.py`: `Profile`, `AccountInfo`, `IncomeInfo`, `FirstLifeInfo`(생성자 필드는 scoring.py 참조).
- 바인딩 결정: D2(email 소문자 정규화), D16(경계 검증).

## Steps
1. `src/members.py` 생성. `from .db import Member, MemberProfile, SessionLocal` / `from .scoring import Profile, AccountInfo, IncomeInfo, FirstLifeInfo`.
2. `get_member_by_email(email, *, session)` — email을 `.strip().lower()`로 정규화 후 조회, 없으면 None.
3. `create_member(email, password_hash, *, session) -> Member` — email 소문자 저장, 빈 MemberProfile 1건 동반 생성(member_id 연결). 이미 있으면 `ValueError`(중복은 라우트에서 409로 매핑 — Task 05).
4. `get_profile(member_id, *, session) -> MemberProfile | None`.
5. `update_profile(member_id, values: dict, *, session)` — 허용 컬럼만 반영(화이트리스트), 지역 목록 필드는 list[str]로 강제.
6. `profile_from_member(row: MemberProfile) -> Profile` — 컬럼→Profile 필드 매핑:
   - `account=AccountInfo(opened=row.account_opened, balance_manwon=row.account_balance_manwon)`
   - `income=IncomeInfo(monthly_manwon=row.income_monthly_manwon, base_manwon=row.income_base_manwon, dual_income=row.income_dual)`
   - `first_life=FirstLifeInfo(ever_owned_house=row.fl_ever_owned_house, income_tax_5y=row.fl_income_tax_5y, currently_earning=row.fl_currently_earning)`
   - 나머지 스칼라 필드 1:1 매핑(birth_date, marriage_date, engaged, is_household_head, household_all_homeless, homeless_since, dependents, region, won_within_5y, children_minor, real_estate_manwon).
   - 신규 필드(residence_regions/income_base_regions/household_type/car_value_manwon 등)는 Profile에 없으므로 어댑터가 별도 반환하지 않음 — 순위 로직(T09)은 MemberProfile 행에서 직접 읽는다. (어댑터는 기존 scoring 함수 호환용.)

## Deliverables
- `src/members.py` (신규)
- `tests/test_members.py` (신규)

## Verify
- `uv run pytest tests/test_members.py -q 2>&1 | tail -20` 통과.
- 테스트(`_db_available` 게이트): ① create_member→get_member_by_email 왕복(소문자 정규화 확인: 'A@x.com'로 만들고 'a@x.com'로 조회됨) ② profile_from_member가 AccountInfo/IncomeInfo/FirstLifeInfo 중첩까지 올바로 변환 ③ 에러: 중복 email create_member→ValueError ④ 경계: 빈 MemberProfile(모든 값 기본) → Profile 변환 시 예외 없음.

## Out of scope
- 비밀번호 해싱/인증(Task 04), 라우트(Task 05), 순위에서 지역 사용(Task 09).
