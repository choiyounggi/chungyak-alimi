# Task 03: bookmark 회원별 스키마 + 이관 함수

## Objective
`src/db.py`의 `Bookmark`가 `member_id`를 갖고 PK=(member_id, pblanc_no) 복합키가 되며,
회원별 북마크 함수와 기존 전역 북마크를 한 회원으로 이관하는 함수가 제공된다.

## Wiki pages (read these first, only these)
- wiki/databases/schema-design/primary-key-choice.md — 접합(junction) 테이블 복합 PK
- wiki/databases/schema-design/foreign-keys-and-referential-actions.md — member_id FK, ON DELETE CASCADE
- wiki/databases/schema-design/online-schema-changes.md — 기존 테이블에 컬럼 추가/PK 변경(확장-후-축소)

## Inputs
- Task 01 산출물: `src/db.py`의 `Member`. 기존 `Bookmark`(pblanc_no PK, created_at), `add_bookmark`/`remove_bookmark`/`is_bookmarked`/`bookmarked_pblanc_nos`(현재 전역, session kwarg).
- 바인딩 결정: D11(복합 PK, CASCADE, 전역→영기 이관), D14(회원 범위 필터).

## Steps
1. `Bookmark` 모델 변경: `member_id: Mapped[int]` = `mapped_column(ForeignKey("member.id", ondelete="CASCADE"), primary_key=True)`; 기존 `pblanc_no`도 `primary_key=True` 유지 → 복합 PK. FK 자식측 인덱스는 복합 PK 선두가 member_id라 조회에 충분.
2. 기존 함수 시그니처에 `member_id` 추가(회원 범위): `add_bookmark(member_id, pblanc_no, *, session)`, `remove_bookmark(member_id, pblanc_no, *, session)`, `is_bookmarked(member_id, pblanc_no, *, session)`, `bookmarked_pblanc_nos(member_id, *, session) -> set[str]`. 모든 쿼리는 `WHERE member_id=:member_id` 포함(D14).
3. `add_bookmark`는 pg_insert on_conflict_do_nothing(복합 PK 기준)으로 멱등 유지.
4. `migrate_global_bookmarks_to_member(member_id, *, session)` 추가: `member_id`가 NULL/미설정인 기존 행(구 스키마에서 이관 대상)을 대상 회원으로 재지정. 이미 이관됐으면 no-op(멱등). 신규 DB(기존 북마크 없음)에서도 예외 없이 통과.
5. `init_db()`가 기존 DB의 `bookmark` 테이블에 `member_id` 컬럼을 추가하도록 보강(기존 `ADD COLUMN IF NOT EXISTS` 패턴 사용). 복합 PK 재구성이 필요하면 `init_db`에서 방어적으로 처리하되, 데이터가 없을 땐 create_all로 충분.

## Deliverables
- `src/db.py` (Bookmark 모델 + 함수 4개 + migrate 함수)
- `tests/test_bookmark.py` (기존 파일 수정 — member_id 인자 반영)

## Verify
- `uv run pytest tests/test_bookmark.py -q 2>&1 | tail -30` 통과.
- 테스트(`_db_available` 게이트): ① 회원 A add→A만 bookmarked_pblanc_nos에 보임, 회원 B는 빈 set(격리) ② 멱등: 같은 (member,pblanc) 두 번 add→1건 ③ 에러/경계: 없는 것 remove→예외 없음 ④ migrate: member_id 없는 행을 대상 회원으로 이관 후 그 회원 목록에 나타남, 재호출 멱등.

## Out of scope
- 웹 엔드포인트/템플릿(Task 12), 시드에서 migrate 호출(Task 07).
