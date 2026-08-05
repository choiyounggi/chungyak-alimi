"""KISA 기준 비밀번호 정책 검증(Task 02) — src/password_policy.py.

순수함수이므로 DB·설정이 필요 없다 → _db_available 게이트를 두지 않는다.

각 케이스는 사유 리스트의 **정확한 개수**와 **해당 규칙의 고유 키워드**를 함께 단언한다.
개수를 단언하는 이유: 위반이 여럿일 때 하나만 반환하고 끝내는 구현을 잡기 위해서다.
"""
from __future__ import annotations

import pytest

from src.members import MAX_PASSWORD_LEN
from src.password_policy import POLICY_HINTS, validate_password

# 규칙별 고유 키워드(D17). 9개 문장 사이에서 서로 겹치지 않는다.
KW_COMBO = "2가지 이상"
KW_MIN10 = "10자 이상"
KW_MIN8 = "8자 이상"
KW_MAX = "128자"
KW_REPEAT = "잇달아"
KW_SEQUENTIAL = "순서대로"
KW_KEYBOARD = "키보드"
KW_EMAIL = "이메일 아이디"
KW_WEAK = "흔히 쓰이는"


def _has(reasons: list[str], keyword: str) -> bool:
    return any(keyword in r for r in reasons)


def _long(n: int) -> str:
    """길이만 검사하기 위한 n자 비밀번호 — 4종 조합, 회피 패턴 없음."""
    unit = "aQ7!"
    return (unit * (n // len(unit) + 1))[:n]


# --------------------------------------------------------------------------
# ① 정상
# --------------------------------------------------------------------------


def test_valid_password_returns_empty_list():
    """4종 조합 10자 + 회피 패턴 없음 → 통과."""
    assert validate_password("Ch!ngyak24") == []


def test_email_none_skips_local_part_rule():
    """email=None 이면 로컬파트 규칙을 건너뛴다(기본값 경로)."""
    assert validate_password("dch0202!A", email=None) == []


# --------------------------------------------------------------------------
# ② 에러 — 규칙마다 독립된 사유로 검출
# --------------------------------------------------------------------------


def test_single_char_class_rejected_regardless_of_length():
    """소문자 1종은 20자여도 조합 위반. 길이는 20 ≥ 10 이므로 사유는 1개뿐이다."""
    reasons = validate_password("mkthbrqzjwmkthbrqzjw")
    assert _has(reasons, KW_COMBO)
    assert len(reasons) == 1


def test_triple_repeat_rejected():
    """동일 문자 3연속(aaa/bbb) — 두 번 걸려도 사유 문장은 1개."""
    reasons = validate_password("aaabbb1!")
    assert _has(reasons, KW_REPEAT)
    assert len(reasons) == 1


def test_sequential_and_keyboard_runs_are_separate_reasons():
    """abc(사전순) + 숫자행 123(사전순·키보드 동시 해당) → 두 규칙이 각각 잡힌다."""
    reasons = validate_password("abc12345!")
    assert _has(reasons, KW_SEQUENTIAL)
    assert _has(reasons, KW_KEYBOARD)
    assert len(reasons) == 2


def test_keyboard_run_rejected():
    """qwe(키보드) + 123(사전순) — 키보드 규칙이 독립 사유로 잡힌다."""
    reasons = validate_password("qwe12345!")
    assert _has(reasons, KW_KEYBOARD)
    assert _has(reasons, KW_SEQUENTIAL)
    assert len(reasons) == 2


def test_descending_sequential_run_rejected():
    """사전순 연속은 감소 방향(fed)도 잡는다.

    감소 숫자열(321)은 역순 숫자행에도 걸려 두 규칙이 동시에 발동하므로,
    사전순 규칙만 격리하기 위해 키보드 행에 없는 알파벳 역순을 쓴다.
    """
    reasons = validate_password("Ch!nfed4")
    assert _has(reasons, KW_SEQUENTIAL)
    assert len(reasons) == 1


def test_reversed_keyboard_run_rejected():
    """키보드 인접은 역순(ewq)도 잡는다."""
    reasons = validate_password("Ch!ngewq")
    assert _has(reasons, KW_KEYBOARD)
    assert len(reasons) == 1


def test_email_local_part_substring_rejected():
    """이메일 로컬파트의 3자 부분문자열(dch)이 비밀번호에 들어 있으면 위반."""
    reasons = validate_password("dch0202!A", email="dch0202@gmail.com")
    assert _has(reasons, KW_EMAIL)
    assert len(reasons) == 1


def test_email_local_part_matched_case_insensitively():
    """로컬파트 대조는 대소문자를 무시한다."""
    reasons = validate_password("DCH0202!a", email="dch0202@gmail.com")
    assert _has(reasons, KW_EMAIL)
    assert len(reasons) == 1


def test_weak_password_rejected_with_all_other_violations():
    """'password' = 취약목록 + 1종 조합 + 10자 미만 → 사유 3개 전부."""
    reasons = validate_password("password")
    assert _has(reasons, KW_WEAK)
    assert _has(reasons, KW_COMBO)
    assert _has(reasons, KW_MIN10)
    assert len(reasons) == 3


def test_weak_password_matched_case_insensitively():
    """취약목록 대조는 대소문자를 무시한다(PASSWORD == password)."""
    assert _has(validate_password("PASSWORD"), KW_WEAK)


# --------------------------------------------------------------------------
# ③ 경계
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "expected_keyword"),
    [
        ("Ch!ngya4", None),  # 4종 · 정확히 8자 → 통과
        ("Ch!ngy4", KW_MIN8),  # 4종 · 7자 → 하한 미달
        ("m4kty9hbr2", None),  # 2종 · 정확히 10자 → 통과
        ("m4kty9hbr", KW_MIN10),  # 2종 · 9자 → 하한 미달
        (_long(128), None),  # 정확히 128자 → 통과
        (_long(129), KW_MAX),  # 129자 → 상한 초과
    ],
)
def test_length_boundaries_off_by_one(password: str, expected_keyword: str | None):
    """길이 규칙의 경계와 그 한 칸 바깥(7/8, 9/10, 128/129)."""
    reasons = validate_password(password)
    if expected_keyword is None:
        assert reasons == []
    else:
        assert _has(reasons, expected_keyword)
        assert len(reasons) == 1


def test_empty_string_rejected():
    """빈 문자열: 조합 위반 + 길이 하한 위반. 회피 패턴은 길이가 없어 발동하지 않는다."""
    reasons = validate_password("")
    assert _has(reasons, KW_COMBO)
    assert _has(reasons, KW_MIN10)
    assert len(reasons) == 2


def test_whitespace_only_rejected():
    """공백만: 공백은 어느 문자 종류에도 속하지 않고, 동일 문자 3연속에는 걸린다."""
    reasons = validate_password("   ")
    assert _has(reasons, KW_COMBO)
    assert _has(reasons, KW_MIN10)
    assert _has(reasons, KW_REPEAT)
    assert len(reasons) == 3


def test_non_ascii_only_counts_toward_length_but_no_char_class():
    """한글은 길이에만 기여하고 문자 종류로 세지 않는다."""
    reasons = validate_password("청약알리미비밀번호")  # 9자
    assert _has(reasons, KW_COMBO)
    assert _has(reasons, KW_MIN10)
    assert len(reasons) == 2


def test_surrounding_whitespace_is_not_stripped():
    """앞뒤 공백을 제거하지 않는다 — 제거했다면 8자가 되어 통과했을 입력이다."""
    # 본체 6자는 3종 이상 하한(8자) 미달이다.
    assert _has(validate_password("Ch!ng4"), KW_MIN8)
    # 앞뒤 공백 2자가 길이에 포함되어 8자가 되므로 같은 본체가 통과한다.
    # strip 했다면 여기서도 하한 미달이 나와야 한다.
    assert validate_password(" Ch!ng4 ") == []


@pytest.mark.parametrize("email", ["", "  ", "ab@x.com", "nodomain", "@gmail.com"])
def test_email_rule_skipped_when_local_part_unusable(email: str):
    """빈 이메일 · @ 없음 · 로컬파트 3자 미만이면 로컬파트 규칙은 발동하지 않는다."""
    assert validate_password("Ch!ngyak24", email=email) == []


def test_over_max_length_still_reports_other_violations():
    """129자여도 조기 종료하지 않는다 — 상한 + 동일 3연속을 함께 반환."""
    reasons = validate_password("a" * 129)
    assert _has(reasons, KW_MAX)
    assert _has(reasons, KW_COMBO)
    assert _has(reasons, KW_REPEAT)
    assert len(reasons) == 3


# --------------------------------------------------------------------------
# ④ 복수 위반 전량 반환
# --------------------------------------------------------------------------


def test_multiple_violations_are_all_returned():
    """'aaa': 조합 + 길이 + 동일 3연속 → 첫 위반에서 멈추지 않는다."""
    reasons = validate_password("aaa")
    assert _has(reasons, KW_COMBO)
    assert _has(reasons, KW_MIN10)
    assert _has(reasons, KW_REPEAT)
    assert len(reasons) == 3


def test_four_violations_are_all_returned():
    """'qwerty': 조합 + 길이 + 키보드 + 취약목록."""
    reasons = validate_password("qwerty")
    assert _has(reasons, KW_COMBO)
    assert _has(reasons, KW_MIN10)
    assert _has(reasons, KW_KEYBOARD)
    assert _has(reasons, KW_WEAK)
    assert len(reasons) == 4
    assert len(reasons) == len(set(reasons)), "같은 사유 문장이 중복되면 안 된다"


def test_every_reason_is_a_nonempty_korean_sentence():
    """사유는 사용자에게 그대로 노출되므로 빈 문자열/코드가 아니어야 한다."""
    reasons = validate_password("qwerty")
    assert reasons
    for r in reasons:
        assert isinstance(r, str)
        assert r.strip()


# --------------------------------------------------------------------------
# ⑤ POLICY_HINTS 계약
# --------------------------------------------------------------------------


def test_policy_hints_contract():
    """t04 가 체크리스트로 그대로 렌더한다 — 비어 있지 않은 문자열 튜플."""
    assert isinstance(POLICY_HINTS, tuple)
    assert POLICY_HINTS
    for hint in POLICY_HINTS:
        assert isinstance(hint, str)
        assert hint.strip()


def test_policy_hints_use_shared_max_length_constant():
    """길이 상한은 members.MAX_PASSWORD_LEN 을 재사용한다(재정의 금지)."""
    assert any(f"{MAX_PASSWORD_LEN}자" in hint for hint in POLICY_HINTS)
