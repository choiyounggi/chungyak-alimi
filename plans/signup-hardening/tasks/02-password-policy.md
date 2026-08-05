# Task 02: KISA 기준 비밀번호 정책 모듈

## Objective
KISA 「암호 이용 안내서」의 패스워드 선택 기준을 순수함수로 구현한다.
DB·요청 객체에 의존하지 않으므로 라우트·스크립트·테스트가 같은 함수를 공유한다.

## Wiki pages (read these first, only these)
- security/authn/password-storage.md — 정책 검증은 해싱 전, 길이 상한의 이유
- security/input/validation-at-trust-boundaries.md — 경계에서 한 번 검증
- testing/quality/minimum-case-set.md — 규칙별 정상/위반/경계 케이스

## Inputs
- 기존 `src/members.py` `MAX_PASSWORD_LEN = 128`(import 해서 쓰고 재정의 금지).
- 바인딩 결정: P1(조합·길이), P2(회피 패턴), P3(길이 상한), P5(사유 전량 노출), P6(모듈 위치).

## Steps
1. `src/password_policy.py` 신규. 공개 API는 **하나**:
   ```python
   def validate_password(password: str, *, email: str | None = None) -> list[str]:
   ```
   빈 리스트 = 통과. 각 원소는 사용자에게 그대로 노출할 한국어 문장(P5).
2. 문자 종류 판별: 영대문자 / 영소문자 / 숫자 / 특수문자(ASCII 33~47,58~64,91~96,123~126).
   - 종류 1개 → 위반("영문 대소문자·숫자·특수문자 중 2가지 이상을 조합해주세요")
   - 종류 2개 → 10자 미만이면 위반
   - 종류 3개 이상 → 8자 미만이면 위반
   - 128자 초과 → 위반(P3)
3. 회피 패턴(P2) — 각각 독립된 사유로:
   - 동일 문자 3연속(`aaa`, `111`)
   - 사전순 연속 3자 — 증가·감소 양방향(`abc`, `cba`, `789`, `321`)
   - 키보드 인접 3연속 — QWERTY 행 문자열(`qwertyuiop`/`asdfghjkl`/`zxcvbnm`)과
     숫자행(`1234567890`)의 부분열, 역순 포함. 대소문자 무시.
   - 이메일 로컬파트 포함 — `email` 이 주어지면 `@` 앞부분의 **3자 이상 연속 부분문자열**이
     비밀번호에 (대소문자 무시하고) 들어 있으면 위반
   - 내장 취약 비밀번호 목록 완전일치(대소문자 무시) — `password`, `qwerty`, `12345678`,
     `iloveyou`, `admin123` 등 소수의 상수 튜플. 외부 사전 파일을 읽지 않는다.
4. 공백만으로 이루어졌거나 빈 문자열이면 조합·길이 규칙에서 자연히 걸리되,
   앞뒤 공백은 **제거하지 않는다**(비밀번호의 공백은 유효한 문자다). 이 점을 docstring에 명시.
5. 규칙 텍스트를 클라이언트 힌트와 공유할 수 있도록 `POLICY_HINTS: tuple[str, ...]`
   (사람이 읽는 규칙 요약)도 함께 노출한다.

## Deliverables
- `src/password_policy.py` (신규)
- `tests/test_password_policy.py` (신규)

## Verify
- `uv run pytest tests/test_password_policy.py -q 2>&1 | tail -20` 통과.
- `uv run ruff check src tests 2>&1 | tail -5` clean.
- 테스트(DB 불필요): ① 정상 — `"Ch!ngyak24"`(4종·10자) 통과, 3종 8자 통과, 2종 10자 통과
  ② 에러 — 2종 9자 / 1종 20자 / `"aaabbb1!"`(동일 3연속) / `"abc12345!"`(연속 3자) /
  `"qwe12345!"`(키보드 3연속) / email=`"dch0202@gmail.com"` 일 때 `"dch0202!A"` 포함 위반 /
  `"password"` 목록 일치 — 각각 사유 리스트가 비어 있지 않고 해당 규칙 문장을 포함
  ③ 경계 — 빈 문자열, 공백 3자, 정확히 8자 3종(통과), 정확히 7자 3종(위반),
  정확히 10자 2종(통과), 정확히 9자 2종(위반), 129자(위반), `email=None`(로컬파트 규칙 건너뜀)
  ④ 위반이 2개 이상일 때 사유가 **모두** 반환되는지(한 개만 반환하고 끝내지 않음).

## Out of scope
- 라우트 연결(Task 04), 비밀번호 변경 페이지(계획 밖), 기존 회원 소급 검증(P4로 제외).
