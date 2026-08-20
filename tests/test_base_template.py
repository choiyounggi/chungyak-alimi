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

# 브리프 [SVG 아이콘 스프라이트] — 다운스트림이 참조하는 id 전부.
# i-eye / i-eye-off 는 비밀번호 마스킹 토글용(Task 04).
ICON_IDS = [
    "i-pin", "i-calendar", "i-won", "i-home", "i-award", "i-doc", "i-clip",
    "i-image", "i-map", "i-search", "i-target", "i-arrow-left", "i-arrow-right",
    "i-gift", "i-building", "i-ruler", "i-clock", "i-alert", "i-bookmark",
    "i-eye", "i-eye-off",
]

# 브리프 [색상/라운드/간격/폰트] — :root 토큰 정의(선언부, `--name:` 형태로 존재 검증).
# 관보 에디토리얼(design.md Exports) 신규 토큰 + 구 토큰명(하위호환 별칭) 모두 유지.
REQUIRED_TOKEN_DEFS = [
    # 신규(design.md Exports)
    "--color-paper:", "--color-ink:", "--color-accent:", "--color-signal:",
    "--font-display:", "--font-body:", "--font-mono:", "--dur-fast:", "--ease-out:",
    # 구 토큰명 — 페이지 태스크가 이관할 때까지 별칭으로 유지(하위호환)
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
    ".topbar", ".topbar .inner", ".topbar a", ".brand", ".wrap", ".toast", ".toast.is-on",
    ".btn", ".btn-primary", ".btn-secondary",
    ".card", ".section",
    ".badge", ".badge--soon", ".badge--mid", ".badge--far", ".badge--pre",
    ".tag",
    ".rank", ".rank-1", ".rank-2",
    ".chip", "button.chip", "button.chip:hover", "button.chip.active",
    ".table",
    "label", "input", "input:focus", "input.invalid",
    ".field", ".field-error", ".field.has-error .field-error", ".form-error",
    ".pw-wrap", ".pw-toggle",
    ".footer", ".empty", ".ic", ".muted", ".sub", ".display",
    ".topnav", ".bookmark-btn", ".bookmark-btn.is-on",
    ".head-row", ".head-actions", "a.title", ".row",
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
    # 값도 표본 검증(잘못된 값 주입 방지) — 관보 에디토리얼 토큰(design.md Exports) + 구 별칭
    assert "--color-paper:oklch(97.5% 0.012 85)" in out
    assert "--canvas:var(--color-paper)" in out
    assert "--color-accent:oklch(40% 0.065 195)" in out
    assert "--accent-teal:var(--color-accent)" in out


# ── 정상: 구글 폰트 로딩 계약 — preconnect 2개 + 3개 폰트 패밀리 + display=swap ──
def test_base_loads_fonts_with_swap():
    out = _env().get_template("base.html").render()
    assert 'rel="preconnect" href="https://fonts.googleapis.com"' in out
    assert 'rel="preconnect" href="https://fonts.gstatic.com" crossorigin' in out
    assert "family=Hahmlet" in out
    assert "family=IBM+Plex+Sans+KR" in out
    assert "family=IBM+Plex+Mono" in out
    # 에러 케이스: display=swap 이 빠지면 폰트 로딩 중 렌더가 막힌다(FOIT) — 반드시 포함되어야 함
    assert "display=swap" in out


# ── 정상: 모션 축소를 요청한 사용자에게는 전환·애니메이션을 사실상 끈다 ──
def test_base_respects_reduced_motion():
    out = _env().get_template("base.html").render()
    assert "@media (prefers-reduced-motion:reduce)" in out
    assert "transition-duration:.01ms!important" in out
    assert "animation:none!important" in out


# ── 정상: h1은 --text-xl(26px)로 .display(33px)와 위계가 구분된다(리뷰 r1 F1) ──
def test_base_h1_uses_text_xl_not_2xl():
    out = _env().get_template("base.html").render()
    assert "h1{font-family:var(--font-display);font-size:var(--text-xl)" in out
    # 모바일은 24px(D3) — .display(모바일 28px)와도 위계가 구분되어야 한다
    assert "h1{font-size:24px}" in out


# ── 정상: --font-mono의 한글 폴백이 Plex Sans KR로 이어져 임의 시스템 폰트로 새지 않는다(리뷰 r1 F2) ──
def test_base_font_mono_falls_back_to_korean_body_font():
    out = _env().get_template("base.html").render()
    assert '--font-mono:"IBM Plex Mono","IBM Plex Sans KR",ui-monospace,monospace' in out


# ── 경계: 섹션 제목 double rule 아래 여백이 남아 콘텐츠가 붙지 않는다(리뷰 r1 NB1) ──
def test_base_section_h2_keeps_spacing_below_double_rule():
    out = _env().get_template("base.html").render()
    assert (
        ".section h2{border-bottom:3px double var(--color-rule-strong);"
        "padding-bottom:10px;margin-bottom:14px}"
    ) in out


# ── 정상: 컴포넌트 클래스 계약 전부(base + modifier) 존재 ──
def test_base_defines_every_component_selector():
    out = _env().get_template("base.html").render()
    for sel in REQUIRED_SELECTORS:
        assert sel in out, f"컴포넌트 셀렉터 계약 누락: {sel}"
    # 크림 푸터(다크 아님) — 배경이 paper-2 토큰(관보 에디토리얼)
    assert "background:var(--color-paper-2)" in out
    # 선택된 필터 칩은 accent 채움(클릭 가능 필터를 CTA 버튼과 구분)
    assert "button.chip.active{background:var(--color-accent)" in out
    # 고정(읽기 전용) 조건 칩 변형 — 클릭 불가 시각 구분
    assert ".chip--info{" in out
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


# ── 정상: symbol 정확히 21개(누락·초과 방지) ──
def test_base_has_exactly_21_symbols():
    out = _env().get_template("base.html").render()
    symbols = re.findall(r'<symbol\b', out)
    assert len(symbols) == len(ICON_IDS) == 21, f"symbol 개수 불일치: {len(symbols)}"


# ── 정상: 상단 내비(전체/북마크) + 북마크 토글 JS 계약 ──
def test_base_topnav_and_bookmark_toggle_js():
    out = _env().get_template("base.html").render()
    # 상단 내비: 전체 / 북마크(아이콘 포함)
    assert 'class="topnav"' in out
    assert 'href="/bookmarks"' in out
    assert "전체" in out and "북마크" in out
    assert 'href="#i-bookmark"' in out
    # 북마크 토글 JS: .bookmark-btn 클릭 → /bookmark/ PUT|DELETE + aria-pressed 토글
    assert "bookmark-btn" in out
    assert "/bookmark/" in out
    assert '"PUT"' in out and '"DELETE"' in out
    assert "aria-pressed" in out
    # 북마크 페이지에서 해제 시 카드 제거
    assert "/bookmarks" in out and "removeChild" in out
    # 북마크 버튼: 카드 우상단 고정(absolute) + 체크 시 아이콘 색 채움(fill)
    assert ".bookmark-btn{position:absolute" in out
    assert ".bookmark-btn.is-on .ic{fill:var(--color-accent)}" in out
    # 카드는 우상단 절대배치의 기준(position:relative)
    assert "position:relative" in out


# ── 실패 가시화: 북마크 요청이 실패하면 사용자에게 보여야 한다 ──
def test_bookmark_failure_shows_toast():
    """조용한 실패 금지 — 서버 500 을 프런트가 삼켜 '안 눌린다'로만 보였던 회귀를 막는다."""
    out = _env().get_template("base.html").render()
    # 토스트 자리와 표시 함수가 존재하고, catch 가 그 함수를 부른다
    assert '<div id="toast" class="toast" role="status"></div>' in out
    assert "function showToast(" in out
    assert "북마크를 변경하지 못했어요" in out
    assert re.search(r"\.catch\(function \(\) \{ showToast\(", out), "catch 가 토스트를 띄우지 않는다"
    # 응답이 ok 가 아니면 성공 경로로 새지 않고 예외로 빠진다
    assert "if (!r.ok) throw new Error(r.status);" in out


# ── 에러 경로: 세션 만료(401)는 재시도가 아니라 로그인으로 ──
def test_bookmark_401_redirects_to_login():
    out = _env().get_template("base.html").render()
    assert 'if (r.status === 401) { location.href = "/login"; return null; }' in out
    # 401 로 이동하는 동안 성공 핸들러가 null 을 건드리지 않는다
    assert "if (!d) return;" in out


# ── 경계값: 토스트는 비어 있을 때 화면을 가리지 않는다 ──
def test_toast_is_inert_when_empty():
    out = _env().get_template("base.html").render()
    m = re.search(r"\.toast\{[^}]*\}", out)
    assert m, ".toast 규칙 파싱 실패"
    rule = m.group(0)
    assert "opacity:0" in rule
    assert "pointer-events:none" in rule
    # 드러나는 것은 .is-on 일 때뿐
    assert ".toast.is-on{opacity:1" in out


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
