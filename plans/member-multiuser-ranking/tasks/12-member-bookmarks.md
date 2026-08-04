# Task 12: 회원별 북마크 엔드포인트/목록 격리

## Objective
북마크 PUT/DELETE/목록/대시보드 플래그가 모두 세션의 현재 회원 기준으로 동작해,
한 회원의 북마크가 다른 회원에게 보이지 않는다.

## Wiki pages (read these first, only these)
- wiki/security/authz/resource-level-checks.md — 세션 member_id로만 북마크 접근(요청 id 불신), API는 401
- wiki/databases/query-optimization/existence-and-count-checks.md — is_bookmarked/카운트 게이팅

## Inputs
- Task 03 산출물: `add_bookmark(member_id, pblanc_no)`, `remove_bookmark(...)`, `is_bookmarked(...)`, `bookmarked_pblanc_nos(member_id)`.
- Task 05 산출물: `require_login`/`current_member_id`(API성 라우트는 미로그인 401).
- Task 11 산출물: 회원 인지형 대시보드(`_dashboard_item`의 `bookmarked` 계산).
- 기존 `src/web/app.py`: `@app.put/delete("/bookmark/{pblanc_no}")`, `@app.get("/bookmarks")`, `bookmarked_dashboard`. 기존 `src/web/templates/bookmarks.html`, `base.html` 북마크 토글 JS.
- 바인딩 결정: D14(세션 member_id), D15(API 401).

## Steps
1. `PUT/DELETE /bookmark/{pblanc_no}`: `current_member_id` 없으면 401. 있으면 `add_bookmark(member_id, pblanc_no)`/`remove_bookmark(...)`. 반환 JSON은 기존과 동일(`{"bookmarked": bool}`). notice 없으면 404 유지.
2. `GET /bookmarks`: `require_login`(미로그인 303). `bookmarked_dashboard(session, member_id, ...)`로 그 회원 북마크만 렌더.
3. `bookmarked_dashboard`/`_dashboard_item`의 `bookmarked` 계산을 `bookmarked_pblanc_nos(member_id)` 기준으로. (Task 11에서 대시보드 회원화했으면 여기서 북마크 부분만 회원 인지화.)
4. 기존 북마크 토글 JS(base.html)는 엔드포인트 URL 동일하므로 변경 최소. 미로그인 상태에서 토글 시 401 처리(로그인 유도) 확인.

## Deliverables
- `src/web/app.py` (bookmark 라우트 + bookmarked_dashboard 회원화)
- `tests/test_bookmark.py` (기존 수정 — 라우트 회원 격리 케이스 추가; Task 03에서 이미 DB층 반영)

## Verify
- `uv run pytest tests/test_bookmark.py -q 2>&1 | tail -30` 통과.
- 테스트(TestClient, `_db_available` 게이트): ① 회원 A 로그인 PUT → A의 `/bookmarks`에 노출 ② 회원 B 로그인 → 같은 공고가 B `/bookmarks`에 비노출(격리) ③ 에러: 미로그인 PUT/DELETE → 401, 없는 공고 PUT → 404 ④ 경계: 미로그인 `GET /bookmarks` → 303.
- 전체 회귀: `uv run pytest -q 2>&1 | tail -20` (ruff 포함 `uv run ruff check . 2>&1 | tail -5`).

## Out of scope
- 대시보드 순위/필터(Task 11), 프로필 폼(Task 10).
