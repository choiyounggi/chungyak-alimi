"""가입 경계 강화(이메일·KISA 비밀번호·확인) + 마스킹 토글 (Task 04) — postgres 필요.

라우트 계약(상태코드·리다이렉트·필드 에러)과 렌더된 마크업(토글 버튼·규칙 체크리스트)을
같은 파일에서 본다. 픽스처·클라이언트는 test_auth_routes 의 것을 재사용한다
(기존 `from test_applyhome import SAMPLE` 관례와 동일한 테스트 간 임포트).
"""
from __future__ import annotations

import pytest

from src.db import Member, SessionLocal
from src.members import create_member, hash_password
from src.password_policy import POLICY_HINTS, validate_password
from src.web.auth import _FIELD_MESSAGES, _PASSWORD_MISMATCH

from test_auth_routes import (  # noqa: F401  (clean_members 는 픽스처로 재사용)
    EMAIL,
    PASSWORD,
    _client,
    _db_available,
    clean_members,
)

pytestmark = [
    pytest.mark.skipif(not _db_available(), reason="postgres 미가용"),
    pytest.mark.usefixtures("clean_members"),
]

# 정책 시행 이전에 만들어진 계정의 비밀번호(P4 회귀용) — 지금 기준으로는 가입이 거부되는 값.
LEGACY_EMAIL = "legacy-member@example.com"
LEGACY_PASSWORD = "pw-12345"


def _register(email: str = EMAIL, password: str = PASSWORD, password2: str | None = None):
    data = {
        "email": email,
        "password": password,
        "password2": password if password2 is None else password2,
    }
    return _client().post("/register", data=data, follow_redirects=False)


def _member_count() -> int:
    with SessionLocal() as s:
        return s.query(Member).count()


# ── ① 정상: 가입 성공 → 303 /onboarding/1 ───────────────────────────────────


def test_register_success_redirects_to_onboarding():
    r = _register()

    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/1"
    assert _member_count() == 1


# ── ② 에러: 이메일 형식 / 정책 미달 / 확인 불일치 / 중복 ────────────────────


@pytest.mark.parametrize("bad_email", ["", "user@", "user@x", "not-an-email"])
def test_register_rejects_invalid_email(bad_email):
    r = _register(email=bad_email)

    assert r.status_code == 400
    assert _FIELD_MESSAGES["email"] in r.text
    assert _member_count() == 0


def test_register_shows_every_policy_violation():
    """P5: 어떤 규칙을 어겼는지 사유를 하나도 빠뜨리지 않고 보여준다."""
    reasons = validate_password(LEGACY_PASSWORD, email=EMAIL)
    assert len(reasons) >= 2, "정책 미달 사유가 2건 이상인 표본이어야 '전부 노출'을 검증할 수 있다"

    r = _register(password=LEGACY_PASSWORD)

    assert r.status_code == 400
    for reason in reasons:
        assert reason in r.text, f"위반 사유 누락: {reason}"
    assert 'id="e-password"' in r.text
    assert _member_count() == 0


def test_register_rejects_password_confirmation_mismatch():
    r = _register(password=PASSWORD, password2=PASSWORD + "x")

    assert r.status_code == 400
    assert _PASSWORD_MISMATCH in r.text
    assert 'id="e-password2"' in r.text
    assert _member_count() == 0


def test_register_duplicate_email_returns_409():
    assert _register().status_code == 303

    r = _register()

    assert r.status_code == 409
    assert "이미 가입된 이메일입니다" in r.text
    assert _member_count() == 1  # 중복 행이 생기지 않았다


def test_duplicate_email_marks_only_the_email_field_invalid():
    """중복은 이메일 문제다 — 비밀번호 칸까지 invalid 로 표시하면 어디를 고칠지 오도한다."""
    assert _register().status_code == 303

    r = _register()

    assert r.status_code == 409
    email_field = r.text[r.text.index('id="email"') : r.text.index('id="password"')]
    assert 'aria-invalid="true"' in email_field
    # 비밀번호 두 칸에는 invalid 표식이 없다
    password_fields = r.text[r.text.index('id="password"') : r.text.index("</form>")]
    assert 'aria-invalid="true"' not in password_fields
    assert 'class="invalid"' not in password_fields


# ── ③ 경계: 상한 초과 / 빈 확인값 / 비밀번호 미반사 ─────────────────────────


def test_register_rejects_password_over_max_length():
    long_pw = "a" * 129

    r = _register(password=long_pw)

    assert r.status_code == 400
    assert _FIELD_MESSAGES["password"] in r.text
    assert _member_count() == 0


def test_register_rejects_empty_password2():
    r = _register(password=PASSWORD, password2="")

    assert r.status_code == 400
    assert _FIELD_MESSAGES["password2"] in r.text
    assert _member_count() == 0


@pytest.mark.parametrize(
    ("kind", "email", "password", "password2", "expected_status"),
    [
        # 실패 경로 4가지 전부 — 어느 경로로 재렌더되든 비밀번호는 새어나가지 않아야 한다
        ("형식", "not-an-email", PASSWORD, PASSWORD, 400),
        ("정책", EMAIL, LEGACY_PASSWORD, LEGACY_PASSWORD, 400),
        ("불일치", EMAIL, PASSWORD, PASSWORD + "x", 400),
        ("중복", EMAIL, PASSWORD, PASSWORD, 409),
    ],
)
def test_failed_register_preserves_email_and_never_echoes_password(
    kind, email, password, password2, expected_status
):
    """재렌더는 이메일만 되살린다 — 비밀번호는 응답 본문 어디에도 남기지 않는다."""
    if kind == "중복":
        assert _register().status_code == 303  # 먼저 같은 이메일로 가입해 둔다

    r = _client().post(
        "/register",
        data={"email": email, "password": password, "password2": password2},
        follow_redirects=False,
    )

    assert r.status_code == expected_status
    # 이메일은 보존되어 다시 타이핑하지 않아도 된다(자동이스케이프된 형태로)
    assert f'value="{email}"' in r.text
    assert password not in r.text
    assert password2 not in r.text


# ── ④ 회귀(P4): 로그인은 정책을 재검증하지 않는다 ───────────────────────────


def test_login_accepts_legacy_password_below_policy():
    """정책 시행 전 계정을 잠그지 않는다 — 같은 비밀번호로 가입은 막히지만 로그인은 된다."""
    with SessionLocal() as s:
        create_member(LEGACY_EMAIL, hash_password(LEGACY_PASSWORD), session=s)

    login = _client().post(
        "/login",
        data={"email": LEGACY_EMAIL, "password": LEGACY_PASSWORD},
        follow_redirects=False,
    )
    assert login.status_code == 303

    # 같은 비밀번호로 새로 가입하는 것은 여전히 거부된다(정책은 가입 경계에만 적용)
    signup = _register(email="another@example.com", password=LEGACY_PASSWORD)
    assert signup.status_code == 400


# ── ⑤ 마크업: 마스킹 토글 + 규칙 체크리스트 ────────────────────────────────


def test_register_page_renders_masking_toggles():
    r = _client().get("/register")

    assert r.status_code == 200
    # 비밀번호 + 비밀번호 확인 두 칸 모두 토글을 가진다
    assert r.text.count('class="pw-toggle"') == 2
    assert 'aria-controls="password"' in r.text
    assert 'aria-controls="password2"' in r.text
    # 상태 토글은 링크가 아니라 실제 button + aria 상태 + 아이콘 전용이므로 aria-label 필수
    assert 'type="button"' in r.text
    assert 'aria-pressed="false"' in r.text
    assert 'aria-label="비밀번호 표시"' in r.text
    assert 'href="#i-eye"' in r.text
    # JS 없이도 masked 상태로 제출된다
    assert r.text.count('type="password"') == 2


def test_register_page_renders_every_policy_hint():
    r = _client().get("/register")

    assert r.status_code == 200
    assert 'id="pw-rules"' in r.text
    for hint in POLICY_HINTS:
        assert hint in r.text, f"규칙 안내 누락: {hint}"


def test_login_page_renders_masking_toggle():
    r = _client().get("/login")

    assert r.status_code == 200
    assert r.text.count('class="pw-toggle"') == 1
    assert 'aria-controls="password"' in r.text
    assert 'href="#i-eye"' in r.text
    assert 'aria-label="비밀번호 표시"' in r.text
    # 로그인 폼에는 확인 칸도, 규칙 체크리스트도 없다
    assert 'name="password2"' not in r.text
    assert 'id="pw-rules"' not in r.text
