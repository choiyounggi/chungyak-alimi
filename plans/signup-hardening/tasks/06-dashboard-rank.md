# Task 06: 목록 순위 반영(민영/국민 분기 + 선호전형)

## Objective
대시보드 목록이 공고 유형에 따라 민영은 `judge_rank`, 국민주택은 `judge_rank_public`
결과로 1순위/2순위를 표시한다. 온보딩에서 받은 납입횟수·거주기간·선호전형이
실제로 화면에 반영된다.

## Wiki pages (read these first, only these)
- frontend/security/xss-safe-rendering.md — 판정 사유 문자열 출력
- frontend/state/client-vs-server-state.md — 필터는 클라이언트 상태, 목록은 서버
- testing/quality/minimum-case-set.md — 유형별/경계 케이스

## Inputs
- **Task 03 산출물(계약)**:
  `judge_rank_public(notice, p, today=None, applicant_regions=None) -> dict`
  (`{"rank","regulated","reasons","in_area"}`),
  `judge_notice(...)` 가 `housing_type: "민영"|"국민"` 을 포함해 반환,
  `residence_years_in(p, region, today=None) -> float`.
- **Task 05 산출물(계약)**: `member_profile.onboarding_step`, `preferred_types` 가
  채워져 있음; `/onboarding/1` 이 존재함.
- 기존 `src/web/app.py` `member_dashboard`(171~229행), `_dashboard_item`(99행~),
  정렬 규칙(해당지역 → 순위 → 마감임박), `src/web/templates/index.html` 의 카드 마크업·필터.
- 바인딩 결정: R1/R2(순위·분기), R3(거주기간은 사유만), D19(지역은 순위 요건 아님), D4(선호전형).

## Steps
1. `member_dashboard` 가 공고마다 `judge_notice(...)` 의 `supported` / `housing_type` 을 보고
   민영이면 기존 `judge_rank`, 국민이면 `judge_rank_public` 을 호출하도록 바꾼다.
   `supported` 게이트는 계속 `judge_notice` 가 쥔다(중복 정의 금지 — 기존 주석의 원칙 유지).
2. `_dashboard_item` 에 `housing_type` 과 `rank_reasons`(판정 사유 리스트)를 추가한다.
   기존 키(`my_rank`/`in_area`/`in_interest`/`deadline`/북마크 플래그)는 유지 —
   `tests/test_my_rank.py`·`test_member_dashboard.py`·지도 UI가 소비 중이다.
3. 정렬 규칙은 바꾸지 않는다(해당지역 → 순위 → 마감임박). 국민주택 공고도 같은 규칙을 탄다.
4. `preferred_types` 필터: 회원이 선호전형을 지정했으면 카드에 해당 배지를 달고,
   기존 "관심지역만/전체 보기" 토글과 **같은 방식의 클라이언트 필터**로 "선호 전형만" 토글을
   추가한다. 서버는 전체 목록을 그대로 내려준다(D20/기존 패턴 유지). 선호전형 미지정이면
   토글을 렌더하지 않는다(빈 화면 방지).
5. `index.html`: 순위 배지에 유형을 함께 표기(예: `국민 · 1순위`), 사유는 카드 상세에
   `title`/보조 텍스트로. 문자열 HTML 조립 금지, Jinja 자동이스케이프에 맡긴다.

## Deliverables
- `src/web/app.py` (`member_dashboard`, `_dashboard_item`)
- `src/web/templates/index.html`
- `tests/test_dashboard_rank_public.py` (신규)

## Verify
- `uv run pytest tests/test_dashboard_rank_public.py tests/test_member_dashboard.py tests/test_my_rank.py tests/test_index_template.py tests/test_web.py -q 2>&1 | tail -20` 통과
  (**기존 대시보드 테스트 회귀 금지**).
- `uv run ruff check src tests 2>&1 | tail -5` clean.
- 전체 스위트: `uv run pytest -q 2>&1 | tail -10` 통과.
- 테스트(`_db_available` 게이트): ① 정상 — 국민주택 공고 + 납입 13회 수도권 회원 →
  항목의 `my_rank == "1순위"`, `housing_type == "국민"`
  ② 에러/차등 — 같은 회원·같은 공고에서 납입 5회 → `"2순위"` + `rank_reasons` 에 납입횟수 사유
  ③ 경계 — 프로필 없는 회원(`get_profile` 결과 없음) → `my_rank is None` 이고 예외 없음,
  `preferred_types == []` 인 회원의 `GET /` 에는 선호전형 토글이 렌더되지 않음,
  민영 공고의 기존 판정 결과가 이 변경 전후로 동일(회귀 확인).

## Out of scope
- 지도 뷰 변경(plans/map-dashboard 범위), 알림(notify.py), 새 수집원.
