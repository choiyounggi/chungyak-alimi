"""login/register.html 렌더 스모크 — DB 불필요, 순수 Jinja2.

라우트 동작(상태코드·세션)은 test_auth_routes.py, 라우트를 통한 렌더는
test_auth_templates.py 가 본다. 이 파일은 DB 없이도 돌아가는 템플릿 자체의 계약
(base 상속·폼 구조·에러 상태·이모지 0)을 지킨다. Jinja2Templates와 동일한 templates
디렉토리를 FileSystemLoader로 로드하여 `{% extends "base.html" %}`가 실제 base로
해석되도록 한다(= test_base_template.py 패턴).

Task 06에서 basic-auth(username) 폼이 회원 이메일 폼으로 교체되었다.
"""
from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "web" / "templates"
LOGIN = TEMPLATES / "login.html"
REGISTER = TEMPLATES / "register.html"

# 이모지 블록(1F000–1FAFF) + 기타기호/딩뱃(2600–27BF, ⚠️ U+26A0 포함). CJK/화살표는 제외.
EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]")


def _env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True
    )


def _render(name: str, ctx: dict) -> str:
    return _env().get_template(name).render(ctx)


# ── 정상(happy): 에러 없는 GET /login 컨텍스트 ──
def test_login_renders_without_errors():
    out = _render("login.html", {"error": None, "email": ""})

    # base 상속 증거: base <style> 토큰 + 스프라이트 + main.wrap 존재
    assert "--canvas:#fffaf0" in out, "base <style> 미렌더(상속 실패)"
    assert 'id="i-alert"' in out, "base SVG 스프라이트 미포함(상속 실패)"
    assert 'class="wrap"' in out, "base main.wrap 미포함(content 블록 위치)"

    # 중앙정렬(head 블록 페이지 스타일 주입)
    assert "min-height:100vh" in out
    assert "display:flex" in out
    assert "max-width:380px" in out

    # 브랜드 영역
    assert "청약 알리미" in out
    assert "내 관심 청약을 한눈에" in out
    assert 'class="sub"' in out

    # form 계약(회원 이메일 로그인)
    assert 'method="post"' in out
    assert 'action="/login"' in out
    assert "novalidate" in out
    assert 'name="email"' in out
    assert 'name="password"' in out
    assert 'type="password"' in out
    assert 'autocomplete="email"' in out
    assert 'autocomplete="current-password"' in out
    assert "autofocus" in out
    # 구 basic-auth 아이디 필드는 제거됨
    assert 'name="username"' not in out

    # label/for ↔ input id 연결(접근성)
    assert '<label for="email">' in out
    assert '<label for="password">' in out

    # 제출 버튼 + 회원가입 전환 링크
    assert "btn btn-primary" in out
    assert "로그인" in out
    assert 'href="/register"' in out

    # topbar 빈 override → 기본 워드마크 링크(href="/") 부재
    assert 'href="/"' not in out, "topbar 빈 override 실패(기본 상단바 잔존)"

    # 에러 상태 부재
    assert 'class="form-error"' not in out
    assert 'aria-invalid="true"' not in out
    assert 'role="alert"' not in out


# ── 경계값 + error-case: 폼 레벨 에러 + email 값 보존 ──
def test_login_shows_form_level_error():
    out = _render(
        "login.html",
        {"error": "이메일 또는 비밀번호가 올바르지 않습니다", "email": "me@example.com"},
    )

    # form 배너 + i-alert 아이콘(이모지 아님)
    assert 'class="form-error"' in out
    assert "이메일 또는 비밀번호가 올바르지 않습니다" in out
    assert 'href="#i-alert"' in out, "경고는 #i-alert SVG로 표시되어야 함"

    # 보조기술 전달: role=alert + aria-invalid + 에러 영역 참조
    assert 'role="alert"' in out
    assert 'id="login-error"' in out
    m = re.search(r'<input[^>]*name="email"[^>]*>', out, re.S)
    assert m, "email input 태그 파싱 실패"
    assert 'aria-invalid="true"' in m.group(0)
    assert 'aria-describedby="login-error"' in m.group(0)

    # 입력값 보존(비밀번호는 절대 되돌려주지 않는다)
    assert 'value="me@example.com"' in out
    assert 'value="{}"'.format("") not in out.split('name="password"')[1][:120]


# ── error-case: 누락 키에도 예외 없이 렌더(경계) ──
def test_login_renders_with_missing_context_keys():
    out = _render("login.html", {})

    assert 'name="email"' in out
    assert 'value=""' in out  # email 미제공 → 빈 값
    assert 'class="form-error"' not in out


# ── error-case: 사용자 입력은 이스케이프되어 출력된다(XSS) ──
def test_login_escapes_email_value():
    out = _render("login.html", {"error": None, "email": '"><script>alert(1)</script>'})

    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out or "&amp;lt;" in out


# ── error-case(DoD): 이모지 0개(소스 + 렌더) ──
@pytest.mark.parametrize("name", ["login.html", "register.html"])
def test_auth_templates_have_no_emoji(name):
    raw = (TEMPLATES / name).read_text(encoding="utf-8")
    assert EMOJI_RE.findall(raw) == [], f"{name} 소스에 이모지 잔존"

    out = _render(name, {"error": "오류", "email": ""})
    assert EMOJI_RE.findall(out) == [], f"{name} 렌더 출력에 이모지 잔존"
    assert 'href="#i-alert"' in out


# ── 상속/블록 구조(소스 레벨) ──
@pytest.mark.parametrize(
    ("path", "title"),
    [(LOGIN, "로그인 · 청약 알리미"), (REGISTER, "회원가입 · 청약 알리미")],
)
def test_auth_templates_extend_base_with_blocks(path, title):
    raw = path.read_text(encoding="utf-8")
    first = next(line for line in raw.splitlines() if line.strip())
    assert first.strip() == '{% extends "base.html" %}', "첫 줄이 extends base가 아님"
    assert "{% block topbar %}{% endblock %}" in raw, "topbar 빈 override 누락"
    assert title in raw, "title override 누락"
    assert "{% block head %}" in raw
    assert "{% block content %}" in raw


# ── base 네비: 미로그인(request 없음) 시 로그인/회원가입 링크 ──
def test_base_nav_shows_auth_links_when_anonymous():
    """request 가 없는 직접 렌더에서도 예외 없이 미로그인 네비가 나와야 한다."""
    out = _render("base.html", {})

    assert 'href="/login"' in out
    assert 'href="/register"' in out
    assert 'action="/logout"' not in out


# ── register.html: 로그인 전환 링크 + 비밀번호 규칙 안내 ──
def test_register_renders_form_and_login_link():
    out = _render("register.html", {"error": None, "email": ""})

    assert 'action="/register"' in out
    assert 'name="email"' in out
    assert 'autocomplete="new-password"' in out
    assert '<label for="email">' in out
    assert 'href="/login"' in out
    assert "128자 이하" in out
