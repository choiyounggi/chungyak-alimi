# Task 05: 3스텝 온보딩 라우터 + 템플릿

## Objective
가입 직후 `/onboarding/1..3` 에서 프로필을 나눠 받는다. 각 스텝은 자기 필드만 검증·저장하고
진행상태를 DB에 남겨, 중간에 이탈해도 재로그인 시 이어서 진행할 수 있다.

## Wiki pages (read these first, only these)
- frontend/forms/validation-timing.md — 스텝별 제출 검증, 서버 오류 필드 매핑
- security/input/validation-at-trust-boundaries.md — 스텝별 allowlist 검증
- frontend/state/client-vs-server-state.md — 진행상태를 DB에 두는 이유
- backend/common/api-design/error-responses.md — 400 재렌더 vs 303 전진
- frontend/accessibility/interactive-elements.md — 다중선택·라디오 그룹 라벨링

## Inputs
- **Task 01 산출물(계약)**: `member_profile` 신규 컬럼 `owns_car`, `account_payment_count`,
  `residence_history`(`[{"region","since"}]`), `preferred_types`, `partners`
  (`[{label,lives_with_parents,owns_home,residence_region,income_base_region}]`),
  `onboarding_step`(0~3); `members.update_profile` 이 `residence_history` → `residence_regions`
  파생 동기화를 수행함; `scoring.PREFERRED_TYPES` 허용값 집합.
- **Task 04 산출물(계약)**: `POST /register` 성공 시 `303 → /onboarding/1`.
- 기존 `src/web/app.py`: `ProfileForm` 과 그 애너테이션(`_OptDate`/`_Count`/`_OptCount`/
  `_Regions`/`_blank_to`/`_split_regions`), `_field_error`, `HOUSEHOLD_TYPES`,
  `COUPLE_HOUSEHOLD_TYPES`, `_TEMPLATES`, `require_login`, `SessionLocal`.
- 기존 `src/web/auth.py` 의 `APIRouter` 분리 패턴, `src/web/templates/profile.html` 의
  `field`/`check` 매크로와 CSS 토큰.
- 바인딩 결정: O1(진행상태 컬럼), O2(라우터 분리), O3(스텝 분할), O4(부분 저장),
  O5(차단 없이 배너), O6(폼 모델 재사용), R4(신혼 7년 폼 검증), D3/D4/D5.

## Steps
1. 재사용을 위해 `app.py` 의 폼 애너테이션·헬퍼(`_OptDate`, `_Count`, `_OptCount`, `_Regions`,
   `_blank_to`, `_split_regions`, `_field_error`, `HOUSEHOLD_TYPES`, `COUPLE_HOUSEHOLD_TYPES`)를
   `src/web/forms.py` 로 옮기고 `app.py` 는 그것을 import 한다.
   **이동만 하고 동작은 바꾸지 않는다**(`ProfileForm` 자체와 `/profile` 라우트는 app.py 잔류).
2. `src/web/onboarding.py` 신규 `APIRouter`:
   - `OnboardingStep1`: `birth_date`, `household_type`, `marriage_date`, `is_household_head`,
     `household_all_homeless`, `dependents`, `children_minor`
   - `OnboardingStep2`: `owns_car`, `car_value_manwon`, `real_estate_manwon`,
     `household_head_owns_home`, `fl_ever_owned_house`, `account_opened`,
     `account_payment_count`, `account_balance_manwon`, `income_monthly_manwon`,
     `income_base_manwon`, `income_dual`
   - `OnboardingStep3`: `residence_history`, `income_base_regions`, `interest_regions`,
     `preferred_types`, `partners`
   - `GET /onboarding/{step}`(1~3, `require_login`) — 현재 저장값으로 폼 렌더. 범위 밖 step은 404.
   - `POST /onboarding/{step}` — 해당 모델로 검증 → 실패 시 400 재렌더(입력값·필드 에러 보존),
     성공 시 `update_profile` + `onboarding_step = max(현재, step)` → 다음 스텝(3이면 `/`)으로 303.
   - 되돌아가기 허용: 낮은 step 을 다시 제출해도 `onboarding_step` 은 내려가지 않는다(O4).
3. 교차 필드 검증(`model_validator(mode="after")`):
   - Step1 — `household_type == "newlywed"` 인데 `marriage_date` 가 없거나 오늘 기준 7년 초과 →
     `marriage_date` 필드 에러(R4). 7년 계산은 `scoring._full_years` 를 쓰지 말고
     **`scoring` 에 이미 있는 규칙 상수/헬퍼를 import** 해 중복 정의하지 않는다.
   - Step3 — `preferred_types` 에 `pre_newlywed` 가 있으면 `partners` 가 정확히 2개이고
     각 항목의 `residence_region` 이 비어 있지 않아야 한다. 허용값 밖의 `preferred_types` 는 거부.
   - Step3 — `residence_history` 항목은 `region` 필수, `since` 는 `YYYY-MM-DD` 또는 빈값,
     미래 날짜 거부, 최대 10개.
4. 템플릿 `onboarding_1.html` / `onboarding_2.html` / `onboarding_3.html`:
   - `profile.html` 의 `field`/`check` 매크로를 `_macros.html` 로 승격해 공유한다
     (`profile.html` 도 그 매크로를 import 하도록 바꾸되 **렌더 결과는 동일**해야 한다).
   - 스텝 진행 표시(`<ol>` + `aria-current="step"`), 이전/다음 버튼.
   - `preferred_types` 는 체크박스 그룹(`<fieldset><legend>`), `owns_car` 체크 시에만
     차량가액 입력 표시(JS는 `hidden` 토글만, 서버는 항상 값을 검증).
   - `residence_history` 는 "지역 + 거주 시작일" 행 반복(기본 1행, JS로 행 추가, 최대 10).
     JS 없이도 첫 행은 제출 가능해야 한다.
   - `partners` 는 `pre_newlywed` 선택 시에만 표시되는 2인 블록(각각 부모동거 여부·자가 보유·
     거주지·소득본거지).
5. `src/web/app.py`: `include_router(onboarding.router)` 추가, `GET /` 렌더 컨텍스트에
   `onboarding_step` 을 넣고 `templates/base.html`(또는 `index.html`)에 미완성 배너
   (`onboarding_step < 3` 일 때만, 이어하기 링크). **접근 차단은 하지 않는다**(O5).

## Deliverables
- `src/web/forms.py` (신규, app.py에서 이동)
- `src/web/onboarding.py` (신규)
- `src/web/templates/onboarding_1.html`, `onboarding_2.html`, `onboarding_3.html` (신규)
- `src/web/templates/_macros.html`, `profile.html`, `base.html` (매크로 공유·배너)
- `src/web/app.py` (import 정리, include_router, 배너 컨텍스트)
- `tests/test_onboarding.py` (신규)

## Verify
- `uv run pytest tests/test_onboarding.py tests/test_profile_form.py tests/test_web.py tests/test_index_template.py tests/test_base_template.py -q 2>&1 | tail -20` 통과
  (**기존 `/profile` 폼 테스트 회귀 금지**).
- `uv run ruff check src tests 2>&1 | tail -5` clean.
- 테스트(TestClient, 로그인 후, `_db_available` 게이트): ① 정상 — 1→2→3 순차 제출 후
  `onboarding_step == 3`, 저장값 재조회 일치, 마지막 303 `Location == "/"`
  ② 에러 — Step1에서 `newlywed` + 혼인신고일 8년 전 → 400 + `marriage_date` 필드 에러,
  Step3에서 `pre_newlywed` 인데 `partners` 1명 → 400, `preferred_types` 에 허용 외 값 → 400
  ③ 경계 — 미로그인 `GET /onboarding/1` → 303 `/login`, `GET /onboarding/4` → 404,
  Step2를 완료한 회원이 Step1을 다시 제출해도 `onboarding_step` 이 2 아래로 내려가지 않음,
  `residence_history` 빈 제출 → `[]` 저장 + `residence_regions` 도 `[]`,
  `residence_history` 11개 → 400, 미래 `since` → 400
  ④ 배너 — `onboarding_step < 3` 인 회원의 `GET /` 응답에 이어하기 링크가 있고,
  `== 3` 이면 없다. 그리고 **어느 경우에도 `/` 는 200**(차단 없음).

## Out of scope
- 목록 순위 표시(Task 06), scoring 판정 로직(Task 03).
