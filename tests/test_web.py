from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from src.db import (
    MatchResult,
    Notice,
    NoticeHouseType,
    SessionLocal,
    engine,
    init_db,
    save_match_results,
    upsert_house_types,
    upsert_notice_analysis,
    upsert_notices,
)
from src.models import ApplyhomeHouseType, ApplyhomeNotice
from src.web.app import app, matched_dashboard

from test_applyhome import SAMPLE
from test_auth_routes import login_client
from test_housetype import SAMPLE_HT


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


@pytest.fixture
def seeded():
    init_db()
    s = SessionLocal()
    for t in (MatchResult, NoticeHouseType, Notice):
        s.execute(delete(t))
    s.commit()
    # 매칭 공고 1건 + 주택형 + match_result
    n = ApplyhomeNotice.model_validate({**SAMPLE, "PBLANC_NO": "W1", "HOUSE_MANAGE_NO": "W1"})
    ht = ApplyhomeHouseType.model_validate({**SAMPLE_HT, "PBLANC_NO": "W1"})
    upsert_notices([n], session=s)
    upsert_house_types([ht], session=s)
    save_match_results([("applyhome:W1", True, [])], session=s)
    yield s
    for t in (MatchResult, NoticeHouseType, Notice):
        s.execute(delete(t))
    s.commit()
    s.close()


# ── 대시보드 데이터: 매칭 공고 + 분양가 계산 ──
def test_matched_dashboard(seeded):
    items = matched_dashboard(seeded)
    assert len(items) == 1
    it = items[0]
    assert it["notice"].pblanc_no == "applyhome:W1"
    assert it["price_lo"] == 50724  # SAMPLE_HT LTTOT_TOP_AMOUNT


# ── 인덱스 렌더: 200 + 공고명 포함 ──
def test_index_renders(seeded):
    client = login_client()  # `/`는 회원 로그인 보호 라우트
    r = client.get("/")
    assert r.status_code == 200
    assert SAMPLE["HOUSE_NM"] in r.text


# ── 대시보드: 카드 필터링용 특공 라벨 (주택형 raw 세대수 기반) ──
def test_dashboard_specials_from_house_type(seeded):
    ht = ApplyhomeHouseType.model_validate(
        {**SAMPLE_HT, "PBLANC_NO": "W1", "NWBB_HSHLDCO": "12", "LFE_FRST_HSHLDCO": "0"}
    )
    upsert_house_types([ht], session=seeded)
    items = matched_dashboard(seeded)
    assert items[0]["specials"] == ["신혼부부"]  # 0세대(생애최초)는 제외


# ── 대시보드: 이름 기반 특공 라벨 (신혼희망타운 등 세대수 필드 없는 소스) ──
def test_dashboard_specials_from_name(seeded):
    n = ApplyhomeNotice.model_validate(
        {**SAMPLE, "PBLANC_NO": "W2", "HOUSE_MANAGE_NO": "W2", "HOUSE_NM": "행복 신혼희망타운"}
    )
    upsert_notices([n], session=seeded)
    save_match_results([("applyhome:W2", True, [])], session=seeded)
    items = matched_dashboard(seeded)
    by_no = {it["notice"].pblanc_no: it for it in items}
    assert by_no["applyhome:W2"]["specials"] == ["신혼부부"]
    assert by_no["applyhome:W1"]["specials"] == []  # 특공 정보 없음 → 빈 목록 (경계값)


# ── 인덱스 렌더: 필터 칩(지역/특공) + 카드 data 속성 ──
def test_index_filter_chips(seeded):
    client = login_client()
    r = client.get("/")
    assert 'data-ftype="area"' in r.text  # filters.yaml regions → 클릭 칩
    assert 'data-ftype="special"' in r.text
    assert 'data-area="경기"' in r.text  # 카드 매칭 속성
    assert "js-empty" in r.text


# ── 상세: 회원 로그인 게이트 ──
def test_detail_allows_logged_in_member(seeded):
    """로그인한 회원은 상세를 그대로 본다 — 목록에서 눌렀는데 로그인 화면으로 튕기지 않는다."""
    r = login_client().get("/notice/applyhome:W1", follow_redirects=False)
    assert r.status_code == 200


def test_detail_redirects_anonymous_to_login(seeded):
    """미로그인은 다른 보호 페이지와 동일하게 /login 으로 303."""
    r = TestClient(app).get("/notice/applyhome:W1", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# ── 상세 페이지: 렌더 + 주택형/특공 표시 ──
def test_detail_renders(seeded):
    client = login_client()
    r = client.get("/notice/applyhome:W1")
    assert r.status_code == 200
    assert SAMPLE["HOUSE_NM"] in r.text
    assert SAMPLE_HT["HOUSE_TY"] in r.text  # 주택형(055.9200A)
    assert "주택형별 모집" in r.text
    assert "자격요건" in r.text  # 공고문 안내 섹션


# ── 상세: LH 단지 이미지 갤러리 + 공고문 PDF 링크 ──
def test_detail_lh_images_and_files(seeded):
    n = ApplyhomeNotice.model_validate(
        {**SAMPLE, "PBLANC_NO": "LHW1", "HOUSE_MANAGE_NO": None, "raw": {
            **SAMPLE,
            "_lh_detail": {
                "adres": "경기도 김포시", "schedule": [], "pan_dtl_cts": "",
                "images": [{"label": "단지조감도", "name": "조감도.jpg",
                            "url": "https://apply.lh.or.kr/lhapply/lhImageView2.do?fileid=1"}],
                "files": [{"label": "공고문(PDF)", "name": "공고문.pdf",
                           "url": "https://apply.lh.or.kr/lhapply/lhFile.do?fileid=2"}],
            },
        }}
    )
    upsert_notices([n], source="lh", session=seeded)
    save_match_results([("lh:LHW1", True, [])], session=seeded)
    client = login_client()
    r = client.get("/notice/lh:LHW1")
    assert "단지 이미지" in r.text
    assert "lhImageView2.do?fileid=1" in r.text
    assert "단지조감도" in r.text
    assert "lhFile.do?fileid=2" in r.text  # 공고문 PDF 직링크
    # 라이트박스: 팝업 마크업 + 좌우 넘김 버튼
    assert 'id="lightbox"' in r.text
    assert "lb-prev" in r.text and "lb-next" in r.text
    # 이미지 없는 공고(W1)엔 갤러리·라이트박스 미노출 (경계값)
    r2 = client.get("/notice/applyhome:W1")
    assert "단지 이미지" not in r2.text
    assert 'id="lightbox"' not in r2.text


# ── 상세: 없는 공고 404 ──
def test_detail_not_found():
    assert login_client().get("/notice/NOPE").status_code == 404


# ── healthz (인증 불필요) ──
def test_healthz():
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── 인증: 회원 세션 로그인 하나뿐 — 로그인/로그아웃/보호 라우트 플로우는
#    tests/test_auth_routes.py 가 검증한다.


# ── 상세: 카카오 지도(키 설정 시 렌더, 없으면 미렌더) ──
def test_detail_map(seeded, monkeypatch):
    from src.web import app as webapp

    monkeypatch.setattr(webapp.settings, "kakao_js_key", "TESTKAKAOKEY")
    r = login_client().get("/notice/applyhome:W1")
    assert 'id="map"' in r.text
    assert "dapi.kakao.com" in r.text
    assert "TESTKAKAOKEY" in r.text
    # 로드 실패 fallback: sdk onerror + 엔진 부분실패 가드 + 안내 문구
    assert 'onerror="mapLoadFailed()"' in r.text
    assert "지도를 불러오지 못했어요" in r.text
    assert "새로고침" in r.text
    # 지오코딩 사다리 폴백 + 전부 실패 시 안내(서울시청 방치 회귀 방지)
    assert "tryGeocode" in r.text
    assert "지도에서 위치를 찾지 못했어요" in r.text


def test_detail_no_map_without_key(seeded, monkeypatch):
    from src.web import app as webapp

    monkeypatch.setattr(webapp.settings, "kakao_js_key", "")
    r = login_client().get("/notice/applyhome:W1")
    assert 'id="map"' not in r.text


# ── 상세: 공고문 자동 요약 섹션 (PDF 분석 정상) ──
def test_detail_analysis_renders(seeded):
    upsert_notice_analysis(
        "applyhome:W1",
        "gh",
        [
            {
                "name": "공고문.pdf",
                "url": "https://example.com/a.pdf",
                "ok": True,
                "error": None,
                "page_count": 3,
                "text_chars": 500,
                "sections": [
                    {"key": "rank1", "title": "1순위 조건", "lines": ["무주택세대구성원", "해당지역 거주"]}
                ],
            }
        ],
        session=seeded,
    )
    seeded.commit()
    r = login_client().get("/notice/applyhome:W1")
    assert r.status_code == 200
    assert "공고문 자동 요약" in r.text
    assert "1순위 조건" in r.text
    assert "무주택세대구성원" in r.text
    assert "정확한 내용은 반드시 원문 공고문을 확인" in r.text


# ── 상세: 분석 없음/실패-only 인 경우 요약 섹션 미표시 (경계값) ──
def test_detail_analysis_absent_or_failed_only_not_shown(seeded):
    client = login_client()
    r = client.get("/notice/applyhome:W1")
    assert r.status_code == 200
    assert "공고문 자동 요약" not in r.text  # 분석 행 없음

    upsert_notice_analysis(
        "applyhome:W1",
        "gh",
        [
            {
                "name": "공고문.pdf",
                "url": "https://example.com/a.pdf",
                "ok": False,
                "error": "다운로드 실패",
                "page_count": 0,
                "text_chars": 0,
                "sections": [],
            }
        ],
        session=seeded,
    )
    seeded.commit()
    r2 = client.get("/notice/applyhome:W1")
    assert r2.status_code == 200
    assert "공고문 자동 요약" not in r2.text  # 실패-only 파일뿐 → 미표시


# ── 상세: 요약 lines 는 Jinja 자동 이스케이프로 무해화 (외부 PDF 텍스트는 신뢰 불가) ──
def test_detail_analysis_escapes_html(seeded):
    upsert_notice_analysis(
        "applyhome:W1",
        "gh",
        [
            {
                "name": "공고문.pdf",
                "url": "https://example.com/a.pdf",
                "ok": True,
                "error": None,
                "page_count": 1,
                "text_chars": 10,
                "sections": [
                    {"key": "notes", "title": "유의사항", "lines": ["<script>alert(1)</script>"]}
                ],
            }
        ],
        session=seeded,
    )
    seeded.commit()
    r = login_client().get("/notice/applyhome:W1")
    assert r.status_code == 200
    assert "<script>alert" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
