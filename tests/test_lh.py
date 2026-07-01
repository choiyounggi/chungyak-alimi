from __future__ import annotations

from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from src.collectors.lh import (
    LhNotice,
    LhSupply,
    fetch_lh_detail,
    fetch_lh_notices,
    fetch_lh_supply,
    normalize_region,
)
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
    with pytest.raises(ValidationError):
        LhNotice.model_validate(d)


# ── 날짜 텍스트값("공고문 참조")은 None (크래시 방지) ──
def test_lhdate_text_becomes_none():
    n = LhNotice.model_validate({**SAMPLE_LH, "CLSG_DT": "공고문 참조"})
    assert n.rcept_endde is None


# ── javascript: URL 차단 ──
def test_lh_javascript_url_stripped():
    n = LhNotice.model_validate({**SAMPLE_LH, "DTL_URL": "javascript:alert(1)"})
    assert n.pblanc_url is None
    ok = LhNotice.model_validate({**SAMPLE_LH, "DTL_URL": "https://apply.lh.or.kr/x"})
    assert ok.pblanc_url == "https://apply.lh.or.kr/x"


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


SPL_RESPONSE = [
    {"dsSch": [{"PAN_ID": "x"}]},
    {
        "dsList01": [
            {"HTY_NNA": "84.9500A", "SPL_AR": "111.8836", "DDO_AR": "84.95",
             "HSH_CNT": "10", "LS_GMY": "공고문 참조"},
            {"HTY_NNA": "75.8400A", "SPL_AR": "99.8853", "DDO_AR": "75.84",
             "HSH_CNT": "3", "LS_GMY": "공고문 참조"},
        ],
        "resHeader": [{"SS_CODE": "Y"}],
    },
]


# ── 공급정보 파싱: 면적·세대수, 분양가는 None ──
def test_parse_lh_supply():
    s = LhSupply.model_validate(SPL_RESPONSE[1]["dsList01"][0])
    assert s.house_ty == "84.9500A"
    assert s.suply_ar == 111.8836
    assert s.suply_hshldco == 10
    assert s.lttot_top_amount is None  # LH 분양가 미제공


# ── C1 회귀 방어: LH 보강(특공 키 없는 주택형)돼도 특공 필터 통과 ──
def test_lh_special_filter_holds_after_enrichment():
    cfg = FilterConfig(regions=["경남"], special_supply=["생애최초", "신혼부부"])
    n = LhNotice.model_validate(SAMPLE_LH)
    ht = LhSupply.model_validate(SPL_RESPONSE[1]["dsList01"][0])  # raw엔 특공 세대수 키 없음
    matched, fails = match_notice(n, [ht], cfg, today=date(2026, 7, 1))
    assert matched is True  # 보강돼도 특공 판정 보류 → 탈락 안 함
    assert "특공없음" not in fails


# ── 공급정보 수집: dsList01 파싱 + pblanc_no 주입 ──
def test_fetch_lh_supply():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SPL_RESPONSE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = fetch_lh_supply(pan_id="P9", ccr="03", spl="051", upp="05", ais="05", client=c)
    assert len(out) == 2
    assert all(s.pblanc_no == "P9" for s in out)
    assert out[0].suply_ar == 111.8836


DTL_RESPONSE = [
    {"dsSch": [{"PAN_ID": "x"}]},
    {"dsSbd": [{
        "LCT_ARA_ADR": "경기도 고양시 덕양구 도내동",
        "LCT_ARA_DTL_ADR": "외 8개 동 일원",
        "MVIN_XPC_YM": "2030년 03월", "SUM_TOT_HSH_CNT": "1024",
    }]},
    {"dsSplScdl": [{
        "HS_SBSC_ACP_TRG_CD_NM": "사전청약당첨자",
        "ACP_DTTM": "2026.07.20 10:00 ~ 2026.07.21 17:00",
        "PZWR_ANC_DT": "20260819",
        "PZWR_PPR_SBM_ST_DT": "20260826", "PZWR_PPR_SBM_ED_DT": "20260830",
        "CTRT_ST_DT": "20261116", "CTRT_ED_DT": "20261119",
    }]},
    {"dsEtcInfo": [{"PAN_DTL_CTS": "■ 공급위치 : 경기도 고양시 ..."}]},
    {"resHeader": [{"SS_CODE": "Y"}]},
]


# ── 상세정보 파싱: 주소 결합 · 서류제출 기간 · 공고전문 ──
def test_fetch_lh_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DTL_RESPONSE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        d = fetch_lh_detail(pan_id="P", ccr="02", spl="050", upp="05", ais="05", client=c)
    assert d["adres"] == "경기도 고양시 덕양구 도내동 외 8개 동 일원"
    assert d["schedule"][0]["anc"] == "2026-08-19"
    assert d["schedule"][0]["sbm"] == "2026-08-26 ~ 2026-08-30"  # 서류제출 기간
    assert "공급위치" in d["pan_dtl_cts"]


# ── 상세정보: SS_CODE 오류면 None ──
def test_fetch_lh_detail_ss_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"resHeader": [{"SS_CODE": "E"}]}])

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert fetch_lh_detail(pan_id="P", ccr="1", spl="1", upp="1", ais="1", client=c) is None
