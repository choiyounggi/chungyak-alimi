# Task 10: 프로필 입력/수정 폼 라우트 + 템플릿

## Objective
로그인 회원이 `/profile`에서 자기 `member_profile` 전 필드를 입력/수정할 수 있다.
세대유형이 신혼/예비신혼일 때 두 사람의 거주지/소득본거지 입력이 표시된다.

## Wiki pages (read these first, only these)
- wiki/frontend/forms/validation-timing.md — 제출 검증, 서버 오류 필드 매핑
- wiki/security/input/validation-at-trust-boundaries.md — 폼 값 검증(숫자/날짜/enum/리스트)
- wiki/frontend/security/xss-safe-rendering.md — 값 재표시 이스케이프

## Inputs
- Task 02 산출물: `get_profile(member_id)`, `update_profile(member_id, values)`.
- Task 05 산출물: `require_login`(세션 member_id), Jinja `templates`.
- Task 06 산출물: base.html 네비(프로필 링크).
- 기존 `src/db.py`의 `MemberProfile` 컬럼 목록(Task 01), `src/scoring.py`(household_type 값 집합).
- 바인딩 결정: D14(세션 member_id로만 접근), D16/D21(검증), D22(XSS), D7(household_type enum).

## Steps
1. `src/web/app.py`에 `GET /profile`(require_login): `get_profile(member_id)` → `profile.html` 렌더(현재 값 채움).
2. `POST /profile`(require_login, form): pydantic 모델로 필드 검증(숫자 만원 필드 int≥0, 날짜 YYYY-MM-DD/빈값, household_type ∈ 4값, 지역 필드는 콤마구분→list[str]). 검증 실패 시 폼 에러 재렌더. 성공 시 `update_profile(member_id, values)` 후 `/profile`로 303(성공 메시지).
3. `src/web/templates/profile.html` 생성: base 확장, 섹션별 필드(기본/세대/통장/소득/생애최초/지역). household_type select. 거주지/소득본거지는 콤마구분 텍스트 입력(신혼/예비신혼일 때 2인 입력 안내). 접근성 label.
4. 모든 쓰기는 세션의 member_id로만(폼에 member_id 숨김 필드 두지 않음 — D14).

## Deliverables
- `src/web/app.py` (GET/POST /profile)
- `src/web/templates/profile.html` (신규)
- `tests/test_profile_form.py` (신규)

## Verify
- `uv run pytest tests/test_profile_form.py -q 2>&1 | tail -20` 통과.
- 테스트(TestClient, 로그인 후, `_db_available` 게이트): ① `GET /profile` 200 + 현재 값 표시 ② `POST /profile`로 지역/세대유형 저장 → 재조회 반영 ③ 에러: household_type 허용 외 값 → 검증 에러(저장 안 됨) ④ 경계: 미로그인 `GET /profile` → 303 `/login`; 빈 지역 입력 → `[]` 저장.

## Out of scope
- 대시보드 순위 반영(Task 11), 북마크(Task 12).
