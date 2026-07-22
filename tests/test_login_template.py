"""login.html 렌더 스모크 — DB 불필요, 순수 Jinja2.

기존 test_web.py는 postgres-gated(미가용 시 skip)이므로, 로그인 페이지의 상속·폼·에러
상태 계약은 독립 실행 가능한 이 파일로 검증한다. Jinja2Templates와 동일한 templates
디렉토리를 FileSystemLoader로 로드하여 `{% extends "base.html" %}`가 실제 base로
해석되도록 한다(= test_base_template.py 패턴).
"""
from __future__ import annotations

import re
from pathlib import Path

import jinja2

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "web" / "templates"
LOGIN = TEMPLATES / "login.html"

# 이모지 블록(1F000–1FAFF) + 기타기호/딩뱃(2600–27BF, ⚠️ U+26A0 포함). CJK/화살표는 제외.
EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]")


def _env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True
    )


def _render(ctx: dict) -> str:
    return _env().get_template("login.html").render(ctx)


# ── 정상(happy): 에러 없는 GET /login 컨텍스트 ──
def test_login_renders_without_errors():
    out = _render({"errors": {}, "username": ""})

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

    # form 계약(로직 보존)
    assert 'method="post"' in out
    assert 'action="/login"' in out
    assert "novalidate" in out
    assert 'name="username"' in out
    assert 'name="password"' in out
    assert 'type="password"' in out
    assert 'autocomplete="username"' in out
    assert 'autocomplete="current-password"' in out
    assert "autofocus" in out

    # 제출 버튼
    assert "btn btn-primary" in out
    assert "로그인" in out

    # topbar 빈 override → 기본 워드마크 링크(href="/") 부재
    assert 'href="/"' not in out, "topbar 빈 override 실패(기본 상단바 잔존)"

    # 에러 상태 부재 (base CSS의 `.field.has-error` 셀렉터와 구분: 마크업 적용형은 공백 표기)
    assert 'class="form-error"' not in out
    assert "field has-error" not in out
    # 어떤 input에도 invalid 클래스가 적용되지 않음(base `input.invalid` 셀렉터와 구분)
    assert 'class="invalid"' not in out


# ── 경계값 + error-case: form + username 에러, password 키 누락 ──
def test_login_shows_inline_errors():
    ctx = {
        "errors": {
            "form": "아이디 또는 비밀번호가 올바르지 않습니다",
            "username": "아이디를 입력해주세요",
        },
        "username": "me",
    }
    out = _render(ctx)

    # form 배너 + i-alert 아이콘(이모지 아님)
    assert 'class="form-error"' in out
    assert "아이디 또는 비밀번호가 올바르지 않습니다" in out
    assert 'href="#i-alert"' in out, "경고는 #i-alert SVG로 표시되어야 함"

    # username 필드 에러 3상태
    assert "has-error" in out
    assert "아이디를 입력해주세요" in out
    # username input에 invalid 클래스가 붙었는지(해당 input 태그 추출 후 확인)
    m = re.search(r'<input[^>]*name="username"[^>]*>', out)
    assert m, "username input 태그 파싱 실패"
    assert "invalid" in m.group(0), "username 에러 시 input.invalid 누락"

    # 입력값 보존
    assert 'value="me"' in out

    # 누락 키(password) 안전: 예외 없이 렌더 + password 에러 메시지 부재
    assert "비밀번호를 입력해주세요" not in out


# ── error-case(DoD): 이모지 0개(소스 + 렌더) ──
def test_login_has_no_emoji():
    raw = LOGIN.read_text(encoding="utf-8")
    assert EMOJI_RE.findall(raw) == [], "login.html 소스에 이모지 잔존(⚠️ 제거 필요)"

    # 에러 있는 렌더 출력에도 이모지 0 + 경고는 아이콘으로 대체
    out = _render(
        {"errors": {"form": "오류"}, "username": ""}
    )
    assert EMOJI_RE.findall(out) == [], "렌더 출력에 이모지 잔존"
    assert 'href="#i-alert"' in out


# ── 상속/블록 구조(소스 레벨) ──
def test_login_extends_base_with_blocks():
    raw = LOGIN.read_text(encoding="utf-8")
    first = next(line for line in raw.splitlines() if line.strip())
    assert first.strip() == '{% extends "base.html" %}', "첫 줄이 extends base가 아님"
    assert "{% block topbar %}{% endblock %}" in raw, "topbar 빈 override 누락"
    assert "로그인 · 청약 알리미" in raw, "title override 누락"
    assert "{% block head %}" in raw
    assert "{% block content %}" in raw
