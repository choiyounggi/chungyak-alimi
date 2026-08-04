# Task 05: register/login/logout 라우트 + 세션 + require_login

## Objective
`src/web/auth.py`가 회원가입/로그인/로그아웃 라우트와 `require_login` 의존성을 제공하고
`src/web/app.py`가 이를 포함한다. 세션 쿠키로 로그인 상태가 유지되고, 보호 라우트는
비로그인 시 `/login`으로 리다이렉트된다. 기존 basic-auth는 제거된다.

## Wiki pages (read these first, only these)
- wiki/security/authn/session-vs-token.md — 서버 세션 쿠키(httpOnly/Secure/SameSite), 단일 백엔드
- wiki/backend/common/api-design/error-responses.md — 409(중복)/401(로그인 실패)/303(리다이렉트) 코드 선택
- wiki/security/authz/resource-level-checks.md — 세션의 member_id만 신뢰, 요청 id 불신
- wiki/security/input/validation-at-trust-boundaries.md — 폼 입력 검증(email 형식/빈값/길이)

## Inputs
- 기존 `src/web/app.py`: `app`(FastAPI), `SessionMiddleware`(SESSION_SECRET), `settings`(SESSION_HTTPS_ONLY, web_user/web_password=제거 대상), Jinja `templates`(있으면 재사용). 기존 basic-auth 의존성 위치.
- Task 02/04 산출물: `create_member`, `get_member_by_email`, `authenticate_member`, `hash_password`.
- 바인딩 결정: D12(세션 쿠키), D13(SameSite=Lax), D14(세션 member_id), D15(409/401/303), D16(pydantic 검증).

## Steps
1. `src/web/auth.py` 생성. `router = APIRouter()`.
2. 세션 헬퍼: 로그인 시 `request.session["member_id"] = m.id`; 로그아웃 시 `request.session.clear()`. `current_member_id(request) -> int | None` = `request.session.get("member_id")`.
3. 인가 헬퍼(둘 다 정의):
   - `current_member_id(request) -> int | None` = `request.session.get("member_id")`.
   - `require_login(request) -> int` 의존성: `current_member_id`가 None이면 `raise HTTPException(status_code=303, headers={"Location": "/login"})`(FastAPI가 303 리다이렉트로 렌더). 아니면 member_id(int) 반환.
   - HTML 보호 페이지는 `Depends(require_login)`를 쓴다. API성 JSON 라우트(북마크 등, Task 12)는 `require_login` 대신 `current_member_id`를 직접 확인해 None이면 401을 반환한다.
4. 라우트:
   - `GET /register`, `GET /login` → 폼 템플릿 렌더(Task 06 템플릿).
   - `POST /register`(form: email, password): pydantic으로 email/password 검증 → `get_member_by_email`로 중복 확인, 중복이면 폼에 에러 + 409(HTML 폼이면 상태코드 409로 재렌더). 아니면 `create_member(email, hash_password(password))` 후 로그인 세션 설정 + `/`로 303.
   - `POST /login`(form): `authenticate_member` → 성공 시 세션 설정 + `/`로 303, 실패 시 폼 에러 + 401 재렌더.
   - `POST /logout`: 세션 clear + `/login`으로 303.
5. `src/web/app.py`: `app.include_router(auth.router)`; 기존 basic-auth 의존성/설정 사용처 제거(대시보드/북마크 등은 `require_login`으로 대체 — 실제 대시보드 전환은 Task 11, 여기선 라우트 등록 + 미사용 basic-auth 제거).
6. SessionMiddleware 쿠키 옵션 확인: `https_only=settings.session_https_only`, `same_site="lax"`.

## Deliverables
- `src/web/auth.py` (신규)
- `src/web/app.py` (라우터 포함 + basic-auth 제거)
- `tests/test_auth_routes.py` (신규)

## Verify
- `uv run pytest tests/test_auth_routes.py -q 2>&1 | tail -30` 통과.
- 테스트(TestClient, `_db_available` 게이트): ① register→쿠키로 로그인됨→`/`(또는 보호 라우트) 200 ② 중복 email register→409 ③ 잘못된 로그인→401 ④ 경계/에러: 미로그인으로 보호 라우트 GET→303 `/login`, logout 후 재접근→303. ⑤ 빈 email/password→검증 에러(400/폼).

## Out of scope
- 폼 HTML 디자인(Task 06), 프로필 폼(Task 10), 대시보드 회원화(Task 11), 북마크(Task 12).
