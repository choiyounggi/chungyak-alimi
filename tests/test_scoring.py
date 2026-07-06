from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from src.scoring import (
    AccountInfo,
    FirstLifeInfo,
    IncomeInfo,
    Profile,
    homeless_years,
    judge_first_life,
    judge_newlywed,
    judge_notice,
    judge_rank,
    load_profile,
    score_points,
)

TODAY = date(2026, 7, 6)


def _profile(**over) -> Profile:
    base = dict(
        birth_date=date(1990, 3, 15),
        marriage_date=date(2022, 5, 1),
        is_household_head=True,
        household_all_homeless=True,
        dependents=2,
        region="서울",
        children_minor=1,
        account=AccountInfo(opened=date(2016, 1, 1), balance_manwon=1500),
        income=IncomeInfo(monthly_manwon=700, base_manwon=719, dual_income=True),
        real_estate_manwon=0,
        first_life=FirstLifeInfo(ever_owned_house=False, income_tax_5y=True, currently_earning=True),
    )
    base.update(over)
    return Profile(**base)


def _notice(regulated: bool = False, dtl: str = "민영", area: str = "서울", source: str = "applyhome"):
    raw = {"SPECLT_RDN_EARTH_AT": "Y" if regulated else "N"}
    return SimpleNamespace(
        raw=raw, area_nm=area, house_dtl_secd_nm=dtl, house_secd_nm="APT", source=source
    )


def _ht(area: float):
    return SimpleNamespace(suply_ar=area)


# ── 가점: 정상 계산 ──
def test_score_points_normal():
    # 1990-03생, 30세(2020-03)부터 무주택 6년 → 2*(6+1)=14점
    # 부양가족 2명 → 5*(2+1)=15점 / 통장 2016-01부터 10년 → 10+2=12점
    s = score_points(_profile(), today=TODAY)
    assert s == {"homeless": 14, "dependents": 15, "account": 12, "total": 41}


# ── 가점: 상한(경계값) — 무주택 32 · 부양 35 · 통장 17 ──
def test_score_points_caps():
    p = _profile(
        birth_date=date(1970, 1, 1),
        marriage_date=None,
        dependents=8,
        account=AccountInfo(opened=date(2000, 1, 1), balance_manwon=1500),
    )
    s = score_points(p, today=TODAY)
    assert (s["homeless"], s["dependents"], s["account"]) == (32, 35, 17)
    assert s["total"] == 84


# ── 가점: 통장 6개월 미만 1점 / 유주택 세대 무주택점수 0 (경계값) ──
def test_score_points_low_bounds():
    p = _profile(account=AccountInfo(opened=date(2026, 5, 1), balance_manwon=300))
    assert score_points(p, today=TODAY)["account"] == 1
    p2 = _profile(household_all_homeless=False)
    assert score_points(p2, today=TODAY)["homeless"] == 0


# ── 가점: 만30세 미만 미혼 → 무주택기간 미기산 0점 (경계값) ──
def test_score_points_under_30_single():
    p = _profile(birth_date=date(1996, 11, 6), marriage_date=None, dependents=0)
    s = score_points(p, today=TODAY)
    assert s["homeless"] == 0
    assert s["dependents"] == 5  # 부양가족 0명 기본 5점


# ── 신혼: 예비신혼부부(engaged) → 자격 인정 + 안내 문구 ──
def test_newlywed_engaged():
    p = _profile(marriage_date=None, engaged=True)
    out = judge_newlywed(p, today=TODAY)
    assert out["eligible"] is True and out["tier"] == "우선공급"
    assert any("예비신혼부부" in x for x in out["reasons"])
    # 생초는 혼인신고 전이면 1인가구 트랙(추첨제)
    single = _profile(marriage_date=None, engaged=True, children_minor=0)
    assert judge_first_life(single, today=TODAY)["tier"] == "추첨제"


# ── 무주택기간: 30세 이전 혼인 시 혼인신고일 기산 ──
def test_homeless_years_early_marriage():
    p = _profile(birth_date=date(2000, 1, 1), marriage_date=date(2024, 1, 1))
    # 30세(2030년) 전에 혼인 → 2024-01부터 2.5년
    assert 2.0 < homeless_years(p, TODAY) < 3.0


# ── 무주택기간: 주택 처분일이 있으면 그 이후부터 ──
def test_homeless_years_after_disposal():
    p = _profile(homeless_since=date(2024, 7, 1))
    assert homeless_years(p, TODAY) < 2.1


# ── 순위: 요건 충족 → 1순위 ──
def test_rank_first():
    r = judge_rank(_notice(), [_ht(84.9)], _profile(), today=TODAY)
    assert r["rank"] == "1순위" and not r["regulated"]


# ── 순위: 규제지역 + 세대주 아님 → 2순위 ──
def test_rank_regulated_not_head():
    r = judge_rank(_notice(regulated=True), [_ht(84.9)], _profile(is_household_head=False), today=TODAY)
    assert r["rank"] == "2순위"
    assert any("세대주" in x for x in r["reasons"])


# ── 순위: 예치금 부족 → 2순위 (경계값: 서울 85㎡ 이하 300만원) ──
def test_rank_deposit_insufficient():
    p = _profile(account=AccountInfo(opened=date(2016, 1, 1), balance_manwon=299))
    r = judge_rank(_notice(), [_ht(84.9)], p, today=TODAY)
    assert r["rank"] == "2순위"
    # 큰 평형만 부족하면 1순위 유지 + 안내만
    p2 = _profile(account=AccountInfo(opened=date(2016, 1, 1), balance_manwon=300))
    r2 = judge_rank(_notice(), [_ht(84.9), _ht(101.0)], p2, today=TODAY)
    assert r2["rank"] == "1순위"
    assert any("일부" in x for x in r2["reasons"])


# ── 신혼: 맞벌이 소득구간 판정 (우선/일반/추첨) ──
def test_newlywed_tiers():
    # 700/719 = 97% ≤ 120(맞벌이) → 우선공급
    assert judge_newlywed(_profile(), today=TODAY)["tier"] == "우선공급"
    p_gen = _profile(income=IncomeInfo(monthly_manwon=1100, base_manwon=719, dual_income=True))
    assert judge_newlywed(p_gen, today=TODAY)["tier"] == "일반공급"  # 153% ≤ 160
    p_lot = _profile(income=IncomeInfo(monthly_manwon=1300, base_manwon=719, dual_income=True))
    assert judge_newlywed(p_lot, today=TODAY)["tier"] == "추첨제"  # 181% 초과 + 자산 0
    p_bad = _profile(
        income=IncomeInfo(monthly_manwon=1300, base_manwon=719, dual_income=True),
        real_estate_manwon=40_000,
    )
    out = judge_newlywed(p_bad, today=TODAY)
    assert out["tier"] == "부적격" and out["eligible"] is False


# ── 신혼: 미혼/혼인 7년 초과 → 자격 없음 (에러 케이스) ──
def test_newlywed_ineligible():
    assert judge_newlywed(_profile(marriage_date=None), today=TODAY)["eligible"] is False
    old = judge_newlywed(_profile(marriage_date=date(2015, 1, 1)), today=TODAY)
    assert old["eligible"] is False and any("7년" in x for x in old["reasons"])


# ── 생초: 소득구간 + 1인가구 추첨 + 소유이력 부적격 ──
def test_first_life():
    # 97% ≤ 130 → 우선공급
    assert judge_first_life(_profile(), today=TODAY)["tier"] == "우선공급"
    single = _profile(marriage_date=None, children_minor=0)
    out = judge_first_life(single, today=TODAY)
    assert out["tier"] == "추첨제"  # 1인가구는 추첨제만
    owned = _profile(first_life=FirstLifeInfo(ever_owned_house=True, income_tax_5y=True, currently_earning=True))
    assert judge_first_life(owned, today=TODAY)["eligible"] is False


# ── 종합: 민영 지원 + 요약 문자열 ──
def test_judge_notice_supported():
    out = judge_notice(_notice(), [_ht(84.9)], _profile(), today=TODAY)
    assert out["supported"] is True
    assert "가점 41점" in out["summary"] and "1순위" in out["summary"]
    assert "신혼 우선공급" in out["summary"]


# ── 종합: LH/국민주택 미지원 (경계값) ──
def test_judge_notice_unsupported():
    assert judge_notice(_notice(source="lh", dtl=""), [], _profile(), today=TODAY)["supported"] is False
    assert judge_notice(_notice(dtl="국민"), [], _profile(), today=TODAY)["supported"] is False


# ── 프로필 로드: 파일 없으면 None (에러 케이스) ──
def test_load_profile_missing(tmp_path):
    assert load_profile(str(tmp_path / "nope.yaml")) is None


# ── 프로필 로드: example 파일이 스키마와 일치 ──
def test_load_profile_example():
    p = load_profile("config/profile.example.yaml")
    assert p is not None and p.account.opened is not None
