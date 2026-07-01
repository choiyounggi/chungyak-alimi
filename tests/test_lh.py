from __future__ import annotations

from datetime import date

import httpx
import pytest

from src.collectors.lh import LhNotice, fetch_lh_notices, normalize_region
from src.filters import FilterConfig, match_notice

SAMPLE_LH = {
    "PAN_ID": "2015122300020254",
    "PAN_NM": "진주혁신더힐 잔여세대 일반매각 공고",
    "CNP_CD_NM": "경상남도",
    "UPP_AIS_TP_NM": "분양주택",
    "AIS_TP_CD_NM": "분양주택",
    "PAN_NT_ST_DT": "2026.06.30",
    "CLSG_DT": "2026.07.14",
    "PAN_SS": "공고중",
    "DTL_URL": "https://apply.lh.or.kr/...panId=2015122300020254",
}


# ── 지역 정규화 ──
def test_normalize_region():
    assert normalize_region("경기도") == "경기"
    assert normalize_region("서울특별시") == "서울"
    assert normalize_region("인천광역시") == "인천"
    assert normalize_region("강원특별자치도") == "강원"
    assert normalize_region(None) is None


# ── 파싱: 날짜(점)·지역·원본 보존 ──
def test_parse_lh_notice():
    n = LhNotice.model_validate(SAMPLE_LH)
    assert n.pblanc_no == "2015122300020254"
    assert n.area_nm == "경남"  # 경상남도 → 경남
    assert n.rcept_endde == date(2026, 7, 14)
    assert n.house_secd_nm == "분양주택"
    assert n.raw["CNP_CD_NM"] == "경상남도"  # 원본 보존


# ── 경계: 필수 PAN_ID 없으면 에러 ──
def test_missing_pan_id_raises():
    d = {k: v for k, v in SAMPLE_LH.items() if k != "PAN_ID"}
    with pytest.raises(Exception):
        LhNotice.model_validate(d)


# ── 수집: 유형 간 중복 PAN_ID 제거 ──
def test_fetch_dedup_across_types():
    def handler(request: httpx.Request) -> httpx.Response:
        # 어떤 유형이든 같은 공고 1건 반환 → 중복 제거되어 1건이어야
        return httpx.Response(200, json=[{"dsSch": []}, {"dsList": [SAMPLE_LH]}])

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = fetch_lh_notices(types=("05", "06"), per_page=100, client=c)
    assert len(out) == 1


# ── 필터: LH(주택형 없음)는 특공 필터를 보류하고 지역/기간으로 통과 ──
def test_lh_passes_without_house_types():
    cfg = FilterConfig(regions=["경남"], special_supply=["생애최초"], price_max_manwon=80000)
    n = LhNotice.model_validate(SAMPLE_LH)
    matched, fails = match_notice(n, [], cfg, today=date(2026, 7, 1))
    assert matched is True  # 특공/분양가 정보 없어도 통과
    assert "특공없음" not in fails


# ── 필터: LH 지역 불일치는 탈락 ──
def test_lh_region_mismatch():
    cfg = FilterConfig(regions=["서울"])
    n = LhNotice.model_validate(SAMPLE_LH)  # 경남
    matched, fails = match_notice(n, [], cfg, today=date(2026, 7, 1))
    assert matched is False
    assert any("지역" in f for f in fails)
