# Task 11: 회원별 대시보드 온더플라이 순위 + 관심지역 필터/전체보기

## Objective
로그인 회원의 대시보드가 요청 시점에 그 회원 프로필로 순위를 계산하고(거주지/소득본거지
해당지역 1순위 반영), 기본은 회원 `interest_regions`만 보여주되 "전체 보기"로 타 지역도 열람한다.

## Wiki pages (read these first, only these)
- wiki/frontend/state/client-vs-server-state.md — "전체 보기" 토글은 클라이언트 UI 상태
- wiki/security/authz/resource-level-checks.md — 세션 member_id로만 프로필/데이터 접근
- wiki/backend/common/api-design/error-responses.md — 비로그인 처리(303)

## Inputs
- Task 02: `get_profile`, `profile_from_member`.
- Task 09: `judge_rank(..., applicant_regions=...)`(+`in_area`), 기존 `judge_notice`/`score_points`.
- Task 08: `region_matches`(필터에 재사용 가능).
- Task 12: 회원 범위 `bookmarked_pblanc_nos(member_id, *, session)` — 대시보드 북마크 플래그는 이 함수로 계산(Task 12가 11에 선행).
- 기존 `src/web/app.py`: `matched_dashboard(session, ...)`, `_dashboard_item(...)`, `index` 라우트, 기존 지도/뷰포트·칩 필터(index.html) — 회귀 금지.
- 기존 `src/web/templates/index.html`(칩/지도/목록), `_macros.html`(notice_card, `data-rank`).
- 바인딩 결정: D17(온더플라이), D18(지역), D20(전체보기=클라 상태), D14(세션).

## Steps
1. 신규 함수 `member_dashboard(session, member_id, today=None) -> list[dict]` 추가(기존 `matched_dashboard`는 남겨 재사용/참조): 회원 `MemberProfile`을 읽어 각 공고에 대해 `applicant_regions = residence_regions ∪ income_base_regions`로 `judge_rank(..., applicant_regions=...)` 호출, `my_rank`와 `in_area`를 아이템(`_dashboard_item` 확장)에 실어 반환. 정렬: 해당지역(in_area True) 우선 → 1순위→2순위→판정불가 → 그 안에서 마감임박순.
2. `index` 라우트: `require_login`으로 member_id 확보 → 회원 프로필로 대시보드 계산. 미로그인 시 303 `/login`.
3. 관심지역 필터: 서버는 전체(matched) 목록을 내려주되, 각 아이템에 `data-area`(기존 존재)와 회원 `interest_regions`를 템플릿/JS에 전달. 기본 렌더는 interest_regions에 속한 카드만 표시, "전체 보기" 토글(button, aria-pressed) 시 전체 표시 — 클라이언트 필터(기존 칩/뷰포트 필터 로직과 AND 결합, 기존 로직 재사용).
4. `_macros.html`/`index.html`: 해당지역 1순위는 뱃지로 구분(예: rank-1 + "해당지역" 표식). `in_area`를 `data-inarea`로 실어 필요 시 필터.
5. 기존 지도 마커/뷰포트 필터/칩 필터 동작 보존(회귀 테스트 유지).

## Deliverables
- `src/web/app.py` (index/대시보드 회원화)
- `src/web/templates/index.html` (전체보기 토글 + 관심지역 기본필터 + 해당지역 뱃지)
- `tests/test_index_template.py` (기존 수정) 또는 `tests/test_member_dashboard.py` (신규)

## Verify
- `uv run pytest tests/test_member_dashboard.py tests/test_index_template.py -q 2>&1 | tail -30` 통과.
- 테스트(`_db_available` 게이트): ① 로그인 회원의 거주지 매칭 공고가 해당지역 1순위로 표시 ② interest_regions 밖 공고는 기본 미표시, "전체 보기"에서 표시(토글 컨텍스트/속성 확인) ③ 미로그인 `/` → 303 ④ 경계: `interest_regions`가 비어있으면 기본 렌더에서 전체 표시(폴백=전체) — 예외 없음 ⑤ 회귀: 기존 칩/지도 요소가 여전히 렌더.

## Out of scope
- 북마크 회원 격리(Task 12), 프로필 편집(Task 10).
