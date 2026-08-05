# Task 04: 가입 경계 강화(이메일·KISA 비밀번호·확인) + 마스킹 토글

## Objective
`POST /register` 가 이메일 유효성, KISA 비밀번호 정책, 비밀번호 확인 일치를 강제하고
실패 사유를 필드 인라인으로 보여준다. 회원가입·로그인 폼의 비밀번호 칸에서
눈 아이콘으로 마스킹을 토글할 수 있다. 가입 성공은 `/onboarding/1` 로 이어진다.

## Wiki pages (read these first, only these)
- security/input/validation-at-trust-boundaries.md — 폼 경계 검증, 에러 문구 반사 금지
- frontend/forms/validation-timing.md — 제출 시 검증 + 서버 오류의 필드 매핑
- frontend/accessibility/interactive-elements.md — 토글은 실제 button + aria 상태
- frontend/security/xss-safe-rendering.md — 값 재표시, JS는 textContent

## Inputs
- **Task 02 산출물(계약)**:
  `from ..password_policy import POLICY_HINTS, validate_password`
  `validate_password(password: str, *, email: str | None = None) -> list[str]` — 빈 리스트=통과.
- 기존 `src/web/auth.py` `Credentials` / `_render` / `register_submit` / `login_submit`,
  고정 에러 문구 상수(`_LOGIN_FAILED` / `_INVALID_INPUT` / `_DUPLICATE_EMAIL`).
- 기존 `src/web/templates/register.html` / `login.html` / `base.html`(SVG 심볼 정의부·CSS 토큰).
- 바인딩 결정: E1(EmailStr, deliverability 조회 안 함), E2(정규화 범위), E3(의존성),
  P4(로그인은 정책 미검증), P5(사유 전량 노출), U1/U2/U3(토글·아이콘·힌트).

## Steps
1. `pyproject.toml` 의 의존성 `pydantic>=2.7` 을 `pydantic[email]>=2.7` 로 바꾸고
   `uv sync` 로 락파일을 갱신한다(`email-validator` 확보).
2. `src/web/auth.py`:
   - `Credentials.email` 을 정규식에서 `EmailStr` 로 교체(길이 상한 254는 유지).
     기존 `_EMAIL_PATTERN` 상수는 사용처가 사라지면 제거한다.
   - 회원가입 전용 모델 `RegisterForm`(email: EmailStr, password, password2) 추가.
     `model_validator(mode="after")` 로 ① `password != password2` → 확인 불일치 에러
     ② `validate_password(password, email=email)` 결과가 비어 있지 않으면 그 사유들을 에러로.
     **로그인은 기존 `Credentials` 를 그대로 쓴다**(P4 — 정책 재검증 금지).
   - `register_submit` 이 `password2` 폼 필드를 받고, 실패 시 `errors: dict[str, str|list]`
     로 필드별 사유(`email` / `password` / `password2`)를 템플릿에 넘긴다. 비밀번호 값은
     **재표시하지 않는다**(이메일만 보존).
   - 성공 시 리다이렉트 대상을 `/` → `/onboarding/1` 로 변경(303).
   - 중복 이메일 409, 형식 오류 400 은 기존 상태코드 유지.
3. `src/web/templates/base.html`: SVG 심볼 `#i-eye`(보임) / `#i-eye-off`(가림) 추가.
   기존 심볼 정의 블록과 같은 방식으로만 넣는다.
4. 비밀번호 필드 마크업을 매크로화(`templates/_macros.html` 에 `password_field(...)`):
   래퍼 `div.pw-wrap` + `input` + 우측 `button.pw-toggle`
   (`type="button"`, `aria-pressed="false"`, `aria-label="비밀번호 표시"`, `aria-controls=<input id>`).
   `register.html`(password, password2)과 `login.html`(password)에 적용.
5. `register.html`: 비밀번호 규칙 체크리스트를 `POLICY_HINTS` 로 렌더(`<ul id="pw-rules">`),
   서버가 준 필드 에러를 각 필드 슬롯에 인라인 표시(다중 사유는 `<ul>`).
6. `base.html` 의 `{% block scripts %}` 가 아니라 각 페이지 스크립트에 토글 JS:
   클릭 시 `input.type` 을 `password↔text` 로, `aria-pressed`/`aria-label`/`<use href>` 갱신.
   문자열 HTML 조립 금지(`textContent`/속성 조작만). JS가 없어도 폼은 정상 제출된다.

## Deliverables
- `pyproject.toml`, `uv.lock`
- `src/web/auth.py`
- `src/web/templates/base.html`, `_macros.html`, `register.html`, `login.html`
- `tests/test_auth_register_policy.py` (신규)

## Verify
- `uv run pytest tests/test_auth_register_policy.py tests/test_auth_routes.py tests/test_auth_templates.py tests/test_login_template.py -q 2>&1 | tail -20` 통과
  (**기존 인증 테스트 회귀 금지**).
- `uv run ruff check src tests 2>&1 | tail -5` clean.
- 테스트(TestClient, `_db_available` 게이트): ① 정상 — 유효 이메일 + 정책 통과 비밀번호 +
  일치하는 확인 → 303 이고 `Location` 이 `/onboarding/1`
  ② 에러 — `"user@"`/`"user@x"` 등 잘못된 이메일 400, 정책 미달 비밀번호 400 + 응답 본문에
  위반 사유 문자열 포함, 확인 불일치 400, 중복 이메일 409
  ③ 경계 — 비밀번호 129자 400, `password2` 미전송(빈 문자열) 400, 실패 재렌더 시 이메일은
  보존되고 **비밀번호 값은 응답 본문에 없음**
  ④ 로그인 회귀 — 정책에 미달하는 기존 비밀번호로도 `POST /login` 성공(P4)
  ⑤ 마크업 — 렌더된 register/login HTML에 `type="button"`, `aria-pressed`, `#i-eye` 존재.

## Out of scope
- 온보딩 라우트/템플릿(Task 05) — 여기서는 리다이렉트 대상만 `/onboarding/1` 로 바꾼다.
- 비밀번호 변경 페이지, 기존 회원 소급 검증(P4로 제외).
