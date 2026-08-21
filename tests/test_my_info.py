"""내정보 화면·계정 섹션(비밀번호 변경) — postgres 필요, _db_available 게이트(Task t2).

라우트 검증 순서: 현재 비밀번호 확인(401) → 정책 위반·확인 불일치(400) → 해시 교체·커밋.
비밀번호는 실패 응답 어디에도 재표시하지 않는다(password_field 매크로 계약, D5).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from src.db import Member, MemberProfile, SessionLocal, engine, init_db
from src.members import get_member_by_email
from src.web.app import app

from test_auth_routes import BASE_URL, login_client

EMAIL = "myinfo-tester@example.com"
# 가입 경계가 KISA 정책을 강제하므로(Task 04) 픽스처 비밀번호도 정책을 통과해야 한다.
PASSWORD = "Vu8#mQ2rTz"
NEW_PASSWORD = "Zt7@Kd4Wnq"


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


def _reset() -> None:
    init_db()
    with SessionLocal() as s:
        for t in (MemberProfile, Member):
            s.execute(delete(t))
        s.commit()


def _password_hash(email: str = EMAIL) -> str:
    with SessionLocal() as s:
        m = get_member_by_email(email, session=s)
        assert m is not None, f"회원 없음: {email}"
        return m.password_hash


@pytest.fixture
def client():
    _reset()
    yield login_client(EMAIL, PASSWORD)
    _reset()


# ── ① 정상: 변경 → 303, 새 비밀번호로 로그인 성공·옛 비밀번호는 실패 ──────────


def test_password_change_succeeds_and_rotates_login(client):
    r = client.post(
        "/profile/password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/profile?pw_saved=1"

    ok = TestClient(app, base_url=BASE_URL).post(
        "/login", data={"email": EMAIL, "password": NEW_PASSWORD}, follow_redirects=False
    )
    assert ok.status_code == 303
    assert ok.headers["location"] == "/"

    fail = TestClient(app, base_url=BASE_URL).post(
        "/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert fail.status_code == 401


# ── ② 현재 비밀번호 불일치 → 401, 해시 불변, 비밀번호 값 재표시 금지 ──────────


def test_wrong_current_password_returns_401_and_leaves_hash_unchanged(client):
    before = _password_hash()
    r = client.post(
        "/profile/password",
        data={
            "current_password": "wrong-password-1",
            "new_password": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
    )
    assert r.status_code == 401
    assert "현재 비밀번호가 올바르지 않습니다" in r.text
    assert _password_hash() == before
    assert NEW_PASSWORD not in r.text
    assert "wrong-password-1" not in r.text


# ── ③ 정책 위반 → 400, 인라인 오류, 해시 불변 ─────────────────────────────────


def test_policy_violation_returns_400_inline_error(client):
    before = _password_hash()
    r = client.post(
        "/profile/password",
        data={
            "current_password": PASSWORD,
            "new_password": "weakpassword",
            "new_password2": "weakpassword",
        },
    )
    assert r.status_code == 400
    assert "영문 대문자" in r.text
    assert _password_hash() == before


# ── ④ 새 비밀번호 확인 불일치 → 400 ───────────────────────────────────────────


def test_confirm_mismatch_returns_400(client):
    before = _password_hash()
    r = client.post(
        "/profile/password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD + "x",
        },
    )
    assert r.status_code == 400
    assert "비밀번호와 비밀번호 확인이 다릅니다" in r.text
    assert _password_hash() == before


# ── ⑤ 경계값: 빈 입력 → 400 ───────────────────────────────────────────────────


def test_blank_inputs_return_400(client):
    before = _password_hash()
    r = client.post(
        "/profile/password",
        data={"current_password": "", "new_password": "", "new_password2": ""},
    )
    assert r.status_code == 400
    assert "입력값을 확인해주세요" in r.text
    assert _password_hash() == before


# ── ⑥ 인가: 미로그인 → 303 /login ─────────────────────────────────────────────


def test_requires_login(client):
    anon = TestClient(app, base_url=BASE_URL)
    r = anon.post(
        "/profile/password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# ── ⑦ GET /profile: 계정 섹션에 이메일 노출 + 비밀번호 변경 폼 ────────────────


def test_get_profile_shows_account_section_and_email(client):
    r = client.get("/profile")
    assert r.status_code == 200
    assert "계정" in r.text
    assert EMAIL in r.text
    assert "이메일은 변경할 수 없습니다" in r.text
    assert 'name="current_password"' in r.text
    assert 'name="new_password"' in r.text
    assert 'name="new_password2"' in r.text
    # 섹션 카드화(t4 D3): base 의 테두리 없는 .section 을 이 페이지에서만 카드로 재정의
    assert (
        ".section{background:var(--color-surface);border:1px solid var(--color-line);"
        "border-radius:var(--r-lg);box-shadow:var(--shadow-1);" in r.text
    )
    # select 토큰 이관 — 새 이름(--color-line/--color-canvas)만 참조
    assert "border:1px solid var(--color-line);border-radius:var(--r-md);" in r.text
    assert "background:var(--color-canvas);color:var(--color-ink);" in r.text


# ── topnav: 로그인 상태 대시보드 응답에 '내정보' 링크 노출 ────────────────────


def test_dashboard_shows_my_info_nav_link_when_logged_in(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/profile"' in r.text
    assert "내정보" in r.text
