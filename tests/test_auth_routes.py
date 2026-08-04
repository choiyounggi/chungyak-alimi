"""회원 인증 라우트 + require_login (Task 05) — postgres 필요, _db_available 게이트.

`login_client()` 는 보호 라우트(`/`)를 호출하는 다른 테스트 모듈에서도 재사용한다
(기존 `from test_applyhome import SAMPLE` 관례와 동일한 테스트 간 임포트).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from src.db import Member, MemberProfile, SessionLocal, engine, init_db
from src.web.app import app

EMAIL = "tester@example.com"
PASSWORD = "pw-12345"


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


def _client() -> TestClient:
    """세션 쿠키가 Secure 이므로 https 로 호출해야 브라우저처럼 쿠키가 되돌아온다."""
    return TestClient(app, base_url="https://testserver")


def _clear_members() -> None:
    init_db()
    with SessionLocal() as s:
        for t in (MemberProfile, Member):
            s.execute(delete(t))
        s.commit()


@pytest.fixture
def clean_members():
    _clear_members()
    yield
    _clear_members()


def login_client(email: str = EMAIL, password: str = PASSWORD) -> TestClient:
    """회원가입(이미 있으면 로그인)으로 세션 쿠키를 확보한 TestClient — 보호 라우트 테스트용."""
    client = _client()
    r = client.post(
        "/register", data={"email": email, "password": password}, follow_redirects=False
    )
    if r.status_code == 409:  # 앞선 테스트가 만들어둔 계정 → 로그인으로 대체
        r = client.post(
            "/login", data={"email": email, "password": password}, follow_redirects=False
        )
    assert r.status_code == 303, f"로그인 헬퍼 실패: {r.status_code}"
    return client


# ── ① 정상: 회원가입 → 세션 → 보호 라우트 접근 ──────────────────────────────


def test_register_logs_in_and_grants_access_to_protected_route(clean_members):
    client = _client()

    r = client.post(
        "/register", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"

    # 가입 직후 세션 쿠키만으로 대시보드 접근
    dash = client.get("/", follow_redirects=False)
    assert dash.status_code == 200


def test_login_after_register_grants_access(clean_members):
    _client().post("/register", data={"email": EMAIL, "password": PASSWORD})

    client = _client()  # 쿠키 없는 새 클라이언트
    r = client.post(
        "/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert r.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 200


def test_login_is_case_insensitive_for_email(clean_members):
    """경계: 대문자로 가입해도 소문자 이메일로 로그인된다(정규화)."""
    _client().post("/register", data={"email": "Mixed@Example.COM", "password": PASSWORD})

    client = _client()
    r = client.post(
        "/login", data={"email": "mixed@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303


# ── ② 에러: 중복 가입 409 ───────────────────────────────────────────────────


def test_duplicate_register_returns_409(clean_members):
    _client().post("/register", data={"email": EMAIL, "password": PASSWORD})

    r = _client().post(
        "/register", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert r.status_code == 409
    assert "이미 가입된 이메일입니다" in r.text

    with SessionLocal() as s:
        assert s.query(Member).count() == 1  # 중복 행이 생기지 않았다


# ── ③ 에러: 잘못된 로그인 401 ───────────────────────────────────────────────


def test_login_with_wrong_password_returns_401(clean_members):
    _client().post("/register", data={"email": EMAIL, "password": PASSWORD})

    client = _client()
    r = client.post(
        "/login", data={"email": EMAIL, "password": "wrong-password"}, follow_redirects=False
    )
    assert r.status_code == 401
    assert "이메일 또는 비밀번호가 올바르지 않습니다" in r.text
    # 실패한 로그인은 세션을 만들지 않는다
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_with_unknown_email_returns_401(clean_members):
    r = _client().post(
        "/login", data={"email": "ghost@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 401


# ── ④ 경계: 미로그인 보호 라우트 → 303 /login, 로그아웃 후 재차단 ───────────


def test_protected_route_redirects_to_login_when_anonymous(clean_members):
    r = _client().get("/", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_logout_clears_session_and_reblocks_protected_route(clean_members):
    client = login_client()

    out = client.post("/logout", follow_redirects=False)
    assert out.status_code == 303
    assert out.headers["location"] == "/login"

    after = client.get("/", follow_redirects=False)
    assert after.status_code == 303
    assert after.headers["location"] == "/login"


# ── ⑤ 경계/에러: 폼 입력 검증 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "", "password": PASSWORD},          # 빈 이메일
        {"email": EMAIL, "password": ""},             # 빈 비밀번호
        {"email": "not-an-email", "password": PASSWORD},  # 형식 위반
        {"email": EMAIL, "password": "a" * 129},      # 길이 상한 초과
    ],
)
def test_register_rejects_invalid_input(clean_members, payload):
    r = _client().post("/register", data=payload, follow_redirects=False)

    assert r.status_code == 400
    with SessionLocal() as s:
        assert s.query(Member).count() == 0  # 검증 실패는 회원을 만들지 않는다


def test_login_rejects_empty_credentials(clean_members):
    r = _client().post(
        "/login", data={"email": "", "password": ""}, follow_redirects=False
    )

    assert r.status_code == 400


# ── 폼 페이지 렌더 ──────────────────────────────────────────────────────────


def test_login_and_register_pages_render(clean_members):
    assert _client().get("/login").status_code == 200
    r = _client().get("/register")
    assert r.status_code == 200
    assert 'name="email"' in r.text
    assert 'name="password"' in r.text


# ── 세션 쿠키 속성(httpOnly / Secure / SameSite) ────────────────────────────


def test_session_cookie_is_httponly_secure_samesite_lax(clean_members):
    """세션 쿠키는 JS 접근 불가(httponly) + HTTPS 전용(secure) + SameSite=Lax 여야 한다."""
    r = _client().post(
        "/register", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )

    set_cookie = r.headers["set-cookie"].lower()
    assert "session=" in set_cookie
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie


def test_logout_expires_the_session_cookie(clean_members):
    """로그아웃 응답은 세션 쿠키를 비운다(서버 세션 clear 와 짝)."""
    client = login_client()

    out = client.post("/logout", follow_redirects=False)

    assert "session=" in out.headers.get("set-cookie", "")
    assert not client.cookies.get("session")


# ── 세션 신뢰 경계: 요청이 보낸 member_id 는 믿지 않는다 ────────────────────


def test_member_id_from_request_body_is_ignored(clean_members):
    """세션에 없는 member_id 를 폼으로 보내도 인증되지 않는다(세션만 신뢰)."""
    r = _client().post(
        "/login", data={"email": EMAIL, "password": PASSWORD, "member_id": "1"},
        follow_redirects=False,
    )

    assert r.status_code == 401
