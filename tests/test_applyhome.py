from __future__ import annotations

from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from src.collectors.applyhome import fetch_apt_notices
from src.models import ApplyhomeNotice

# 실제 API 응답(getAPTLttotPblancDetail) 1건 축약본
SAMPLE = {
    "PBLANC_NO": "2026000320",
    "HOUSE_MANAGE_NO": "2026000320",
    "HOUSE_NM": "고양창릉 S-4블록 공공분양주택(본청약)",
    "HOUSE_SECD_NM": "APT",
    "HOUSE_DTL_SECD_NM": "국민",
    "RENT_SECD_NM": "분양주택",
    "SUBSCRPT_AREA_CODE_NM": "경기",
    "HSSPLY_ADRES": "경기도 고양시 덕양구 도내동 외 8개동 일원",
    "RCRIT_PBLANC_DE": "2026-06-30",
    "RCEPT_BGNDE": "2026-07-20",
    "RCEPT_ENDDE": "2026-07-29",
    "SPSPLY_RCEPT_BGNDE": "",  # 미정 → None 이어야 함
    "SPSPLY_RCEPT_ENDDE": None,
    "PRZWNER_PRESNATN_DE": "2026-08-19",
    "TOT_SUPLY_HSHLDCO": 1024,
    "MVN_PREARNGE_YM": "203003",
    "PBLANC_URL": "https://www.applyhome.co.kr/...",
    "BSNS_MBY_NM": "한국토지주택공사 경기북부지역본부",
}


def _client(pages: list[list[dict]]) -> httpx.Client:
    """페이지별 data 배열을 돌려주는 MockTransport 클라이언트."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        data = pages[page - 1] if 0 <= page - 1 < len(pages) else []
        return httpx.Response(200, json={"currentCount": len(data), "data": data})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://test")


# ── 정상 ──
def test_parse_normal_record():
    n = ApplyhomeNotice.model_validate(SAMPLE)
    assert n.pblanc_no == "2026000320"
    assert n.area_nm == "경기"
    assert n.rcrit_pblanc_de == date(2026, 6, 30)
    assert n.tot_suply_hshldco == 1024
    assert n.raw["HOUSE_NM"] == SAMPLE["HOUSE_NM"]  # 원본 보존


# ── 경계값: 빈 문자열/None 날짜 → None ──
def test_empty_and_null_dates_become_none():
    n = ApplyhomeNotice.model_validate(SAMPLE)
    assert n.spsply_rcept_bgnde is None
    assert n.spsply_rcept_endde is None


# ── 경계값: 페이징이 빈 페이지에서 멈춘다 ──
def test_pagination_stops_on_empty_page():
    with _client([[SAMPLE, SAMPLE], []]) as c:
        result = fetch_apt_notices(per_page=2, client=c)
    assert len(result) == 2  # 2건 후 빈 페이지에서 종료


# ── 경계값: per_page 미만이면 다음 페이지 안 부른다 ──
def test_stops_when_page_not_full():
    with _client([[SAMPLE]]) as c:
        result = fetch_apt_notices(per_page=100, client=c)
    assert len(result) == 1


# ── 에러: 필수 필드(PBLANC_NO) 누락 → ValidationError ──
def test_missing_required_field_raises():
    bad = {k: v for k, v in SAMPLE.items() if k != "PBLANC_NO"}
    with pytest.raises(ValidationError):
        ApplyhomeNotice.model_validate(bad)


# ── javascript: URL 차단(XSS 방어) ──
def test_javascript_url_stripped():
    n = ApplyhomeNotice.model_validate({**SAMPLE, "PBLANC_URL": "javascript:alert(1)"})
    assert n.pblanc_url is None
    ok = ApplyhomeNotice.model_validate({**SAMPLE, "PBLANC_URL": "https://ok.kr"})
    assert ok.pblanc_url == "https://ok.kr"


# ── 선택 필드 누락은 허용 ──
def test_missing_optional_field_ok():
    minimal = {"PBLANC_NO": "x", "HOUSE_NM": "테스트"}
    n = ApplyhomeNotice.model_validate(minimal)
    assert n.area_nm is None
    assert n.tot_suply_hshldco is None
