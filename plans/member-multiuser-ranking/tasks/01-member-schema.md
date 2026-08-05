# Task 01: member / member_profile 모델 + init_db 스키마

## Objective
`src/db.py`에 `Member`, `MemberProfile` ORM 모델이 추가되고, `init_db()`가 이 두 테이블을
멱등하게 생성한다. 신규/기존 DB 모두에서 앱 기동 시 스키마가 준비된다.

## Wiki pages (read these first, only these)
- wiki/databases/schema-design/primary-key-choice.md — Member PK(BIGINT identity), email UNIQUE
- wiki/databases/schema-design/column-data-types.md — DATE/TIMESTAMPTZ/INTEGER(만원)/TEXT+CHECK(enum)/JSONB(지역 목록)
- wiki/databases/schema-design/foreign-keys-and-referential-actions.md — member_profile→member FK, ON DELETE CASCADE
- wiki/databases/schema-design/nullability-and-defaults.md — bool NOT NULL DEFAULT, nullable 의미 명시

## Inputs
- 기존 `src/db.py`: `Base`(DeclarativeBase), `engine`, `SessionLocal`, `init_db()`(멱등, `Base.metadata.create_all` + `exec_driver_sql ADD COLUMN IF NOT EXISTS` 패턴), 기존 모델들(`Notice`, `MatchResult` 등).
- 바인딩 결정: D1(BIGINT identity PK, email UNIQUE), D2(email TEXT/소문자), D4(TIMESTAMPTZ created_at), D5(1:1 CASCADE), D6(만원 INTEGER), D7(household_type CHECK enum), D8(지역 목록 JSONB), D9(DATE), D10(bool NOT NULL DEFAULT).

## Steps
1. `src/db.py`에 `Member(Base)` 추가 — `__tablename__="member"`:
   - `id: Mapped[int]` PK `autoincrement=True`(BIGINT).
   - `email: Mapped[str]` `unique=True, nullable=False`(소문자 저장은 데이터액세스 계층 T02/T04 책임 — 여기선 UNIQUE 제약만).
   - `password_hash: Mapped[str]` nullable=False.
   - `created_at: Mapped[datetime]` `server_default=func.now()`, `timezone=True`.
2. `MemberProfile(Base)` 추가 — `__tablename__="member_profile"`:
   - `member_id: Mapped[int]` = `mapped_column(ForeignKey("member.id", ondelete="CASCADE"), primary_key=True, unique=True)` (1:1).
   - 기존 `scoring.Profile` 대응 컬럼(전부, snake_case): `birth_date DATE`, `marriage_date DATE`, `engaged BOOL NOT NULL DEFAULT false`, `is_household_head BOOL NOT NULL DEFAULT false`, `household_all_homeless BOOL NOT NULL DEFAULT true`, `homeless_since DATE`, `dependents INT NOT NULL DEFAULT 0`, `won_within_5y BOOL NOT NULL DEFAULT false`, `children_minor INT NOT NULL DEFAULT 0`, `real_estate_manwon INT NOT NULL DEFAULT 0`, `account_opened DATE`, `account_balance_manwon INT NOT NULL DEFAULT 0`, `income_monthly_manwon INT`(nullable=미입력), `income_base_manwon INT`(nullable), `income_dual BOOL NOT NULL DEFAULT false`, `fl_ever_owned_house BOOL NOT NULL DEFAULT false`, `fl_income_tax_5y BOOL NOT NULL DEFAULT false`, `fl_currently_earning BOOL NOT NULL DEFAULT false`.
   - 신규 컬럼: `car_value_manwon INT NOT NULL DEFAULT 0`, `household_head_owns_home BOOL NOT NULL DEFAULT false`, `household_type` = `mapped_column(String, nullable=False, server_default="general")` + `CheckConstraint("household_type IN ('newlywed','pre_newlywed','youth','general')")`, `is_first_home BOOL NOT NULL DEFAULT false`, `residence_regions`/`income_base_regions`/`interest_regions` = `mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))` (list[str]).
   - `region: Mapped[str]` = `mapped_column(String, nullable=False, server_default="")` (기존 Profile.region 유지 — 예치금/기본 거주 판정용).
3. `init_db()`가 새 테이블/컬럼을 만들도록 확인: `Base.metadata.create_all(engine)`가 신규 테이블을 만든다. 기존 DB에 컬럼 누락 시를 대비해 기존 패턴대로 필요한 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`는 추가하지 않아도 됨(신규 테이블이므로 create_all로 충분) — 단, 함수가 오류 없이 재실행 가능(멱등)해야 한다.
4. import 보강: `from sqlalchemy import CheckConstraint, ForeignKey, func, text` / `from sqlalchemy.dialects.postgresql import JSONB` / `from datetime import datetime, date`(누락분만).

## Deliverables
- `src/db.py` (Member, MemberProfile 모델 + init_db 멱등 유지)
- `tests/test_member_schema.py` (신규)

## Verify
- `uv run pytest tests/test_member_schema.py -q 2>&1 | tail -20` 통과.
- 테스트(`_db_available` 게이트): ① `init_db()` 2회 호출해도 예외 없음(멱등) ② Member+MemberProfile 생성/조회 왕복 ③ 경계: household_type에 허용 외 값 삽입 시 IntegrityError(`with pytest.raises`) ④ 경계: 지역 목록 컬럼 기본값이 `[]`.

## Out of scope
- 데이터액세스 함수/어댑터(Task 02), bookmark 변경(Task 03), 비밀번호 해싱(Task 04).
