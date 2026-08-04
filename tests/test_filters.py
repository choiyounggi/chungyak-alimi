from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.filters import FilterConfig, find_superseded, load_filter_config, match_notice
from src.models import ApplyhomeHouseType, ApplyhomeNotice

from test_applyhome import SAMPLE
from test_housetype import SAMPLE_HT

TODAY = date(2026, 7, 1)  # 테스트 기준일 고정(실행 시점 의존 제거)


def _notice(**over) -> ApplyhomeNotice:
    # 기본 접수마감을 미래로 둬서 기간필터를 통과시킨다(개별 테스트에서 덮어씀).
    base = {**SAMPLE, "RCEPT_ENDDE": "2026-07-29", "SPSPLY_RCEPT_ENDDE": ""}
    return ApplyhomeNotice.model_validate({**base, **over})


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
    matched, fails = match_notice(n, [ht], CFG, today=TODAY)
    assert matched is True
    assert fails == []


# ── 지역 탈락 ──
def test_region_fail():
    n = _notice(SUBSCRPT_AREA_CODE_NM="부산")
    ht = _ht(LFE_FRST_HSHLDCO=10)
    matched, fails = match_notice(n, [ht], CFG, today=TODAY)
    assert matched is False
    assert any("지역" in f for f in fails)


# ── 분양가 초과 탈락 (경계값) ──
def test_price_over_fail():
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울")
    ht = _ht(LTTOT_TOP_AMOUNT="90000", LFE_FRST_HSHLDCO=10)  # 9억 > 8억
    matched, fails = match_notice(n, [ht], CFG, today=TODAY)
    assert matched is False
    assert "분양가초과" in fails


# ── 분양가: 여러 주택형 중 하나라도 상한 이하면 통과 ──
def test_price_any_under_passes():
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울")
    hts = [
        _ht(HOUSE_TY="A", LTTOT_TOP_AMOUNT="90000", LFE_FRST_HSHLDCO=1),
        _ht(HOUSE_TY="B", LTTOT_TOP_AMOUNT="70000", LFE_FRST_HSHLDCO=1),
    ]
    matched, _ = match_notice(n, hts, CFG, today=TODAY)
    assert matched is True


# ── 특별공급 없음 탈락 ──
def test_no_special_supply_fail():
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울")
    ht = _ht(LTTOT_TOP_AMOUNT="50000", LFE_FRST_HSHLDCO=0, NWBB_HSHLDCO=0)
    matched, fails = match_notice(n, [ht], CFG, today=TODAY)
    assert matched is False
    assert "특공없음" in fails


# ── 기간: 접수마감 지난 공고 제외 ──
def test_closed_notice_excluded():
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울", RCEPT_ENDDE="2026-06-20")  # 과거
    ht = _ht(LTTOT_TOP_AMOUNT="50000", LFE_FRST_HSHLDCO=10)
    matched, fails = match_notice(n, [ht], CFG, today=TODAY)
    assert matched is False
    assert "접수마감" in fails


# ── 기간: 특공마감이 미래면 유효(둘 중 늦은 마감 기준) ──
def test_open_by_special_deadline():
    n = _notice(
        SUBSCRPT_AREA_CODE_NM="서울",
        RCEPT_ENDDE="2026-06-20",  # 일반은 지남
        SPSPLY_RCEPT_ENDDE="2026-07-10",  # 특공은 미래
    )
    ht = _ht(LTTOT_TOP_AMOUNT="50000", LFE_FRST_HSHLDCO=10)
    matched, fails = match_notice(n, [ht], CFG, today=TODAY)
    assert "접수마감" not in fails
    assert matched is True


# ── 기간: only_open=False 면 지난 공고도 통과 ──
def test_only_open_false_ignores_deadline():
    cfg = FilterConfig(regions=["서울"], only_open=False)
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울", RCEPT_ENDDE="2026-01-01")
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert matched is True
    assert "접수마감" not in fails


# ── 경계: 빈 필터는 전부 통과 ──
def test_empty_config_passes_all():
    n = _notice(SUBSCRPT_AREA_CODE_NM="부산")
    matched, fails = match_notice(n, [], FilterConfig(), today=TODAY)
    assert matched is True
    assert fails == []


# ── 분양가 정보 없으면(임대 등) 가격 조건 보류(통과) ──
def test_no_price_info_holds():
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울")
    ht = _ht(LTTOT_TOP_AMOUNT="", NWBB_HSHLDCO=5)  # 분양가 없음, 신혼부부 있음
    matched, fails = match_notice(n, [ht], CFG, today=TODAY)
    assert "분양가초과" not in fails
    assert matched is True


# ── 설정 로드 ──
def test_load_config():
    cfg = FilterConfig(regions=["서울"], price_max_manwon=80000)
    assert cfg.regions == ["서울"]
    assert cfg.price_max_manwon == 80000
    assert cfg.only_open is True  # 기본 켜짐
    with pytest.raises(Exception):
        FilterConfig(price_max_manwon="여덟억")  # 타입 오류


# ── 제외 키워드: 공고명에 포함되면 탈락 ──
# (배경 2026-07-06: exclude_keywords가 비어 있어 "고령자복지주택(영구임대)" 공고가
#  매칭·알림됨 — 연령제한/수급자 대상 공고 오매칭 회귀 방지)
def test_exclude_keyword_elderly_fail():
    cfg = FilterConfig(exclude_keywords=["고령자", "실버", "영구임대"])
    n = _notice(
        HOUSE_NM="성남시 분당목련1 분당한솔7 고령자복지주택(영구임대) 예비입주자 모집공고",
        SUBSCRPT_AREA_CODE_NM="경기",
    )
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert matched is False
    assert "제외키워드" in fails


def test_exclude_keyword_permanent_rental_fail():
    cfg = FilterConfig(exclude_keywords=["고령자", "실버", "영구임대"])
    n = _notice(
        HOUSE_NM="성남시 분당목련1 분당한솔7 분당청솔6 영구임대 예비입주자 모집공고",
        SUBSCRPT_AREA_CODE_NM="경기",
    )
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert matched is False
    assert "제외키워드" in fails


def test_exclude_keyword_absent_passes():
    cfg = FilterConfig(exclude_keywords=["고령자", "실버", "영구임대"])
    n = _notice(SUBSCRPT_AREA_CODE_NM="경기")  # 일반 공공분양 공고명
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert "제외키워드" not in fails
    assert matched is True


def test_exclude_keyword_empty_config_passes():
    cfg = FilterConfig(exclude_keywords=[])
    n = _notice(HOUSE_NM="고령자복지주택", SUBSCRPT_AREA_CODE_NM="경기")
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert "제외키워드" not in fails


# ── 제외 키워드: 국민임대(소득 70% 이하 대상) 탈락, 공공임대·행복주택은 유지 ──
def test_exclude_keyword_gungmin_rental():
    cfg = FilterConfig(exclude_keywords=["고령자", "실버", "영구임대", "국민임대"])
    gungmin = _notice(
        HOUSE_NM="용인시 용인구갈8 국민임대 예비입주자 모집공고", SUBSCRPT_AREA_CODE_NM="경기"
    )
    matched, fails = match_notice(gungmin, [], cfg, today=TODAY)
    assert matched is False and "제외키워드" in fails
    # 신혼 트랙에서 유효한 행복주택·10년 공공임대는 계속 통과 (경계값)
    happy = _notice(HOUSE_NM="여주역세권 행복주택 예비입주자 모집", SUBSCRPT_AREA_CODE_NM="경기")
    public10 = _notice(HOUSE_NM="김포한강 10년 공공임대주택리츠 예비입주자 모집", SUBSCRPT_AREA_CODE_NM="경기")
    assert "제외키워드" not in match_notice(happy, [], cfg, today=TODAY)[1]
    assert "제외키워드" not in match_notice(public10, [], cfg, today=TODAY)[1]


# ── 기관 필터(D19): [] = 전체 ──
def test_agency_filter_passes_when_empty():
    cfg = FilterConfig(agencies=[])
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울", agency="SH")
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert matched is True
    assert fails == []


# ── 기관 필터: 목록에 없는 기관은 탈락 + 사유에 기관명 ──
def test_agency_filter_rejects_other_agency():
    cfg = FilterConfig(agencies=["LH"])
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울", agency="SH")
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert matched is False
    assert "기관:SH" in fails
    # 같은 설정에서 LH 공고는 통과 (필터가 전량 탈락시키지 않음을 보장)
    lh = _notice(SUBSCRPT_AREA_CODE_NM="서울", agency="LH")
    assert match_notice(lh, [], cfg, today=TODAY) == (True, [])


# ── 임대보증금 상한(D18): 원 단위 컬럼 vs 만원 단위 설정 ──
def test_rent_deposit_over_limit_fails():
    cfg = FilterConfig(rent_deposit_max_manwon=15000)  # 1.5억
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울", rent_gtn=200_000_000)  # 2억
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert matched is False
    assert "임대보증금초과" in fails


# ── 임대보증금: 상한과 같으면 통과(경계값, inclusive) ──
def test_rent_deposit_at_limit_passes():
    cfg = FilterConfig(rent_deposit_max_manwon=15000)
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울", rent_gtn=150_000_000)
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert matched is True
    assert "임대보증금초과" not in fails
    # 1원만 넘어도 탈락 (off-by-one)
    over = _notice(SUBSCRPT_AREA_CODE_NM="서울", rent_gtn=150_000_001)
    assert "임대보증금초과" in match_notice(over, [], cfg, today=TODAY)[1]


# ── 임대보증금: 보증금 정보 없는 공고(분양 등)는 판정 보류 ──
def test_rent_deposit_none_is_skipped():
    cfg = FilterConfig(rent_deposit_max_manwon=15000)
    n = _notice(SUBSCRPT_AREA_CODE_NM="서울", rent_gtn=None)
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert matched is True
    assert "임대보증금초과" not in fails
    # rent_gtn 속성 자체가 없는 모델(청약홈 등)도 동일하게 보류
    bare = _notice(SUBSCRPT_AREA_CODE_NM="서울")
    assert not hasattr(bare, "rent_gtn")
    assert match_notice(bare, [], cfg, today=TODAY) == (True, [])


# ── 임대 정책(D18): 기본 설정에서 영구임대·국민임대는 더 이상 제외키워드가 아니다 ──
def test_public_rental_keyword_no_longer_excluded():
    cfg = load_filter_config()  # config/filters.yaml 실제 로드
    assert "국민임대" not in cfg.exclude_keywords
    assert "영구임대" not in cfg.exclude_keywords
    assert cfg.exclude_keywords == ["고령자", "실버"]  # 연령제한만 유지
    n = _notice(HOUSE_NM="○○ 국민임대주택 예비입주자 모집", SUBSCRPT_AREA_CODE_NM="경기")
    matched, fails = match_notice(n, [], cfg, today=TODAY)
    assert "제외키워드" in match_notice(
        _notice(HOUSE_NM="고령자복지주택 모집", SUBSCRPT_AREA_CODE_NM="경기"), [], cfg, today=TODAY
    )[1]  # 고령자는 여전히 탈락 (필터가 꺼진 게 아님을 보장)
    assert "제외키워드" not in fails
    assert matched is True


# ── 정정공고 대체(D25): 주택관리번호가 있으면 소스와 무관하게 같은 그룹 ──
def _myhome(pblanc_no: str, hmn: str, name: str, pblanc_de: date):
    return SimpleNamespace(
        pblanc_no=pblanc_no,
        source="myhome",
        house_manage_no=hmn,
        house_nm=name,
        rcrit_pblanc_de=pblanc_de,
        rcept_bgnde=None,
    )


def test_superseded_groups_by_house_manage_no_for_any_source():
    # 공고명이 서로 달라 이름 기반 그룹으로는 묶이지 않는다 — 주택관리번호로만 묶여야 한다.
    ns = [
        _myhome("myhome:P1-1", "P1-1", "○○ 국민임대 입주자모집", date(2026, 6, 1)),
        _myhome("myhome:P2-1", "P1-1", "[정정공고]○○ 국민임대 입주자모집(2차)", date(2026, 6, 10)),
    ]
    assert find_superseded(ns) == {"myhome:P1-1": "myhome:P2-1"}
    # 주택관리번호가 다르면(형제 단지) 이름이 같아도 서로를 대체하지 않는다 — D27 경계
    sib = [
        _myhome("myhome:P1-1", "P1-1", "○○ 국민임대 입주자모집", date(2026, 6, 1)),
        _myhome("myhome:P1-2", "P1-2", "○○ 국민임대 입주자모집", date(2026, 6, 1)),
    ]
    assert find_superseded(sib) == {}
