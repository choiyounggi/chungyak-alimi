"""index.html 렌더 스모크 — DB 불필요, 순수 Jinja2.

test_base_template.py와 같은 방식(FileSystemLoader + autoescape)으로 index.html이
base.html을 상속(extends)하고 브리프의 Clay 계약을 지키는지 검증한다. app.py의 index
라우트 컨텍스트({items, cfg, today})를 mock으로 재현한다.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "web" / "templates"
INDEX = TEMPLATES / "index.html"

# 브리프 L50 — 정보행 아이콘 매핑(주소/접수/가격/면적/세대). 칩엔 아이콘 없음.
INFO_ROW_ICONS = ["i-pin", "i-calendar", "i-won", "i-ruler", "i-building"]


def _env() -> jinja2.Environment:
    # base.html을 함께 로드해야 extends가 해석됨.
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True
    )


def _notice(**over):
    base = dict(
        house_nm="테스트힐스테이트",
        pblanc_no="2026000123",
        area_nm="경기",
        house_secd_nm="APT",
        house_dtl_secd_nm="민영",
        rcept_bgnde="2026-08-01",
        rcept_endde="2026-08-05",
        tot_suply_hshldco=320,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _item(**over):
    base = dict(
        notice=_notice(),
        my_rank="1순위",
        specials=["신혼부부", "생애최초"],
        adres="경기도 화성시 동탄면 1-2",
        price_lo=45000,
        price_hi=52000,
        area_lo=59.9,
        area_hi=84.9,
        deadline=None,
        dday=5,
    )
    base.update(over)
    return base


def _cfg(**over):
    base = dict(
        regions=["서울", "경기"],
        house_types=["APT"],
        special_supply=["신혼부부", "생애최초"],
        price_max_manwon=60000,
        only_open=True,
    )
    base.update(over)
    return base


def _render(items, cfg=None, today="2026-07-22") -> str:
    return _env().get_template("index.html").render(
        items=items, cfg=cfg or _cfg(), today=today
    )


# ── 정상: 목록 1건 렌더 → extends base 확인 + 카드/칩/아이콘/푸터/JS 계약 ──
def test_index_renders_normal_list():
    out = _render([_item()])
    # extends base → base 스프라이트 상속(#i-pin symbol 존재)
    assert 'id="i-pin"' in out
    # 헤더
    assert "내 관심 청약" in out
    assert 'class="count"' in out
    # 카드 제목 + 링크 경로 보존
    assert "테스트힐스테이트" in out
    assert 'href="/notice/2026000123"' in out
    # 칩 필터 data 속성·값 규칙 보존
    assert 'data-ftype="area"' in out
    assert 'data-fval="경기"' in out
    assert 'data-ftype="secd"' in out
    assert 'data-ftype="special"' in out
    assert 'data-ftype="rank"' in out and 'data-fval="1순위"' in out
    # 카드 data 속성 보존
    assert 'data-area="경기"' in out
    assert 'data-secd="APT"' in out
    assert 'data-specials="신혼부부|생애최초"' in out
    assert 'data-rank="1순위"' in out
    # D-day 뱃지 클래스(base 계약) + 임계값(5 → mid)·문구 보존
    assert 'class="badge badge--mid"' in out
    assert "D-5" in out
    # 지역=.tag, 순위=.rank(base 계약)
    assert 'class="tag"' in out
    assert "rank-1" in out
    # 정보행 SVG 아이콘 5종만 사용
    for icon in INFO_ROW_ICONS:
        assert f'href="#{icon}"' in out, f"정보행 아이콘 누락: {icon}"
    # 크림 푸터 문구
    assert "공공 오픈API(청약홈/LH) 기반" in out
    # 칩 필터 JS 보존(셀렉터·요소)
    assert "button.chip" in out
    assert 'getElementById("js-empty")' in out
    assert 'querySelector(".count")' in out


# ── 경계값: 빈 목록 → 서버 빈 상태 + 카드 0개, 예외 없음 ──
def test_index_renders_empty_list():
    out = _render([])
    assert "조건에 맞는 진행·예정 공고가 없어요." in out
    assert 'class="card"' not in out
    assert 'id="js-empty"' in out  # JS 빈 상태 컨테이너는 항상 존재
    assert "공공 오픈API(청약홈/LH) 기반" in out  # 푸터는 유지


# ── 경계값: dday 임계 경계(예정/임박/원거리)·price_lo 없음 ──
def test_index_dday_thresholds_and_missing_price():
    # dday<0 → 예정(badge--pre)
    pre = _render([_item(dday=-3, price_lo=None, price_hi=None)])
    assert 'class="badge badge--pre"' in pre
    assert "D+3 예정" in pre
    # 가격 없음 → 가격 세그먼트(#i-won) 미노출, 렌더 정상
    assert 'href="#i-won"' not in pre
    # dday<=3 → 임박(badge--soon)
    soon = _render([_item(dday=2)])
    assert 'class="badge badge--soon"' in soon
    assert "D-2 임박" in soon
    # dday>7 → 원거리(badge--far)
    far = _render([_item(dday=30)])
    assert 'class="badge badge--far"' in far


# ── error-assert(DoD): 파일 내 이모지 0개 + 첫 줄 extends + 블록 계약 ──
def test_index_source_no_emoji_and_extends_base():
    raw = INDEX.read_text(encoding="utf-8")
    # 이모지 블록(1F000–1FAFF) + 기타기호/딩뱃(2600–27BF). CJK/화살표(2190–21FF)는 제외.
    emoji = re.findall(r"[\U0001F000-\U0001FAFF☀-➿]", raw)
    assert emoji == [], f"index.html 내 이모지 발견: {emoji}"
    # 첫 비어있지 않은 줄이 extends
    first = next(ln for ln in raw.splitlines() if ln.strip())
    assert first.strip() == '{% extends "base.html" %}', f"첫 줄 extends 아님: {first!r}"
    # DoD 블록 계약
    for blk in ("title", "content", "footer", "scripts"):
        assert f"block {blk}" in raw, f"블록 누락: {blk}"
    # base 재정의 금지: 자체 <!DOCTYPE>/<head>/:root 없어야 함
    assert "<!DOCTYPE" not in raw
    assert ":root{" not in raw


# ── 필터: 클릭 가능 vs 고정 구분 + 다중 선택(정상 케이스) ──
def test_filter_groups_distinguish_clickable_and_fixed():
    out = _render([_item()], _cfg())
    # 두 그룹 라벨로 시각적 구분
    assert "필터 · 다중 선택" in out
    assert "고정 조건" in out
    # 클릭 가능 필터: button.chip + aria-pressed(다중 선택 토글 상태)
    assert 'data-ftype="area"' in out and 'aria-pressed="false"' in out
    # 고정 조건: 읽기 전용 chip--info (가격/상태). button 아님
    assert "chip chip--info" in out
    assert "60,000만원 이하" in out and "진행·예정만" in out


# ── 필터: 다중 선택 JS 계약(타입 내 OR / 타입 간 AND, 재클릭 해제) ──
def test_filter_multiselect_js_present():
    raw = INDEX.read_text(encoding="utf-8")
    # 다중 선택 자료구조: 타입별 선택 값 집합
    assert "active[type]" in raw
    # 종류 간 AND 주석/로직 흔적
    assert "AND" in raw and "OR" in raw
    # 재클릭 시 해제(splice) + aria-pressed 토글
    assert "splice" in raw
    assert 'setAttribute("aria-pressed"' in raw
    # 과거 단일 선택(active = null) 잔재 없음
    assert "var active = null" not in raw


# ── 경계: 지역/유형 미설정 시 '전국/전체 유형'이 고정 조건으로 표기 ──
def test_fixed_fallbacks_when_no_region_type():
    out = _render([_item()], _cfg(regions=[], house_types=[]))
    assert "전국" in out and "전체 유형" in out
    # 폴백은 고정(chip--info)으로만, 클릭 가능한 area/secd 버튼은 없어야 함
    assert 'data-ftype="area"' not in out
    assert 'data-ftype="secd"' not in out
