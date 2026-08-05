"""KISA 「암호 이용 안내서」 패스워드 선택 기준 검증(Task 02) — 순수함수.

DB·요청 객체·설정에 의존하지 않으므로 라우트·스크립트·테스트가 같은 함수를 쓴다.
해싱 전 평문에 대해서만 동작한다(정책 검증 → 통과 시 argon2id 해싱).

앞뒤 공백을 제거하지 않는다 — 공백도 유효한 비밀번호 문자이며, 경계에서 입력을 변형하면
사용자가 입력한 비밀번호와 저장되는 비밀번호가 달라진다.

문자 종류는 영대문자 / 영소문자 / 숫자 / 특수문자(ASCII 33-47, 58-64, 91-96, 123-126)
4가지다. 공백(32)과 비ASCII 문자(한글 등)는 어느 종류에도 속하지 않고 길이에만 기여한다.

공개 API 는 validate_password 와 POLICY_HINTS 둘뿐이다.
"""
from __future__ import annotations

from .members import MAX_PASSWORD_LEN

# 회피 패턴 판정용 키보드 배열(QWERTY 3행 + 숫자행). 역순도 함께 검사한다.
_KEYBOARD_ROWS: tuple[str, ...] = (
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "1234567890",
)

# 흔히 쓰이는 비밀번호 — 완전일치만 본다. 외부 사전 파일·패키지를 도입하지 않는다.
_WEAK_PASSWORDS: tuple[str, ...] = (
    "password",
    "passw0rd",
    "qwerty",
    "qwerty123",
    "123456",
    "12345678",
    "1234567890",
    "iloveyou",
    "admin",
    "admin123",
    "letmein",
    "welcome",
    "abc123",
    "chungyak",
)

# 사유 문장은 "무엇이 틀렸나"가 아니라 "무엇을 하면 되나"로 쓴다(기존 web/auth.py 어투).
_MSG_COMBO = "영문 대문자·소문자·숫자·특수문자 중 2가지 이상을 섞어주세요"
_MSG_MIN_LEN_2 = "영문·숫자·특수문자 중 2가지 조합이면 10자 이상으로 만들어주세요"
_MSG_MIN_LEN_3 = "3가지 이상을 조합할 때는 8자 이상으로 만들어주세요"
_MSG_MAX_LEN = f"비밀번호는 {MAX_PASSWORD_LEN}자 이하로 입력해주세요"
_MSG_REPEAT = "같은 문자를 3번 잇달아 쓰지 말아주세요"
_MSG_SEQUENTIAL = "abc·321 처럼 순서대로 이어지는 3자를 피해주세요"
_MSG_KEYBOARD = "qwe·asd 처럼 키보드에서 나란한 3자를 피해주세요"
_MSG_EMAIL_LOCAL = "이메일 아이디와 겹치는 부분을 빼주세요"
_MSG_WEAK = "흔히 쓰이는 비밀번호입니다. 다른 문자열로 바꿔주세요"

# 회원가입 화면의 규칙 체크리스트로 그대로 렌더된다(판정은 서버의 validate_password 가 한다).
POLICY_HINTS: tuple[str, ...] = (
    "영문 대문자·소문자·숫자·특수문자 중 2가지 이상 조합",
    "2가지 조합은 10자 이상, 3가지 이상 조합은 8자 이상",
    f"최대 {MAX_PASSWORD_LEN}자",
    "같은 문자 3번 연속(aaa) 금지",
    "순서대로 이어지는 3자(abc·321) 금지",
    "키보드에서 나란한 3자(qwe·asd) 금지",
    "이메일 아이디와 겹치는 3자 이상 금지",
    "흔히 쓰이는 비밀번호 금지",
)


def _char_classes(password: str) -> int:
    """비밀번호에 쓰인 문자 종류 수(0~4). 공백·비ASCII 는 어느 종류에도 넣지 않는다."""
    upper = lower = digit = special = False
    for ch in password:
        code = ord(ch)
        if "A" <= ch <= "Z":
            upper = True
        elif "a" <= ch <= "z":
            lower = True
        elif "0" <= ch <= "9":
            digit = True
        elif 33 <= code <= 47 or 58 <= code <= 64 or 91 <= code <= 96 or 123 <= code <= 126:
            special = True
    return sum((upper, lower, digit, special))


def _has_triple_repeat(lower: str) -> bool:
    """같은 문자 3연속(aaa, 111, 공백 3개)."""
    for i in range(len(lower) - 2):
        if lower[i] == lower[i + 1] == lower[i + 2]:
            return True
    return False


def _has_sequential_run(lower: str) -> bool:
    """코드포인트가 1씩 증가하거나 감소하는 3연속(abc, cba, 789, 321)."""
    for i in range(len(lower) - 2):
        first = ord(lower[i + 1]) - ord(lower[i])
        second = ord(lower[i + 2]) - ord(lower[i + 1])
        if first == second and first in (1, -1):
            return True
    return False


def _has_keyboard_run(lower: str) -> bool:
    """키보드 행에서 나란한 3연속(qwe, asd, 123). 역순(ewq, 321)도 위반이다."""
    for i in range(len(lower) - 2):
        window = lower[i : i + 3]
        for row in _KEYBOARD_ROWS:
            if window in row or window in row[::-1]:
                return True
    return False


def _contains_email_local(lower: str, email: str | None) -> bool:
    """이메일 로컬파트(@ 앞)의 3자 이상 연속 부분문자열이 비밀번호에 들어 있는가.

    3자 이상 공통 부분문자열은 반드시 길이 3 창을 포함하므로 길이 3 창만 훑으면 충분하다.
    이메일이 없거나 로컬파트가 3자 미만이면 규칙이 성립하지 않으므로 건너뛴다.
    """
    if not email:
        return False
    local = email.strip().lower().split("@")[0]
    if len(local) < 3:
        return False
    for i in range(len(local) - 2):
        if local[i : i + 3] in lower:
            return True
    return False


def validate_password(password: str, *, email: str | None = None) -> list[str]:
    """KISA 기준 위반 사유 목록. 빈 리스트 = 통과.

    각 원소는 사용자에게 그대로 노출할 완성된 한국어 문장이다.
    위반이 여러 개면 **전부** 반환한다 — 첫 위반에서 중단하면 사용자가 한 번에
    하나씩만 고치게 되고, 정책은 어차피 공개 정보라 숨길 이유가 없다.

    앞뒤 공백은 제거하지 않는다(공백도 유효한 비밀번호 문자다).
    문자 종류를 셀 때 공백과 비ASCII 문자는 어느 종류에도 포함하지 않고 길이에만 기여한다.

    email 을 주면 로컬파트(@ 앞)와 3자 이상 겹치는지도 함께 본다. None 이면 그 규칙만 건너뛴다.
    """
    reasons: list[str] = []

    classes = _char_classes(password)
    if classes <= 1:
        reasons.append(_MSG_COMBO)

    # 종류가 0~1개일 때도 2종 기준(10자)을 함께 알려준다 —
    # 조합을 고치는 최선의 경로가 종류를 하나 늘려 2종이 되는 것이기 때문이다.
    min_len = 8 if classes >= 3 else 10
    if len(password) < min_len:
        reasons.append(_MSG_MIN_LEN_3 if min_len == 8 else _MSG_MIN_LEN_2)

    # 상한을 넘겨도 여기서 끝내지 않는다 — 나머지 사유도 함께 모아 반환한다.
    if len(password) > MAX_PASSWORD_LEN:
        reasons.append(_MSG_MAX_LEN)

    lower = password.lower()
    if _has_triple_repeat(lower):
        reasons.append(_MSG_REPEAT)
    if _has_sequential_run(lower):
        reasons.append(_MSG_SEQUENTIAL)
    if _has_keyboard_run(lower):
        reasons.append(_MSG_KEYBOARD)
    if _contains_email_local(lower, email):
        reasons.append(_MSG_EMAIL_LOCAL)
    if lower in _WEAK_PASSWORDS:
        reasons.append(_MSG_WEAK)

    return reasons
