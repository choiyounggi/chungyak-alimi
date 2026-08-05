"""login/register 템플릿 + 네비게이션(Task 06) — 라우트를 통해 실제 렌더된 HTML을 검증.

순수 Jinja 렌더 계약(상속·이모지 등)은 tests/test_login_template.py 가 맡고,
이 파일은 라우트 응답(에러 재표시·로그인 상태별 네비)을 본다 → postgres 필요.
"""
from __future__ import annotations

import re

import pytest

from test_auth_routes import (  # noqa: F401  (clean_members 는 픽스처로 재사용)
    EMAIL,
    PASSWORD,
    _client,
    _db_available,
    clean_members,
    login_client,
)

# 모든 케이스를 빈 회원 테이블에서 시작한다(test_auth_routes 의 픽스처 재사용).
pytestmark = [
    pytest.mark.skipif(not _db_available(), reason="postgres 미가용"),
    pytest.mark.usefixtures("clean_members"),
]


# ── ① GET /login: 라벨 있는 email/password 폼 ────────────────────────────────


def test_login_page_has_labelled_email_and_password_fields():
    r = _client().get("/login")

    assert r.status_code == 200
    assert 'name="email"' in r.text
    assert 'name="password"' in r.text
    assert 'type="password"' in r.text
    # label/for 가 각 입력의 id 와 연결되어야 스크린리더가 필드명을 읽는다
    assert '<label for="email">' in r.text
    assert '<label for="password">' in r.text
    assert 'id="email"' in r.text
    assert 'id="password"' in r.text
    # 구 basic-auth 폼의 아이디 필드는 사라졌다
    assert 'name="username"' not in r.text


def test_login_page_links_to_register():
    r = _client().get("/login")

    assert 'href="/register"' in r.text
    assert "회원가입" in r.text


# ── ② GET /register: 라벨 폼 + 로그인 링크 + 비밀번호 규칙 안내 ──────────────


def test_register_page_has_labelled_fields_and_login_link():
    r = _client().get("/register")

    assert r.status_code == 200
    assert '<label for="email">' in r.text
    assert '<label for="password">' in r.text
    assert 'action="/register"' in r.text
    assert 'href="/login"' in r.text
    assert "128자 이하" in r.text  # 비밀번호 규칙 안내


# ── ③ 에러 경계: 로그인 실패 후 폼이 에러와 함께 재표시된다 ─────────────────


def test_failed_login_rerenders_form_with_error():
    r = _client().post(
        "/login", data={"email": EMAIL, "password": "wrong"}, follow_redirects=False
    )

    assert r.status_code == 401
    assert "이메일 또는 비밀번호가 올바르지 않습니다" in r.text
    # 폼이 다시 보여야 재시도할 수 있다
    assert 'action="/login"' in r.text
    assert 'name="password"' in r.text
    # 에러 상태가 보조기술에 전달된다
    assert 'role="alert"' in r.text
    assert 'aria-invalid="true"' in r.text


def test_duplicate_register_rerenders_form_and_keeps_email():
    _client().post(
        "/register", data={"email": EMAIL, "password": PASSWORD, "password2": PASSWORD}
    )

    r = _client().post(
        "/register",
        data={"email": EMAIL, "password": PASSWORD, "password2": PASSWORD},
        follow_redirects=False,
    )

    assert r.status_code == 409
    assert "이미 가입된 이메일입니다" in r.text
    # 재렌더 시 이전 email 값 유지 → 다시 타이핑하지 않아도 된다
    assert f'value="{EMAIL}"' in r.text


def test_register_error_page_does_not_reflect_user_input_as_html():
    """XSS 경계: 입력한 값은 이스케이프되어 원시 태그로 렌더되지 않는다."""
    r = _client().post(
        "/register",
        data={
            "email": '<script>alert(1)</script>@x',
            "password": PASSWORD,
            "password2": PASSWORD,
        },
        follow_redirects=False,
    )

    assert r.status_code == 400
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text  # 자동이스케이프된 형태로만 등장


# ── ④ 네비게이션: 로그인 상태에 따른 분기 ───────────────────────────────────


def test_login_page_never_shows_logout_form():
    """미로그인 상태에서 로그아웃 폼이 노출되면 안 된다.

    (미로그인 네비의 로그인/회원가입 링크 자체는 base.html 을 직접 렌더하는
    test_login_template.py::test_base_nav_shows_auth_links_when_anonymous 가 본다 —
    로그인 페이지는 topbar 를 빈 블록으로 override 하므로 네비가 렌더되지 않는다.)
    """
    r = _client().get("/login")

    assert 'action="/logout"' not in r.text


def test_nav_shows_logout_form_when_logged_in():
    client = login_client()

    r = client.get("/")

    assert r.status_code == 200
    # 로그아웃은 상태 변경이므로 링크가 아니라 POST 폼 + 실제 button
    assert 'action="/logout"' in r.text
    assert 'method="post"' in r.text
    logout_form = re.search(r'<form[^>]*action="/logout"[^>]*>.*?</form>', r.text, re.S)
    assert logout_form, "로그아웃 폼을 찾지 못함"
    assert "<button" in logout_form.group(0)
    assert 'href="/logout"' not in r.text  # GET 링크로 로그아웃하지 않는다
    # 로그인 상태에선 로그인/회원가입 링크를 감춘다
    assert 'href="/register"' not in r.text


def test_nav_returns_to_login_links_after_logout():
    """경계: 로그아웃하면 네비가 다시 미로그인 상태로 돌아간다."""
    client = login_client()
    client.post("/logout", follow_redirects=False)

    r = client.get("/login")

    assert 'action="/logout"' not in r.text
    assert 'href="/register"' in r.text
