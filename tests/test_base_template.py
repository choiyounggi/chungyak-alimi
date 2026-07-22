"""base.html 렌더 스모크 — DB 불필요, 순수 Jinja2.

기존 test_web.py는 postgres-gated(미가용 시 skip)이므로, 공유 파운데이션 계약은
독립 실행 가능한 이 파일로 검증한다. Jinja2Templates와 동일한 templates 디렉토리를
FileSystemLoader로 로드해 base 상속을 확인한다.
"""
from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "web" / "templates"
BASE = TEMPLATES / "base.html"

# 브리프 [SVG 아이콘 스프라이트] — 다운스트림이 참조하는 18개 id 전부.
ICON_IDS = [
    "i-pin", "i-calendar", "i-won", "i-home", "i-award", "i-doc", "i-clip",
    "i-image", "i-map", "i-search", "i-target", "i-arrow-left", "i-arrow-right",
    "i-gift", "i-building", "i-ruler", "i-clock", "i-alert",
]

# 브리프 [색상/라운드/간격/폰트] — :root 토큰 정의(선언부, `--name:` 형태로 존재 검증).
REQUIRED_TOKEN_DEFS = [
    "--canvas:", "--surface-soft:", "--surface-card:", "--surface-strong:",
    "--ink:", "--body-strong:", "--body:", "--muted:", "--muted-soft:",
    "--hairline:", "--hairline-soft:",
    "--primary:", "--primary-active:", "--primary-disabled:", "--on-primary:",
    "--accent-teal:", "--on-teal:", "--accent-peach:", "--accent-peach-soft:",
    "--ok:", "--warn:", "--danger:",
    "--r-xs:", "--r-sm:", "--r-md:", "--r-lg:", "--r-xl:", "--r-pill:",
    "--s-xxs:", "--s-xs:", "--s-sm:", "--s-md:", "--s-lg:", "--s-xl:",
    "--s-xxl:", "--s-section:", "--font-sans:",
]

# 브리프 [컴포넌트 클래스] — 다운스트림(Wave 2)이 계약으로 의존하는 셀렉터 전부.
# base(기본) 클래스와 변형(modifier)을 모두 명시적으로 검증한다.
REQUIRED_SELECTORS = [
    ".topbar", ".topbar .inner", ".topbar a", ".brand", ".wrap",
    ".btn", ".btn-primary", ".btn-secondary",
    ".card", ".section",
    ".badge", ".badge--soon", ".badge--mid", ".badge--far", ".badge--pre",
    ".tag",
    ".rank", ".rank-1", ".rank-2",
    ".chip", "button.chip", "button.chip:hover", "button.chip.active",
    ".table",
    "label", "input", "input:focus", "input.invalid",
    ".field", ".field-error", ".field.has-error .field-error", ".form-error",
    ".footer", ".empty", ".ic", ".muted", ".sub", ".display",
]

# 브리프 [Jinja 블록 계약] — 정확히 이 6개.
REQUIRED_BLOCKS = ["title", "head", "topbar", "content", "footer", "scripts"]


def _env(child: str | None = None) -> jinja2.Environment:
    loaders: list[jinja2.BaseLoader] = [jinja2.FileSystemLoader(str(TEMPLATES))]
    if child is not None:
        loaders.insert(0, jinja2.DictLoader({"_child.html": child}))
    return jinja2.Environment(loader=jinja2.ChoiceLoader(loaders), autoescape=True)


# ── 정상: base.html 단독 렌더 → :root 토큰 정의 전부 존재 ──
def test_base_defines_every_root_token():
    out = _env().get_template("base.html").render()
    for tok in REQUIRED_TOKEN_DEFS:
        assert tok in out, f":root 토큰 선언 누락: {tok}"
    # 값도 표본 검증(잘못된 값 주입 방지)
    assert "--canvas:#fffaf0" in out
    assert "--accent-teal:#1a3a3a" in out


# ── 정상: 컴포넌트 클래스 계약 전부(base + modifier) 존재 ──
def test_base_defines_every_component_selector():
    out = _env().get_template("base.html").render()
    for sel in REQUIRED_SELECTORS:
        assert sel in out, f"컴포넌트 셀렉터 계약 누락: {sel}"
    # 크림 푸터(다크 아님) — 배경이 surface-soft 토큰
    assert "background:var(--surface-soft)" in out
    # 칩 active 상태는 primary 채움
    assert "button.chip.active{background:var(--primary)" in out
    # 기본 topbar: 브랜드 워드마크
    assert 'class="brand"' in out
    assert "청약 알리미" in out


# ── 정상: 아이콘 스프라이트 18개 id 전부 존재 + 각 symbol이 라인 스타일 계약 준수 ──
@pytest.mark.parametrize("icon_id", ICON_IDS)
def test_base_defines_every_icon_symbol(icon_id):
    out = _env().get_template("base.html").render()
    assert f'id="{icon_id}"' in out, f"SVG symbol 누락: {icon_id}"
    # 해당 symbol 여는 태그를 추출해 계약(viewBox 0 0 24 24, 라인 스타일) 검증
    m = re.search(rf'<symbol[^>]*id="{icon_id}"[^>]*>', out)
    assert m, f"symbol 여는 태그 파싱 실패: {icon_id}"
    tag = m.group(0)
    assert 'viewBox="0 0 24 24"' in tag, f"{icon_id}: viewBox 계약 위반"
    assert 'fill="none"' in tag, f"{icon_id}: fill=none 아님(라인 스타일 위반)"
    assert 'stroke="currentColor"' in tag, f"{icon_id}: stroke=currentColor 아님"
    assert 'stroke-width="1.8"' in tag, f"{icon_id}: stroke-width 1.8 아님"


# ── 정상: symbol 정확히 18개(누락·초과 방지) ──
def test_base_has_exactly_18_symbols():
    out = _env().get_template("base.html").render()
    symbols = re.findall(r'<symbol\b', out)
    assert len(symbols) == len(ICON_IDS) == 18, f"symbol 개수 불일치: {len(symbols)}"


# ── 정상: Jinja 블록 6개 이름 계약(정확히 이 이름) 존재 ──
@pytest.mark.parametrize("block_name", REQUIRED_BLOCKS)
def test_base_declares_every_block(block_name):
    raw = BASE.read_text(encoding="utf-8")
    assert f"block {block_name}" in raw, f"Jinja 블록 선언 누락: {block_name}"


# ── 경계값: 빈/누락 컨텍스트에서도 UndefinedError 없이 렌더 ──
def test_base_renders_with_empty_context():
    # 기본 블록에 미정의 컨텍스트 변수가 없어야 함 → 예외 없이 렌더.
    out = _env().get_template("base.html").render({})
    assert "청약 알리미" in out
    assert '<title>' in out
    assert 'id="i-pin"' in out


# ── block override: 자식이 title/topbar/content/footer 를 교체 ──
def test_child_can_override_blocks():
    child = (
        '{% extends "base.html" %}'
        "{% block title %}TEST타이틀{% endblock %}"
        '{% block head %}<meta name="test-head" content="hb">{% endblock %}'
        "{% block topbar %}{% endblock %}"
        '{% block content %}<p class="marker">본문내용</p>{% endblock %}'
        '{% block footer %}<footer class="footer">푸터내용</footer>{% endblock %}'
        '{% block scripts %}<script>window.__t=1</script>{% endblock %}'
    )
    out = _env(child).get_template("_child.html").render()
    # title override
    assert "TEST타이틀" in out
    # head override → 주입된 메타가 head(</head> 이전)에 존재
    assert 'name="test-head"' in out
    assert out.index('name="test-head"') < out.index("</head>")
    # content 주입 (main.wrap 내부)
    assert 'class="marker"' in out and "본문내용" in out
    assert 'class="wrap"' in out
    # topbar 빈 override → 기본 브랜드 부재
    assert 'class="brand"' not in out
    # footer 주입
    assert "푸터내용" in out
    # scripts 주입 (</body> 이전)
    assert "window.__t=1" in out
    # base 스프라이트는 그대로 유지 (override가 base 제공분을 지우지 않음)
    assert 'id="i-pin"' in out


# ── error-case (DoD): base.html 소스에 이모지 0개 ──
def test_base_has_no_emoji():
    raw = BASE.read_text(encoding="utf-8")
    # 이모지 블록(1F000–1FAFF) + 기타기호/딩뱃(2600–27BF). CJK/화살표(2190–21FF)는 제외.
    emoji = re.findall(r"[\U0001F000-\U0001FAFF☀-➿]", raw)
    assert emoji == [], f"base.html 내 이모지 발견: {emoji}"
