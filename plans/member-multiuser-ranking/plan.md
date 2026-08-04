# member-multiuser-ranking

Goal: 정식 회원가입/로그인(멀티유저)을 도입하고, 회원별 리치 프로필을 DB에 저장해
**로그인 회원 기준**으로 맞춤 청약 순위·매칭을 보여준다. 예비신혼부부의
**거주지 ∪ 소득 본거지**가 공고 지역과 매칭되면 **해당지역 1순위**로 인정(사용자 지정 정책).
지역은 전체 오픈, 회원 관심지역이 기본 필터이되 "전체 보기"로 타 지역 열람.

Acceptance:
- 회원가입/로그인/로그아웃 동작(중복 이메일 거부, 잘못된 로그인 거부, 비로그인 시 보호 페이지 리다이렉트).
- 로그인 회원 프로필로 대시보드 순위가 요청 시점에 계산됨.
- 거주지 또는 소득본거지가 공고 지역과 매칭되면 해당지역 1순위로 표시.
- 북마크가 회원별로 격리됨(타 회원 북마크 비노출).
- 기존 단일 `config/profile.yaml`이 영기 계정으로 이관됨.

Stack: Python 3 · FastAPI · Jinja2 · SQLAlchemy 2.0 · PostgreSQL(psycopg3) · pydantic · uv. 테스트 pytest(+ `_db_available` 게이트).

설계 스펙: `docs/superpowers/specs/2026-08-04-member-multiuser-ranking-design.md`

## Decisions

| # | Decision | Choice | Wiki basis |
|---|----------|--------|------------|
| D1 | member PK 타입 | BIGINT identity(내부 전용 — member id는 URL/API에 노출 안 함, 리소스는 세션의 현재 회원 기준으로만 접근). email은 UNIQUE 자연키 | databases-schema-design-primary-key-choice |
| D2 | email 타입 | `String`(TEXT) + UNIQUE, 소문자 정규화 저장. PII로 취급(로그/URL 금지) | databases-schema-design-column-data-types, security-data-pii-handling |
| D3 | 비밀번호 해싱 | argon2id(argon2-cffi, OWASP 최소 m=19456,t=2,p=1). 입력 길이 ≤128 제한, 존재하지 않는 계정도 더미 검증으로 타이밍 은닉 | security-authn-password-storage |
| D4 | 시각 컬럼 | `TIMESTAMPTZ`(created_at) default now(), UTC 저장 | databases-schema-design-column-data-types |
| D5 | member_profile 관계 | member와 1:1(member_id FK+UNIQUE), ON DELETE CASCADE(프로필은 회원의 구조적 일부) | databases-schema-design-foreign-keys-and-referential-actions |
| D6 | 만원 단위 금액 | INTEGER(예치금·소득·부동산가액·차량가액 — 만원 정수 단위, 기존 코드 관례 유지) | databases-schema-design-column-data-types |
| D7 | 세대유형 | TEXT + `CHECK IN ('newlywed','pre_newlywed','youth','general')` (닫힌 소집합, native ENUM 회피) | databases-schema-design-column-data-types |
| D8 | 지역 목록 컬럼 | JSONB(list[str]) — residence_regions/income_base_regions/interest_regions. 필터·매칭은 파이썬(온더플라이)에서 수행하므로 SQL 조인 불필요 | databases-schema-design-column-data-types |
| D9 | 날짜 컬럼 | `DATE`(birth_date/marriage_date/homeless_since) | databases-schema-design-column-data-types |
| D10 | 불리언/기본값 | 모든 bool은 NOT NULL DEFAULT. 3-상태면 CHECK enum으로 분리 | databases-schema-design-nullability-and-defaults |
| D11 | bookmark 회원별 | member_id FK(ON DELETE CASCADE) 추가, PK=(member_id, pblanc_no) 복합. 기존 전역 북마크는 시드 시 영기 회원으로 이관 | databases-schema-design-primary-key-choice(junction), foreign-keys-and-referential-actions |
| D12 | 인증 방식 | 서버 세션 쿠키(httpOnly, Secure=SESSION_HTTPS_ONLY, SameSite=Lax). JWT 미사용(단일 백엔드 동일 출처 브라우저 앱). 기존 SessionMiddleware 재사용 | security-authn-session-vs-token |
| D13 | CSRF | SameSite=Lax 쿠키 + 동일 출처 fetch(상태변경 라우트는 same-origin). 별도 토큰 미도입 | frontend-auth-token-handling-client-side |
| D14 | 리소스 인가 | 회원 범위 쿼리는 항상 세션의 member_id로 필터(소유권을 쿼리에 내장), 요청이 준 member id 신뢰 금지. not-found는 일관 처리 | security-authz-resource-level-checks |
| D15 | 에러 응답 | 중복 이메일 409/폼 에러, 잘못된 로그인 401/폼 재표시, 비로그인 보호페이지 303 → /login | backend-common-api-design-error-responses |
| D16 | 경계 입력 검증 | 폼 입력은 pydantic으로 경계에서 검증(빈값/형식/범위), 타입힌트만 신뢰 금지 | backend-python-boundaries-runtime-validation, security-input-validation-at-trust-boundaries |
| D17 | 순위 계산 방식 | 요청 시점 온더플라이(scoring 순수함수) — 회원×공고 배치 사전계산 안 함 | [no-wiki] 아키텍처 판단(순수함수·소규모·최신성) |
| D18 | 지역 매칭 규칙 | 시/도 정규화(예 "서울특별시"↔"서울") + 시 단위 예외 맵(성남 등). area_nm 실제 값은 T08에서 표본조사 후 정규화표 확정 | [no-wiki] 도메인 |
| D19 | 소득본거지 인정 | 사용자 지정 정책(공식 청약 규칙 아님)임을 docstring/주석에 명시 | [no-wiki] 도메인 정책 |
| D20 | "전체 보기" 필터 상태 | 클라이언트 UI 상태(기본=관심지역만, 토글 시 전체). 서버는 전체 목록 제공 | frontend-state-client-vs-server-state |
| D21 | 폼 검증 타이밍 | 제출 시 검증, 서버 검증 오류를 필드에 매핑해 표시 | frontend-forms-validation-timing |
| D22 | XSS | 회원/프로필 값 출력은 Jinja 자동이스케이프 + JS는 textContent/데이터속성(문자열 HTML 조립 금지) | frontend-security-xss-safe-rendering |
| D23 | 상호작용 요소 접근성 | 토글/버튼은 실제 button + aria 상태 | frontend-accessibility-interactive-elements |
| D24 | 테스트 | DB 필요 테스트는 `_db_available` 게이트, 파일당 정상+에러+경계 ≥3, 격리 준수 | testing-quality-minimum-case-set, testing-data-test-data-and-isolation |

## Task order

| Task | 개요 | Depends on | Parallel-ok |
|------|------|-----------|-------------|
| 01 | member/member_profile 모델 + init_db 스키마 | — | |
| 02 | member 데이터액세스 + Profile 어댑터(members.py) | 01 | |
| 03 | bookmark 회원별 스키마 + 이관 함수 | 01 | |
| 04 | 비밀번호 해싱 + authenticate_member(argon2id) | 02 | |
| 05 | register/login/logout 라우트 + 세션 + require_login | 04 | Phase1 |
| 06 | login/register 템플릿 + 네비 | 05 | Phase1 |
| 07 | profile.yaml→영기 계정 시드 + 기존 북마크 이관 스크립트 | 03,04 | Phase1 |
| 08 | 지역 정규화 유틸(area_nm 표본조사 + 예외맵) | 01 | Phase2 |
| 09 | judge_rank 해당지역 1순위(거주지∪소득본거지) | 08 | Phase2 |
| 10 | 프로필 입력/수정 폼 라우트+템플릿 | 06,07,09 | |
| 11 | 회원별 대시보드 온더플라이 순위 + 관심지역 필터/전체보기 | 09,10 | |
| 12 | 회원별 북마크 엔드포인트/목록 격리 | 03,11 | |

- **Phase 0** = 01→02→03 (전부 선행, db.py/members.py 순차).
- **Phase 1** (05,06,07) ∥ **Phase 2** (08,09): 파일 범위 disjoint(auth/템플릿/스크립트 vs scoring/regions) → 별도 세션 병렬 가능.
- **Phase 3** = 10→11→12 (app.py 순차, Phase1·2 완료 후).
