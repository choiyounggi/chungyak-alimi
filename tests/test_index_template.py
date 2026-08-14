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

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "web" / "templates"
INDEX = TEMPLATES / "index.html"

# 브리프 L50 — 정보행 아이콘 매핑(주소/접수/가격/면적/세대). 칩엔 아이콘 없음.
INFO_ROW_ICONS = ["i-pin", "i-calendar", "i-won", "i-ruler", "i-building"]

# D7의 닫힌 5종 공급기관. src.db.AGENCIES와 같은 값이어야 하지만, 이 파일은 DB 없이
# 도는 순수 Jinja2 스모크라 상수를 재수입하지 않고 독립 어서션으로 둔다.
AGENCIES = ("LH", "SH", "GH", "HUG", "기타")


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
        agency="LH",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _item(**over):
    base = dict(
        notice=_notice(),
        my_rank="1순위",
        specials=["신혼부부", "생애최초"],
        adres="경기도 화성시 동탄면 1-2",
        lat=None,
        lng=None,
        price_lo=45000,
        price_hi=52000,
        area_lo=59.9,
        area_hi=84.9,
        deadline=None,
        dday=5,
        bookmarked=False,
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


def _render(items, cfg=None, today="2026-07-22", kakao_key="", agencies=AGENCIES) -> str:
    return _env().get_template("index.html").render(
        items=items,
        cfg=cfg or _cfg(),
        today=today,
        kakao_key=kakao_key,
        agencies=agencies,
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
    # 핵심 수치(figure) 위계 + 임계값(5 → warn)·문구 보존
    assert 'class="figure figure--warn"' in out
    assert "마감 D-5" in out
    # 유형 dot+라벨(card__top) — 뮤트 아님(dday>=0)
    assert 'class="card__top"' in out
    assert '<span class="type-tag">' in out
    # t2-marker 소비 계약: data-dday/data-housing
    assert 'data-dday="5"' in out
    assert 'data-housing="APT"' in out
    # 지역=.tag, 순위=.rank(base 계약)
    assert 'class="tag"' in out
    assert "rank-1" in out
    # 정보행 SVG 아이콘 5종만 사용
    for icon in INFO_ROW_ICONS:
        assert f'href="#{icon}"' in out, f"정보행 아이콘 누락: {icon}"
    # 크림 푸터 문구
    assert "공공 오픈API(청약홈·LH·마이홈·HUG) + 공식 포털(SH·GH) 기반" in out
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
    # 푸터는 유지
    assert "공공 오픈API(청약홈·LH·마이홈·HUG) + 공식 포털(SH·GH) 기반" in out


# ── 경계값: dday 임계 경계(예정/임박/원거리)·price_lo 없음 ──
def test_index_dday_thresholds_and_missing_price():
    # dday<0 → 예정(figure--pre), 기존 D+표기 의미 유지(D1)
    pre = _render([_item(dday=-3, price_lo=None, price_hi=None)])
    assert 'class="figure figure--pre"' in pre
    assert "D+3 예정" in pre
    assert 'data-dday="-3"' in pre
    # dday<0 → 유형 dot 뮤트(D3)
    assert '<span class="type-tag type-tag--muted">' in pre
    # 가격 없음 → 가격 세그먼트(#i-won) 미노출, 렌더 정상
    assert 'href="#i-won"' not in pre
    # 경계: dday=0 → 마감 임박 하한(closing, --danger)
    d0 = _render([_item(dday=0)])
    assert 'class="figure figure--closing"' in d0
    assert "마감 D-0" in d0
    # 경계: dday=3 → closing 상한
    d3 = _render([_item(dday=3)])
    assert 'class="figure figure--closing"' in d3
    assert "마감 D-3" in d3
    # 경계: dday=4 → warn 하한
    d4 = _render([_item(dday=4)])
    assert 'class="figure figure--warn"' in d4
    assert "마감 D-4" in d4
    # 경계: dday=7 → warn 상한
    d7 = _render([_item(dday=7)])
    assert 'class="figure figure--warn"' in d7
    assert "마감 D-7" in d7
    # 경계: dday=8 → 기본(원거리, 수식자 없음)
    d8 = _render([_item(dday=8)])
    assert 'class="figure"' in d8
    assert 'class="figure figure--' not in d8
    assert "마감 D-8" in d8
    # 정상: dday=12 → 기본, data-dday 보존
    far = _render([_item(dday=12)])
    assert "마감 D-12" in far
    assert 'data-dday="12"' in far


# ── 에러/빈값: dday None → figure 미출력 + data-dday 빈값(D10) ──
def test_index_dday_none_omits_figure():
    out = _render([_item(dday=None)])
    assert '<p class="figure' not in out
    assert 'data-dday=""' in out
    # dday None도 뮤트(D3)
    assert '<span class="type-tag type-tag--muted">' in out


# ── 에러/빈값: housing_type·house_secd_nm 모두 없음 → data-housing 빈값('기타' 넣지 않음, D4) ──
def test_index_data_housing_empty_when_both_missing():
    out = _render([_item(notice=_notice(house_secd_nm=None))])
    assert 'data-housing=""' in out
    # 라벨 표기는 '기타'로 폴백하되 data-housing 속성값은 비운다
    assert "기타" in out


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


# ── 카드 북마크 버튼: 상태(on/off) 반영 ──
def test_card_bookmark_button():
    off = _render([_item(bookmarked=False)])
    assert 'class="bookmark-btn"' in off              # 미북마크 → is-on 없음
    assert 'data-pblanc="2026000123"' in off
    assert 'aria-pressed="false"' in off
    # 버튼 아이콘은 CSS로 채울 수 있게 인라인 SVG(스프라이트 use 아님)
    assert re.search(
        r'class="bookmark-btn"[^>]*>\s*<svg class="ic" viewBox="0 0 24 24"', off
    ), "북마크 버튼이 인라인 SVG(viewBox 포함)를 써야 함"
    assert "M6 3h12" in off                           # 북마크 path
    on = _render([_item(bookmarked=True)])
    assert "bookmark-btn is-on" in on                 # 북마크됨 → is-on
    assert 'aria-pressed="true"' in on


# ── 지도 레이아웃: kakao_key 있을 때 2단 grid + 지도 컨테이너 + 카드 좌표 data-* ──
def test_map_layout_with_kakao_key():
    out = _render([_item(lat=37.61, lng=126.71)], kakao_key="TESTKEY")
    # 2단 레이아웃 + 지도 컨테이너
    assert "map-layout" in out
    assert 'id="chungyak-map"' in out
    # 카드 좌표/식별 data-* (Task 03 JS가 읽음)
    assert 'data-pblanc="2026000123"' in out
    assert 'data-lat="37.61"' in out
    assert 'data-lng="126.71"' in out
    assert 'data-title="테스트힐스테이트"' in out
    assert 'data-adres="경기도 화성시 동탄면 1-2"' in out
    # 리스트 패널 안에 카드가 존재(리스트는 유지)
    assert 'class="list-panel' in out
    assert 'class="card"' in out
    # 내부 제목 앵커는 그대로(중첩 방지: 카드는 div, 링크는 title 앵커)
    assert 'href="/notice/2026000123"' in out


# ── 경계값: kakao_key 없으면 지도 미노출 + 리스트 전체폭, 카드는 유지 ──
def test_no_map_without_kakao_key():
    out = _render([_item()], kakao_key="")
    assert 'id="chungyak-map"' not in out
    assert "list-panel--full" in out  # 리스트 전체폭
    assert 'class="card"' in out  # 리스트는 여전히 렌더
    # 칩 필터도 그대로
    assert 'data-ftype="area"' in out
    # 지도 없을 땐 빈 상태 문구가 칩 필터 맥락(지도 범위 문구 아님)
    assert "선택한 필터에 맞는 공고가 없어요" in out
    assert "이 지도 범위에" not in out


# ── 경계값: 좌표 None → data-lat/lng 빈 값으로 렌더, 예외 없음 ──
def test_card_data_empty_coords():
    out = _render([_item(lat=None, lng=None)], kakao_key="TESTKEY")
    assert 'data-lat=""' in out
    assert 'data-lng=""' in out
    # 좌표 없어도 카드/식별자·주소는 유지
    assert 'data-pblanc="2026000123"' in out
    assert 'data-adres="경기도 화성시 동탄면 1-2"' in out


# ── 지도 JS: kakao_key 있을 때 SDK 로드 + 지오코딩 사다리 재사용 + 상호작용/필터연동 ──
def test_map_js_present_with_kakao_key():
    out = _render([_item(lat=37.61, lng=126.71)], kakao_key="TESTKEY")
    # 카카오 SDK 로드(services 라이브러리) + 키 주입 + 실패 안내(detail 패턴 재사용)
    assert "dapi.kakao.com" in out
    assert "libraries=services" in out
    assert "TESTKEY" in out
    assert 'onerror="mapDashFailed()"' in out
    assert "kakao.maps.load" in out
    assert "12000" in out  # 로드 실패 타임아웃 보존
    # 좌표 없는 공고용 지오코딩 사다리 재사용
    assert "addressSearch" in out
    # 리스트↔지도 연동 + 필터-마커 동기화
    assert "card--active" in out
    assert "panTo" in out                # 카드 hover/focus → 마커로 지도 이동
    assert "scrollIntoView" in out       # 마커 클릭 → 해당 카드로 스크롤
    assert "setMap" in out               # 칩 필터 → 마커 표시/숨김 토글
    # XSS 안전: 오버레이 DOM은 문자열 이어붙이기 대신 createElement/textContent
    assert "createElement" in out
    assert "textContent" in out


# ── 지도 마커: CustomOverlay 2단 pill(.mk)로 교체, 옛 Marker/InfoWindow 제거 ──
def test_marker_customoverlay_replaces_marker_infowindow():
    out = _render([_item(lat=37.61, lng=126.71)], kakao_key="TESTKEY")
    assert "CustomOverlay" in out
    assert "createMarkerEl" in out
    assert "xAnchor" in out and "yAnchor" in out
    # 옛 API 흔적 제거(InfoWindow는 마커 자체가 라벨을 대신함)
    assert "new kakao.maps.Marker(" not in out
    assert "InfoWindow" not in out


# ── 마커 CSS(.mk): 스펙 §1 코드 블록과 일치(토큰만 사용) ──
def test_marker_css_matches_spec():
    out = _render([_item()], kakao_key="TESTKEY")
    assert ".mk{" in out
    assert "border-radius:6px 6px 6px 0" in out
    assert "box-shadow:1px 2px 4px rgba(0,0,0,.16)" in out
    assert "cursor:pointer" in out
    assert ".mk__type{" in out
    assert ".mk__figure{" in out
    assert "border-radius:0 0 5px 0" in out
    assert ".mk--selected{" in out and "scale(1.08)" in out
    assert ".mk--closing .mk__figure{color:var(--danger)}" in out
    assert ".mk--closed{--band:var(--muted-soft)}" in out


# ── 마커 dday 분기(정상/경계/에러): 스펙 4분기가 JS 소스에 그대로 존재 ──
def test_marker_dday_branch_logic_present():
    out = _render([_item()], kakao_key="TESTKEY")
    # 에러(파싱 불가 → 미정)
    assert '"일정 미정"' in out
    # 경계(음수 → 예정, 접수 마감 취급)
    assert '"D+" + (-d) + " 예정"' in out
    # 경계(0~3 → 임박)
    assert "d <= 3" in out
    assert "mk--closing" in out
    # 정상(그 외 → 마감 D-N, 임박 클래스 없음)
    assert '"마감 D-" + d' in out
    assert "mk--closed" in out


# ── 마커 강조: 카드 hover/focus·클릭 시 .mk--selected 토글(emphasize 연동) ──
# emphasize() 함수 본문만 떼어내 이전 마커에서 remove + 새 마커에 add가 모두
# 있는지 확인한다(둘 중 하나만 있으면 선택 상태가 누적되거나 아예 안 붙는다).
def test_marker_selected_class_toggle_wired():
    out = _render([_item(lat=37.61, lng=126.71)], kakao_key="TESTKEY")
    m = re.search(r"( *)function emphasize\(entry\) \{(.*?)\n\1\}\n", out, re.S)
    assert m, "emphasize(entry) 함수를 찾을 수 없음"
    body = m.group(2)
    assert 'activeMarker.el.classList.remove("mk--selected")' in body
    assert 'entry.el.classList.add("mk--selected")' in body


# ── 경계: data-housing 빈값이면 상단 밴드(mk__type) 노드 자체를 생략 ──
# createMarkerEl() 함수 본문만 떼어내 mk__type 생성이 housing 존재를 확인하는
# if 블록 안에서만 일어나는지 확인한다(조건 없이 항상 append하면 이 매치가 깨진다).
def test_marker_type_band_omitted_when_housing_empty():
    out = _render([_item()], kakao_key="TESTKEY")
    m = re.search(r"( *)function createMarkerEl\(card\) \{(.*?)\n\1\}\n", out, re.S)
    assert m, "createMarkerEl(card) 함수를 찾을 수 없음"
    body = m.group(2)
    gate = re.search(
        r"if \(housing\) \{[^}]*mk__type[^}]*wrap\.appendChild\(type\);\s*\}",
        body,
    )
    assert gate, "mk__type 노드 생성이 housing 존재 조건 블록 밖에 있음"


# ── 지도 뷰포트가 곧 목록 필터: 초기 강남역 + 범위∩칩 필터 ──
def test_map_viewport_drives_list():
    out = _render([_item(lat=37.5, lng=127.03)], kakao_key="TESTKEY")
    # 초기 디폴트 영역 = 강남역 (옛 서울시청 중심 제거)
    assert "37.4979" in out and "127.0276" in out
    assert "37.5665" not in out
    # 뷰포트 기반 목록 필터: 현재 범위(getBounds) 안에 있는지(contain)로 판정
    assert "getBounds" in out
    assert ".contain(" in out
    # 갱신은 사용자 조작(드래그/줌)에서만 → hover panTo로는 목록 안 흔들림
    assert "dragend" in out
    assert "zoom_changed" in out
    # 칩 ∩ 뷰포트: 칩 예측자를 공유해 결합
    assert "chungyakChipMatch" in out
    assert "chungyakApplyList" in out
    # 옛 filterchange 메커니즘은 제거됨
    assert "filterchange" not in out
    # 지도 있을 때 빈 상태 문구는 "지도 범위" 맥락
    assert "이 지도 범위에 조건에 맞는 공고가 없어요" in out
    assert "선택한 필터에 맞는 공고가 없어요" not in out


# ── 경계값: kakao_key 없으면 지도 JS 미주입, 칩 필터 JS는 보존 ──
def test_map_js_absent_without_kakao_key():
    out = _render([_item()], kakao_key="")
    assert "dapi.kakao.com" not in out
    assert "mapDashFailed" not in out
    assert "CustomOverlay" not in out
    assert "createMarkerEl" not in out
    # 기존 칩 필터 JS는 그대로
    assert "button.chip" in out
    assert 'getElementById("js-empty")' in out


# ── 기관 필터(D7/D21): 칩 5종이 기존 다중선택 칩 구조로 렌더 ──
def test_agency_chips_rendered():
    out = _render([_item()])
    assert out.count('data-ftype="agency"') == len(AGENCIES) == 5
    for a in AGENCIES:
        assert f'data-ftype="agency" data-fval="{a}"' in out, f"기관 칩 누락: {a}"
    # 기존 칩과 같은 토글 계약(button.chip + aria-pressed)
    assert re.search(
        r'<button type="button" class="chip" data-ftype="agency"[^>]*aria-pressed="false"',
        out,
    ), "기관 칩이 기존 다중선택 칩 구조를 따라야 함"


# ── 기관 필터: 카드에 data-agency가 실려 JS가 재조회 없이 판정 가능 ──
def test_card_has_agency_dataset():
    out = _render([_item(notice=_notice(agency="SH"))])
    assert 'data-agency="SH"' in out


# ── 기관 배지: 카드에 기관명이 배지로 노출(자동이스케이프, |safe 금지) ──
def test_agency_badge_rendered():
    out = _render([_item(notice=_notice(agency="HUG"))])
    assert 'class="badge badge--agency">HUG<' in out
    assert ".badge--agency{" in out  # 페이지 로컬 스타일 존재


# ── 경계값: agency 없음(None) → '기타'로 폴백, 예외 없음 ──
def test_agency_none_falls_back_to_etc():
    out = _render([_item(notice=_notice(agency=None))])
    assert 'data-agency="기타"' in out
    assert 'class="badge badge--agency">기타<' in out


# ── 기관 필터 JS: 기존 matchesType에 agency 분기만 추가(재조회 없음 → 레이스 없음) ──
def test_matches_type_handles_agency():
    out = _render([_item()])
    assert 'type === "agency"' in out
    assert "card.dataset.agency" in out
    # 기존 배선은 그대로(칩∩지도범위 결합 지점 유지)
    assert "chungyakChipMatch" in out or "window.chungyakApplyList" in out


# ── 경계: 지역/유형 미설정 시 '전국/전체 유형'이 고정 조건으로 표기 ──
def test_fixed_fallbacks_when_no_region_type():
    out = _render([_item()], _cfg(regions=[], house_types=[]))
    assert "전국" in out and "전체 유형" in out
    # 폴백은 고정(chip--info)으로만, 클릭 가능한 area/secd 버튼은 없어야 함
    assert 'data-ftype="area"' not in out
    assert 'data-ftype="secd"' not in out
