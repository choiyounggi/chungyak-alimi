"""detail.html 렌더 스모크 — DB 불필요, 순수 Jinja2.

test_web.py의 상세 라우트 테스트는 postgres-gated(미가용 시 skip)이므로, base 상속
계약과 이모지 부재·JS 보존은 독립 실행 가능한 이 파일로 검증한다. test_base_template.py와
동일하게 FileSystemLoader로 templates 디렉토리를 로드해 detail.html을 mock 컨텍스트로 렌더한다.
컨텍스트 키는 src/web/app.py::notice_detail_data 반환 dict과 1:1로 맞춘다.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace as ns

import jinja2

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "web" / "templates"
DETAIL = TEMPLATES / "detail.html"

# 픽토그램/딩뱃 이모지 범위. 라이트박스 컨트롤 ‹ › ✕ 는 브리프 DoD가 명시한 보존 대상이므로 허용.
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]")
_ALLOWED = {"‹", "›", "✕"}  # ‹ › ✕


def _env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True
    )


def _render(**over) -> str:
    """notice_detail_data 스키마와 동일한 기본 컨텍스트 + 개별 override."""
    ctx = {
        "notice": ns(
            house_nm="테스트단지",
            area_nm="경기",
            house_secd_nm="APT",
            house_dtl_secd_nm="민영",
            tot_suply_hshldco=300,
            bsns_mby_nm="OO도시공사",
            pblanc_url="https://apply.example/notice/1",
        ),
        "judged": ns(
            supported=True,
            score=ns(total=64, homeless=15, dependents=20, account=17),
            rank=ns(rank="1순위", regulated=True, reasons=["청약통장 24회"]),
            newlywed=ns(tier="1순위", reasons=["혼인 3년 이내"]),
            first_life=ns(tier=None, reasons=["대상 아님"]),
        ),
        "superseded_by": None,
        "rows": [
            {
                "ht": ns(
                    house_ty="059.9500",
                    suply_ar=59.95,
                    lttot_top_amount=50724,
                    suply_hshldco=100,
                ),
                "specials": [("신혼부부", 12), ("생애최초", 8)],
            }
        ],
        "schedule": [("모집공고", "2026-01-05"), ("당첨자발표", "2026-02-01")],
        "lh_schedule": [
            ns(gubun="일반", acp="2026-01-10", anc="2026-02-01", sbm="2026-02-05", ctrt="2026-02-20")
        ],
        "pan_dtl": "본문 공고 내용 텍스트입니다.",
        "regs": ["조정대상지역", "투기과열지구"],
        "lh_images": [],
        "lh_files": [
            ns(url="https://apply.lh.or.kr/lhFile.do?fileid=2", label="공고문(PDF)", name="p.pdf")
        ],
        "adres": "경기도 김포시 걸포동 123",
        "tel": "1600-0000",
        "builder": "OO건설",
        "mvn": "202612",
        "kakao_key": "",
        "polygon": None,
    }
    ctx.update(over)
    return _env().get_template("detail.html").render(ctx)


# ── 정상(full context): base 상속 + 섹션/테이블/버튼/아이콘 + 판정/정정배너 ──
def test_detail_extends_base_and_renders_full():
    out = _render(superseded_by="W99")
    # base 상속 확인: base 스프라이트가 렌더 결과에 존재해야 extends가 작동한 것
    assert 'id="i-pin"' in out
    # 헤드/타이틀
    assert "테스트단지" in out
    # 섹션 존재
    for s in ("내 청약 판정", "일정", "공급구분별 일정", "주택형별 모집", "위치", "공고 내용", "자격요건"):
        assert s in out, f"섹션 누락: {s}"
    # 테이블은 base .table 클래스
    assert '<table class="table"' in out
    # 주택형 데이터 포맷 보존
    assert "059.9500" in out and "59.95" in out and "50,724" in out
    assert "신혼부부" in out and "생애최초" in out
    # 판정 데이터
    assert "64점" in out and "1순위" in out
    # 섹션 헤더 아이콘 (이모지 대체)
    for icon in ('href="#i-target"', 'href="#i-home"', 'href="#i-calendar"', 'href="#i-doc"', 'href="#i-pin"'):
        assert icon in out, f"아이콘 누락: {icon}"
    # 버튼: 원본 공고문 = primary, PDF = secondary
    assert 'class="btn btn-primary"' in out
    assert 'class="btn btn-secondary"' in out
    assert "https://apply.example/notice/1" in out
    assert "lhFile.do?fileid=2" in out
    # 정정공고 배너: i-alert + 최신 공고 링크
    assert 'href="#i-alert"' in out
    assert "/notice/W99" in out
    # topbar override: 뒤로가기 아이콘 + 문구
    assert 'href="#i-arrow-left"' in out
    assert "목록으로" in out


# ── 경계값: 모든 리스트 빈/None — UndefinedError 없이 렌더, 조건부 블록 미노출 ──
def test_detail_boundary_all_empty():
    out = _render(
        notice=ns(
            house_nm="빈단지",
            area_nm=None,
            house_secd_nm=None,
            house_dtl_secd_nm=None,
            tot_suply_hshldco=None,
            bsns_mby_nm=None,
            pblanc_url=None,
        ),
        judged=None,
        superseded_by=None,
        rows=[],
        schedule=[],
        lh_schedule=[],
        pan_dtl=None,
        regs=[],
        lh_images=[],
        lh_files=[],
        adres=None,
        tel=None,
        builder=None,
        mvn=None,
        kakao_key="",
        polygon=None,
    )
    assert "빈단지" in out
    # base 스프라이트는 그대로
    assert 'id="i-pin"' in out
    # kakao_key/adres 없음 → 지도 미노출, 카카오 스크립트 미주입
    assert 'id="map"' not in out
    assert "dapi.kakao.com" not in out
    # 이미지 없음 → 갤러리/라이트박스 미노출
    assert 'id="lightbox"' not in out
    assert "단지 이미지" not in out
    # 판정/정정/일정/규제 섹션 미노출
    assert "내 청약 판정" not in out
    assert "정정공고" not in out


# ── error-case(계약): 장식 이모지 0 (렌더 결과 + 소스 파일). ‹›✕ 만 허용 ──
def test_detail_no_decorative_emoji():
    # 주입 JS 마크업까지 노출되도록 이미지·지도·폴리곤 있는 컨텍스트로 렌더
    out = _render(
        kakao_key="TESTKEY",
        polygon=[[126.70, 37.60], [126.71, 37.60], [126.71, 37.61]],
        lh_images=[ns(url="https://apply.lh.or.kr/lhImageView2.do?fileid=1", label="조감도", name="a.jpg")],
    )
    found = set(_EMOJI.findall(out)) - _ALLOWED
    assert found == set(), f"렌더 결과에 장식 이모지: {found}"
    src = DETAIL.read_text(encoding="utf-8")
    found_src = set(_EMOJI.findall(src)) - _ALLOWED
    assert found_src == set(), f"소스 detail.html에 장식 이모지: {found_src}"


# ── JS 보존 + 브랜드 색/변수 교체 검증 ──
def test_detail_js_preserved_and_recolored():
    out = _render(
        kakao_key="TESTKEY",
        adres="경기도 김포시 걸포동 123",
        polygon=[[126.70, 37.60], [126.71, 37.60], [126.71, 37.61]],
        lh_images=[ns(url="https://apply.lh.or.kr/lhImageView2.do?fileid=1", label="조감도", name="a.jpg")],
    )
    # 카카오 지도 로직 보존
    assert 'id="map"' in out
    assert "dapi.kakao.com" in out and "TESTKEY" in out
    assert 'onerror="mapLoadFailed()"' in out
    assert "tryGeocode" in out
    assert "지도를 불러오지 못했어요" in out
    assert "지도에서 위치를 찾지 못했어요" in out
    assert "setBounds" in out
    assert "12000" in out  # 타임아웃 보존
    # 브랜드 색 교체: teal 리터럴 존재 · carrot 완전 제거
    assert "#1a3a3a" in out
    assert "#FF6F0F" not in out
    # 주입 안내문 이모지 → SVG use (i-map은 주입부에서만 사용됨)
    assert 'href="#i-map"' in out
    assert 'href="#i-pin"' in out
    assert "\U0001f5fa" not in out  # 🗺️
    assert "\U0001f4cd" not in out  # 📍
    # 라이트박스 보존
    assert 'id="lightbox"' in out
    assert "lb-prev" in out and "lb-next" in out
    # 옛 CSS 변수명 잔존 금지 (base 미정의 → 마이그레이션 완료)
    assert "var(--mut)" not in out
    assert "var(--sub)" not in out
    assert "var(--line)" not in out
