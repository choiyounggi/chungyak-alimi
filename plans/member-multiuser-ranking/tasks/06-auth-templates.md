# Task 06: login / register 템플릿 + 네비게이션

## Objective
`login.html`, `register.html`가 base를 확장해 접근성 있는 폼으로 렌더되고,
`base.html` 네비에 로그인 상태에 따른 로그인/회원가입/로그아웃 링크가 표시된다.

## Wiki pages (read these first, only these)
- wiki/frontend/forms/validation-timing.md — 제출 시 검증, 서버 오류를 필드에 매핑
- wiki/frontend/security/xss-safe-rendering.md — 사용자 입력(email 등) 출력은 자동이스케이프
- wiki/frontend/accessibility/interactive-elements.md — label/for, 실제 button, 포커스

## Inputs
- Task 05 산출물: `GET/POST /register`, `GET/POST /login`, `POST /logout`, 폼 에러 컨텍스트 키(예: `error`, `email`), 세션 member_id 노출 방식(템플릿에 `member_id` 또는 `logged_in` 전달).
- 기존 `src/web/templates/base.html`(topnav 구조: `<nav class="topnav">`), 기존 폼/버튼 CSS 클래스.
- 바인딩 결정: D21(폼 검증 타이밍), D22(XSS), D23(접근성).

## Steps
1. `src/web/templates/login.html` 생성: base 확장, `<form method="post" action="/login">` email/password 입력(각 `<label for>`), 에러 있으면 상단/필드에 표시, 회원가입 링크.
2. `src/web/templates/register.html` 생성: 동일 패턴, `action="/register"`, 비밀번호 규칙(≤128) 안내, 로그인 링크. 재렌더 시 이전 email 값 유지(`value="{{ email or '' }}"`).
3. `base.html` topnav 수정: `{% if logged_in %}` 프로필/로그아웃(폼 POST 버튼 또는 링크), `{% else %}` 로그인/회원가입 링크. 로그아웃은 상태변경이므로 `<form method="post" action="/logout">` + button.
4. 값 출력은 Jinja 자동이스케이프 사용(원시 HTML 조립 금지). 에러 메시지는 서버가 정한 고정 문자열 사용(사용자 입력 반사 금지).

## Deliverables
- `src/web/templates/login.html` (신규)
- `src/web/templates/register.html` (신규)
- `src/web/templates/base.html` (네비 수정)
- `tests/test_auth_templates.py` (신규) — 필요 시 base 템플릿 테스트 갱신 포함
- `tests/test_login_template.py` (구 basic-auth login.html 계약 → 새 이메일 기반 login.html 계약으로 교체)

## Verify
- `uv run pytest tests/test_auth_templates.py -q 2>&1 | tail -20` 통과.
- 테스트(TestClient): ① `GET /login` 200 + `name="email"`/`name="password"`/`<label` 포함 ② `GET /register` 200 + 로그인 링크 포함 ③ 에러 경계: 잘못된 로그인 POST 후 응답 HTML에 에러 문구 포함(폼 재표시) ④ 네비: 미로그인 시 "로그인" 링크, 로그인 시 "로그아웃" 폼 노출(로그인 상태 컨텍스트 분기).

## Out of scope
- 라우트 로직(Task 05), 프로필 입력 폼(Task 10).
