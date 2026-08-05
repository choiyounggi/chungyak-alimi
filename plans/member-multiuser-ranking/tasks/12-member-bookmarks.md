# Task 12: 북마크 회원별화 (모델 복합PK + DB함수 + 이관 + app 엔드포인트)

## Objective
북마크가 회원별로 격리된다: `Bookmark`가 (member_id, pblanc_no) 복합 PK가 되고,
DB 함수·엔드포인트·목록이 모두 세션의 현재 회원 기준으로 동작하며, 기존 전역 북마크는
영기 계정으로 이관된다. 한 회원의 북마크는 다른 회원에게 보이지 않는다.

## Wiki pages (read these first, only these)
- wiki/databases/schema-design/primary-key-choice.md — 접합(junction) 복합 PK
- wiki/databases/schema-design/foreign-keys-and-referential-actions.md — member_id FK, ON DELETE CASCADE
- wiki/security/authz/resource-level-checks.md — 세션 member_id로만 접근(요청 id 불신), API는 401

## Inputs
- Task 01 산출물: `src/db.py`의 `Member`. 기존 `Bookmark`(pblanc_no PK, created_at), 전역 함수 `add_bookmark(pblanc_no,*,session)`/`remove_bookmark`/`is_bookmarked`/`bookmarked_pblanc_nos(*,session)`, `init_db`/`migrate_global_ids` 패턴, `pg_insert`.
- Task 05 산출물: `current_member_id(request)`(세션 member_id | None). 미로그인 API는 401.
- 기존 `src/web/app.py`: `bookmarked_pblanc_nos(session=session)`(라인~170), `add_bookmark(pblanc_no,session)`(~316), `remove_bookmark`(~325), `bookmarked_dashboard`(Bookmark 조인, ~187), `@app.put/delete("/bookmark/{pblanc_no}")`, `@app.get("/bookmarks")`.
- 바인딩 결정: D11(복합 PK, CASCADE, 전역→영기 이관), D14(세션 member_id), D15(API 401).

## Steps
1. `src/db.py` `Bookmark` 모델 변경: `member_id: Mapped[int]` = `mapped_column(BigInteger, ForeignKey("member.id", ondelete="CASCADE"), primary_key=True)`; 기존 `pblanc_no`도 `primary_key=True` 유지 → 복합 PK. (선두 member_id로 회원 조회 인덱스 충족.)
2. DB 함수 시그니처에 member_id 추가(모든 쿼리에 `WHERE member_id=:member_id` 포함, D14): `add_bookmark(member_id, pblanc_no, *, session)`(pg_insert on_conflict_do_nothing, index_elements=["member_id","pblanc_no"]), `remove_bookmark(member_id, pblanc_no, *, session)`, `is_bookmarked(member_id, pblanc_no, *, session)`, `bookmarked_pblanc_nos(member_id, *, session)`.
3. `migrate_global_bookmarks_to_member(member_id, *, session=None)` 추가: member_id 컬럼이 NULL인 기존 행을 대상 회원으로 재지정(멱등, 신규 DB에서도 예외 없음). `init_db`에 `ALTER TABLE bookmark ADD COLUMN IF NOT EXISTS member_id BIGINT` 보강.
4. `src/web/app.py` 갱신(그린 유지):
   - `bookmarked_dashboard(session, member_id, today=None)` — 조인/필터를 member_id 기준으로; `bookmarked_pblanc_nos(member_id, session=session)` 사용.
   - `PUT/DELETE /bookmark/{pblanc_no}`: `current_member_id` 없으면 401, 있으면 `add/remove_bookmark(member_id, pblanc_no, session=session)`. notice 없으면 404 유지, 반환 JSON 동일.
   - `GET /bookmarks`: 미로그인 303 `/login`, 로그인 시 `bookmarked_dashboard(session, member_id)` 렌더.
   - `matched_dashboard`/`member_dashboard`의 `bmarks = bookmarked_pblanc_nos(member_id, session=session)`로 수정(Task 11이 member_dashboard를 만들었으면 그쪽 호출부도 일치).

## Deliverables
- `src/db.py` (Bookmark 모델 + 함수 4개 member화 + migrate 함수)
- `src/web/app.py` (엔드포인트/대시보드 member화)
- `tests/test_bookmark.py` (기존 수정 — member_id 인자 + 회원 격리 + 엔드포인트 401/303)

## Verify
- `uv run pytest tests/test_bookmark.py -q 2>&1 | tail -30` 통과.
- 테스트(`_db_available` 게이트): ① 회원 A add→A만 보임, 회원 B 빈 set(격리) ② 멱등: 같은 (member,pblanc) 2회 add→1건 ③ DB 에러/경계: 없는 것 remove→예외 없음 ④ migrate: member_id NULL 행 이관 후 그 회원 목록에 나타남, 재호출 멱등 ⑤ 엔드포인트: 회원 A 로그인 PUT→A `/bookmarks` 노출, 회원 B 비노출; 미로그인 PUT/DELETE→401, 없는 공고→404, 미로그인 GET /bookmarks→303.
- 전체 회귀: `uv run pytest -q 2>&1 | tail -20` + `uv run ruff check . 2>&1 | tail -5`.

## Out of scope
- 대시보드 순위/지역 필터(Task 11), 프로필 폼(Task 10).
