# Task 04: 비밀번호 해싱 + authenticate_member (argon2id)

## Objective
`src/members.py`에 argon2id 기반 비밀번호 해싱/검증과 `authenticate_member(email, password)`가
추가되어, 올바른 자격증명만 회원을 반환하고 존재하지 않는 계정도 타이밍이 노출되지 않는다.

## Wiki pages (read these first, only these)
- wiki/security/authn/password-storage.md — argon2id 파라미터, 길이 제한, 더미 검증
- wiki/security/dependencies/supply-chain.md — argon2-cffi 의존성 추가(이름 검증, lockfile)

## Inputs
- Task 02 산출물: `src/members.py`의 `get_member_by_email`, `create_member`.
- 바인딩 결정: D3(argon2id, 입력 ≤128, 더미 검증), D16(경계 검증).

## Steps
1. 의존성 추가: `pyproject.toml`에 `argon2-cffi`(런타임). `uv add argon2-cffi` 또는 `[project].dependencies`에 추가 후 `uv sync`. lockfile 갱신.
2. `src/members.py`에 `from argon2 import PasswordHasher` / `from argon2.exceptions import VerifyMismatchError`. 모듈 수준 `_ph = PasswordHasher()` (기본 파라미터가 OWASP 최소 이상 — 확인만).
3. `hash_password(password: str) -> str`: 길이 검증 `if len(password) > 128: raise ValueError`; `return _ph.hash(password)`.
4. `verify_password(hash_: str, password: str) -> bool`: `try: return _ph.verify(hash_, password)` `except VerifyMismatchError: return False`.
5. `authenticate_member(email, password, *, session) -> Member | None`:
   - `m = get_member_by_email(email, session=session)`.
   - 계정 없으면 고정 더미 해시에 대해 `verify_password`를 호출해 타이밍 은닉 후 None 반환.
   - 있으면 `verify_password(m.password_hash, password)` 결과에 따라 m 또는 None.
6. `create_member`(Task 02) 호출부는 그대로 두고, 라우트(Task 05)에서 `hash_password`로 해시를 만들어 넘긴다.

## Deliverables
- `src/members.py` (해싱/검증/authenticate 함수 추가)
- `pyproject.toml` + `uv.lock` (argon2-cffi)
- `tests/test_auth_password.py` (신규)

## Verify
- `uv run pytest tests/test_auth_password.py -q 2>&1 | tail -20` 통과.
- 테스트: ① hash→verify 왕복 True(해시는 평문과 다르고 매번 salt로 달라짐) ② 틀린 비번 verify False ③ 에러: 129자 비번 hash_password→ValueError ④ 경계: 존재하지 않는 email authenticate_member→None(예외 없이). DB 필요 케이스는 `_db_available` 게이트.

## Out of scope
- 세션/쿠키/라우트(Task 05), 폼(Task 06).
