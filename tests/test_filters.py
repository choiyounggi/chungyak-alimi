from __future__ import annotations

import pytest

from src.filters import FilterConfig, match_notice
from src.models import ApplyhomeHouseType, ApplyhomeNotice

from test_applyhome import SAMPLE
from test_housetype import SAMPLE_HT


def _notice(**over) -> ApplyhomeNotice:
    return ApplyhomeNotice.model_validate({**SAMPLE, **over})


def _ht(**over) -> ApplyhomeHouseType:
    return ApplyhomeHouseType.model_validate({**SAMPLE_HT, **over})


CFG = FilterConfig(
    regions=["서울", "경기", "인천"],
    special_supply=["생애최초", "신혼부부"],
    price_max_manwon=80000,
)


# ── 정상: 조건 모두 충족 → 매칭 ──
def test_match_pass():
    n = _notice(SUBSCRPT_AREA_CODE_NM="경기")
    ht = _ht(LTTOT_TOP_AMOUNT="50000", LFE_FRST_HSHLDCO=10)  # 생애최초 있음, 5억
    matched, fails = match_notice(n, [ht], CFG)
    assert matched is True
    assert fails == []


# ── 지역 탈락 ──
def test_region_fail():
    n = _notice(SUBSCRPT_AREA_CODE_NM="부산")
    ht = _ht(LFE_FRST_HSHLDCO=10)
    matched, fails = match_notice(n, [ht], CFG)
    assert matched is False
    assert any("지역" in f for f in fails)


# ── 분양가 초과 탈락 (경계값) ──
def test_price_over_fail():
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울")
    ht = _ht(LTTOT_TOP_AMOUNT="90000", LFE_FRST_HSHLDCO=10)  # 9억 > 8억
    matched, fails = match_notice(n, [ht], CFG)
    assert matched is False
    assert "분양가초과" in fails


# ── 분양가: 여러 주택형 중 하나라도 상한 이하면 통과 ──
def test_price_any_under_passes():
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울")
    hts = [
        _ht(HOUSE_TY="A", LTTOT_TOP_AMOUNT="90000", LFE_FRST_HSHLDCO=1),
        _ht(HOUSE_TY="B", LTTOT_TOP_AMOUNT="70000", LFE_FRST_HSHLDCO=1),
    ]
    matched, _ = match_notice(n, hts, CFG)
    assert matched is True


# ── 특별공급 없음 탈락 ──
def test_no_special_supply_fail():
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울")
    ht = _ht(LTTOT_TOP_AMOUNT="50000", LFE_FRST_HSHLDCO=0, NWBB_HSHLDCO=0)
    matched, fails = match_notice(n, [ht], CFG)
    assert matched is False
    assert "특공없음" in fails


# ── 경계: 빈 필터는 전부 통과 ──
def test_empty_config_passes_all():
    n = _notice(SUBSCRPT_AREA_CODE_NM="부산")
    matched, fails = match_notice(n, [], FilterConfig())
    assert matched is True
    assert fails == []


# ── 분양가 정보 없으면(임대 등) 가격 조건 보류(통과) ──
def test_no_price_info_holds():
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울")
    ht = _ht(LTTOT_TOP_AMOUNT="", NWBB_HSHLDCO=5)  # 분양가 없음, 신혼부부 있음
    matched, fails = match_notice(n, [ht], CFG)
    assert "분양가초과" not in fails
    assert matched is True


# ── 설정 로드 ──
def test_load_config():
    cfg = FilterConfig(regions=["서울"], price_max_manwon=80000)
    assert cfg.regions == ["서울"]
    assert cfg.price_max_manwon == 80000
    with pytest.raises(Exception):
        FilterConfig(price_max_manwon="여덟억")  # 타입 오류
