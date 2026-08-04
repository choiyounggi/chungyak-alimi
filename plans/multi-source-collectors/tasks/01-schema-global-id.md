# Task 01: notice PK를 `source:native` 글로벌 ID로 전환하고 기관·임대료 컬럼 추가

## Objective
`notice` 테이블에 `native_id`·`agency`·`rent_gtn`·`mt_rntchrg` 4개 컬럼이 생기고,
`notice.pblanc_no`와 자식 4개 테이블의 `pblanc_no`가 전부 `"<source>:<native_id>"`
형태로 이관된다. 이관은 몇 번 실행해도 결과가 같다(멱등).

## Wiki pages (read these first, only these)
- `wiki/databases/schema-design/online-schema-changes.md` — 컬럼 추가를 재작성 없이(메타데이터 전용) 하는 방법, 백필 트랜잭션 크기
- `wiki/databases/schema-design/column-data-types.md` — 금액 컬럼 타입 선택(정수 최소단위)
- `wiki/databases/schema-design/nullability-and-defaults.md` — 신규 컬럼의 nullable/기본값 선언
- `wiki/testing/quality/minimum-case-set.md` — 이관 테스트의 케이스 선정

## Inputs
- `src/db.py` — `Notice` 모델(L58~85), `_COLS`(L32~50), `init_db()`(L144~148),
  `_to_row()`(L167~171), `upsert_notices()`(L174~216), `upsert_house_types()`(L219~250).
- 자식 테이블 4개(전부 `pblanc_no` 단일 PK): `notice_house_type`(L88), `match_result`(L115),
  `notify_log`(L126), `bookmark`(L136).
- `tests/test_db.py` — 기존 `_db_available()` 스킵 가드 + `session` 픽스처 패턴(L1~35).
- Decisions that bind you: D1(글로벌 ID·단일 문자열 PK), D2(`native_id` 컬럼),
  D3(nullable·기본값 없음), D4(`BigInteger`·원 단위), D5(`init_db()` 안에서 멱등 이관),
  D6(자식 먼저 → notice 마지막), D7·D8(`agency` 5종·소스별 기본값), D24(테스트 케이스).

## Steps
1. `src/db.py` 상단, `SUPERSEDED_REASON` 인근에 상수와 헬퍼를 추가한다:
   ```python
   # 공급기관 축(D7) — 닫힌 5종. 웹 필터 칩과 값을 공유한다.
   AGENCIES = ("LH", "SH", "GH", "HUG", "기타")

   # 수집원 → 기본 공급기관(D8). myhome 은 행마다 달라 모델이 agency 를 직접 싣는다.
   AGENCY_BY_SOURCE = {
       "applyhome": "기타", "lh": "LH", "hug": "HUG", "sh": "SH", "gh": "GH",
   }


   def global_id(source: str, native: str) -> str:
       """기관 간 공고번호 충돌을 막는 글로벌 ID(D1). 이미 접두된 값은 그대로 둔다."""
       native = str(native)
       return native if ":" in native else f"{source}:{native}"
   ```
2. `Notice` 모델에 컬럼 4개를 추가한다(`source` 선언 바로 아래). `BigInteger`를
   `sqlalchemy` import 목록에 추가한다:
   ```python
   native_id: Mapped[str | None] = mapped_column(String)   # 소스 원본 공고번호(D2)
   agency: Mapped[str | None] = mapped_column(String)      # 공급기관 LH/SH/GH/HUG/기타(D7)
   rent_gtn: Mapped[int | None] = mapped_column(BigInteger)    # 임대보증금(원, D4)
   mt_rntchrg: Mapped[int | None] = mapped_column(BigInteger)  # 월임대료(원, D4)
   ```
3. `_COLS` 끝에 `"native_id", "agency", "rent_gtn", "mt_rntchrg"` 4개를 추가한다.
4. `_to_row()`를 아래로 교체한다. 기존 collector 모델(`ApplyhomeNotice`/`LhNotice`)에는
   새 속성이 없으므로 `getattr(..., None)` 기본값이 필수다:
   ```python
   def _to_row(n, source: str) -> dict:
       row = {c: getattr(n, c, None) for c in _COLS}
       row["native_id"] = n.pblanc_no
       row["pblanc_no"] = global_id(source, n.pblanc_no)
       row["agency"] = getattr(n, "agency", None) or AGENCY_BY_SOURCE.get(source, "기타")
       row["raw"] = n.raw
       row["source"] = source
       return row
   ```
5. `upsert_notices()`에서 글로벌 ID를 쓰도록 3곳을 고친다:
   - 중복 제거: `deduped = {global_id(source, n.pblanc_no): n for n in notices}`
   - `incoming = [global_id(source, n.pblanc_no) for n in notices]`
   - 나머지(`existing` 조회, `rows`, `on_conflict`)는 그대로 — 이제 전부 글로벌 ID를 다룬다.
6. `upsert_house_types()`에 `source: str = "applyhome"` 키워드 인자를 추가하고,
   행 조립 루프에서 `row["pblanc_no"] = global_id(source, ht.pblanc_no)` 한 줄을
   `row["raw"] = ht.raw` 앞에 넣는다. 배치 내 중복 제거 키도
   `(global_id(source, ht.pblanc_no), ht.house_ty)`로 바꾼다.
7. `init_db()`를 아래로 교체한다. 컬럼 추가는 nullable·기본값 없음이라 즉시 끝나고(D3),
   값 이관은 `NOT LIKE '%:%'` 가드로 멱등하다(D5). 자식이 먼저다(D6):
   ```python
   def init_db() -> None:
       Base.metadata.create_all(engine)
       # 경량 마이그레이션: create_all은 기존 테이블에 컬럼을 추가하지 않는다
       with engine.begin() as conn:
           conn.exec_driver_sql("ALTER TABLE match_result ADD COLUMN IF NOT EXISTS my_rank VARCHAR")
           for ddl in (
               "ALTER TABLE notice ADD COLUMN IF NOT EXISTS native_id VARCHAR",
               "ALTER TABLE notice ADD COLUMN IF NOT EXISTS agency VARCHAR",
               "ALTER TABLE notice ADD COLUMN IF NOT EXISTS rent_gtn BIGINT",
               "ALTER TABLE notice ADD COLUMN IF NOT EXISTS mt_rntchrg BIGINT",
           ):
               conn.exec_driver_sql(ddl)
       migrate_global_ids()


   def migrate_global_ids() -> dict:
       """pblanc_no 를 '<source>:<native>' 로 이관(D5·D6). 멱등 — 이미 접두된 행은 건너뛴다.

       자식 테이블을 먼저 갱신한다. notice 를 먼저 바꾸면 조인 키가 사라진다.
       반환: 테이블별 갱신 행 수.
       """
       counts: dict[str, int] = {}
       with engine.begin() as conn:
           conn.exec_driver_sql(
               "UPDATE notice SET native_id = pblanc_no WHERE native_id IS NULL"
           )
           conn.exec_driver_sql(
               "UPDATE notice SET agency = CASE source WHEN 'lh' THEN 'LH' ELSE '기타' END"
               " WHERE agency IS NULL"
           )
           for table in ("notice_house_type", "match_result", "notify_log", "bookmark"):
               r = conn.exec_driver_sql(
                   f"UPDATE {table} c SET pblanc_no = n.source || ':' || c.pblanc_no"
                   " FROM notice n WHERE n.pblanc_no = c.pblanc_no"
                   " AND c.pblanc_no NOT LIKE '%:%'"
               )
               counts[table] = r.rowcount
           r = conn.exec_driver_sql(
               "UPDATE notice SET pblanc_no = source || ':' || pblanc_no"
               " WHERE pblanc_no NOT LIKE '%:%'"
           )
           counts["notice"] = r.rowcount
       return counts
   ```
8. `scripts/migrate_global_id.py`를 새로 만든다 — 이관은 `init_db()`가 이미 하므로
   이 스크립트는 **호출하고 건수를 출력하는 확인용**이다:
   ```python
   """글로벌 ID 이관 확인용 일회성 스크립트. init_db() 가 이미 멱등 이관을 수행하므로
   이 스크립트는 결과 건수를 눈으로 확인하기 위한 것이다(D5)."""

   from __future__ import annotations

   import json

   from src.db import init_db, migrate_global_ids


   def main() -> None:
       init_db()
       print(json.dumps(migrate_global_ids(), ensure_ascii=False))


   if __name__ == "__main__":
       main()
   ```
9. `tests/test_db.py`에 테스트를 추가한다(기존 `session` 픽스처·스킵 가드 재사용). 최소 4케이스(D24):
   - `test_upsert_notices_uses_global_id`: `upsert_notices([_notice("X1")], source="applyhome")` 후
     `Notice.pblanc_no == "applyhome:X1"`, `native_id == "X1"`, `agency == "기타"`.
   - `test_global_id_is_idempotent`: `global_id("lh", "lh:9") == "lh:9"`.
   - `test_migrate_moves_child_rows`: native ID로 `notice` 1행 + `bookmark` 1행을 직접
     INSERT → `migrate_global_ids()` → 두 행 모두 `"applyhome:"` 접두, 북마크가 유지된다.
   - `test_migrate_twice_is_noop`: 위 상태에서 다시 호출하면 `counts["notice"] == 0`이고
     `pblanc_no`가 변하지 않는다.
   - `test_migrate_keeps_orphan_child`: `notice`에 없는 `pblanc_no`를 가진 `bookmark` 행은
     이관되지 않고 그대로 남는다(예외도 나지 않는다).

## Deliverables
- `src/db.py` (수정)
- `scripts/migrate_global_id.py` (신규)
- `tests/test_db.py` (수정 — 테스트 5개 추가)

## Verify
- `./.venv/bin/pytest tests/test_db.py -q` → 전부 통과.
- `./.venv/bin/pytest -q` → 기존 179개 회귀 없음.
- `./.venv/bin/ruff check src tests scripts` → 클린.
- `./.venv/bin/python scripts/migrate_global_id.py` 2회 연속 실행 → 2회차 출력의
  모든 값이 `0`.

## Out of scope
- 신규 collector 작성(03~06), 필터 정책(02), pipeline 배선(07), 웹(08).
- `lttot_top_amount` 등 기존 분양가 컬럼 변경 — 손대지 않는다.
- `agency`에 DB CHECK 제약 추가(D7에서 두지 않기로 결정).
