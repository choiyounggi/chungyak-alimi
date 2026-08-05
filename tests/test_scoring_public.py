"""국민주택(공공) 1·2순위 + 거주기간/예비신혼 검증 — Task 03.

순수 판정 함수라 DB 불요. notice/house_types 는 기존 test_scoring.py 와 같은 경량 스텁.
납입횟수가 실제로 순위에 반영되는지가 이 파일의 핵심 관심사다.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.scoring import (
    AccountInfo,
    IncomeInfo,
    PartnerInfo,
    Profile,
    ResidencePeriod,
    judge_newlywed,
    judge_notice,
    judge_rank,
    judge_rank_public,
    residence_years_in,
)

TODAY = date(2026, 7, 6)


def _profile(**over) -> Profile:
    """수도권 국민주택 1순위 요건을 갓 충족하는 기준 프로필(가입 13개월·납입 13회)."""
    base = dict(
        birth_date=date(1990, 3, 15),
        is_household_head=True,
        household_all_homeless=True,
        region="서울",
        account=AccountInfo(opened=date(2025, 6, 1), balance_manwon=1500),
        account_payment_count=13,
    )
    base.update(over)
    return Profile(**base)


def _notice(regulated: bool = False, area: str | None = "서울", dtl: str = "국민",
            source: str = "applyhome"):
    return SimpleNamespace(
        raw={"SPECLT_RDN_EARTH_AT": "Y" if regulated else "N"},
        area_nm=area,
        house_dtl_secd_nm=dtl,
        house_secd_nm="APT",
        source=source,
    )


def _ht(area: float = 84.9):
    return SimpleNamespace(suply_ar=area)


# ── ① 정상: 수도권 국민주택 — 가입 13개월·납입 13회·무주택 → 1순위 ──
def test_public_rank_first_capital():
    r = judge_rank_public(_notice(), _profile(), TODAY)
    assert r["rank"] == "1순위"
    assert r["reasons"] == []
    assert r["regulated"] is False
    assert r["in_area"] is None  # applicant_regions 미지정 → 지역 판정 안 함


# ── ② 정상: 비수도권(other 그룹 6개월/6회) — 가입 7개월·납입 7회 → 1순위 ──
def test_public_rank_first_other_area():
    p = _profile(account=AccountInfo(opened=date(2025, 12, 1)), account_payment_count=7)
    r = judge_rank_public(_notice(area="부산"), p, TODAY)
    assert r["rank"] == "1순위"
    assert r["reasons"] == []


# ── ③ 에러: 납입횟수만 미달(가입기간은 충족) → 2순위 + 납입횟수 사유 ──
def test_public_rank_payment_count_shortfall():
    r = judge_rank_public(_notice(), _profile(account_payment_count=11), TODAY)
    assert r["rank"] == "2순위"
    assert any("납입횟수" in x for x in r["reasons"])
    # 가입기간은 충족했으므로 그 사유는 붙지 않는다.
    assert not any("가입기간" in x for x in r["reasons"])


# ── ④ 에러: 유주택 세대 → 2순위 ──
def test_public_rank_not_homeless():
    r = judge_rank_public(_notice(), _profile(household_all_homeless=False), TODAY)
    assert r["rank"] == "2순위"
    assert any("무주택" in x for x in r["reasons"])


# ── ⑤ 에러: 규제지역인데 세대주 아님 → 2순위 ──
def test_public_rank_regulated_not_household_head():
    # 규제지역 요건(24개월/24회)은 충족시켜 세대주 사유만 남긴다.
    p = _profile(
        is_household_head=False,
        account=AccountInfo(opened=date(2024, 1, 1)),
        account_payment_count=30,
    )
    r = judge_rank_public(_notice(regulated=True), p, TODAY)
    assert r["rank"] == "2순위"
    assert r["regulated"] is True
    assert any("세대주" in x for x in r["reasons"])
    assert not any("납입횟수" in x for x in r["reasons"])


# ── ⑥ 경계: 수도권 납입횟수 정확히 12회 → 1순위 / 11회 → 2순위 (off-by-one) ──
@pytest.mark.parametrize(("count", "expected"), [(12, "1순위"), (11, "2순위")])
def test_public_rank_payment_count_boundary(count, expected):
    r = judge_rank_public(_notice(), _profile(account_payment_count=count), TODAY)
    assert r["rank"] == expected


# ── ⑦ 경계: 가입기간 정확히 12개월 → 1순위 / 11개월 → 2순위 ──
@pytest.mark.parametrize(
    ("opened", "expected"),
    [(date(2025, 7, 6), "1순위"), (date(2025, 8, 6), "2순위")],
)
def test_public_rank_account_months_boundary(opened, expected):
    r = judge_rank_public(_notice(), _profile(account=AccountInfo(opened=opened)), TODAY)
    assert r["rank"] == expected


# ── ⑧ 경계: 통장 가입일 미입력(None) → 예외 없이 2순위, 0개월로 계산 ──
def test_public_rank_account_opened_none():
    r = judge_rank_public(_notice(), _profile(account=AccountInfo(opened=None)), TODAY)
    assert r["rank"] == "2순위"
    assert any("0개월" in x for x in r["reasons"])


# ── ⑨ 경계: 규제지역 그룹은 24개월/24회 — 13개월·13회는 둘 다 미달 ──
def test_public_rank_regulated_thresholds_are_higher():
    r = judge_rank_public(_notice(regulated=True), _profile(), TODAY)
    assert r["rank"] == "2순위"
    assert any("24개월" in x for x in r["reasons"])
    assert any("24회" in x for x in r["reasons"])


# ── ⑩ 정상: 거주연수는 이력 중 최대값, 지역은 정규형으로 매칭 ──
def test_residence_years_in_returns_max_with_normalized_region():
    p = _profile(
        residence_history=[
            ResidencePeriod(region="서울특별시", since=date(2020, 1, 1)),
            ResidencePeriod(region="서울", since=date(2023, 1, 1)),
        ]
    )
    years = residence_years_in(p, "서울", TODAY)
    assert 6.5 < years < 6.6  # 2020-01-01 기준 최대값("서울특별시"도 서울로 매칭)


# ── ⑪ 경계: 빈 이력 / since 없음 / region 없음 → 0.0 ──
def test_residence_years_in_empty_boundaries():
    assert residence_years_in(_profile(), "서울", TODAY) == 0.0  # residence_history=[]
    only_none = _profile(residence_history=[ResidencePeriod(region="서울", since=None)])
    assert residence_years_in(only_none, "서울", TODAY) == 0.0
    assert residence_years_in(only_none, None, TODAY) == 0.0
    assert residence_years_in(only_none, "", TODAY) == 0.0


# ── ⑫ 에러: 매칭되는 지역 이력이 없음 → 0.0 ──
def test_residence_years_in_no_matching_region():
    p = _profile(residence_history=[ResidencePeriod(region="서울", since=date(2020, 1, 1))])
    assert residence_years_in(p, "부산", TODAY) == 0.0


# ── ⑬ 경계(R3): 규제지역 거주기간 미달은 사유에만 붙고 1순위를 유지한다 ──
def test_regulated_residence_shortfall_does_not_lower_public_rank():
    p = _profile(
        account=AccountInfo(opened=date(2024, 6, 1)),  # 25개월 ≥ 24
        account_payment_count=25,
        residence_history=[ResidencePeriod(region="서울", since=date(2025, 7, 6))],  # 1.0년
    )
    r = judge_rank_public(_notice(regulated=True), p, TODAY)
    assert r["rank"] == "1순위"  # R3: 거주기간은 순위를 낮추지 않는다
    assert any("거주기간" in x for x in r["reasons"])


# ── ⑭ 회귀(R3): 민영 judge_rank — 비규제는 사유 없음, 규제는 사유만 붙고 1순위 유지 ──
def test_private_rank_residence_reason_is_non_blocking():
    p = _profile(account=AccountInfo(opened=date(2016, 1, 1), balance_manwon=1500))
    plain = judge_rank(_notice(dtl="민영"), [_ht()], p, TODAY)
    assert plain["rank"] == "1순위"
    assert plain["reasons"] == []  # 비규제 공고에는 거주기간 사유를 붙이지 않는다

    reg = judge_rank(_notice(regulated=True, dtl="민영"), [_ht()], p, TODAY)
    assert reg["rank"] == "1순위"  # 거주기간 사유가 순위를 떨어뜨리면 안 된다
    assert any("거주기간" in x for x in reg["reasons"])


# ── ⑮ R5: 예비신혼 상대방이 자가 보유면 부적격 / partners=[] 이면 기존 판정 불변 ──
def test_newlywed_partner_owning_home_is_ineligible():
    income = IncomeInfo(monthly_manwon=700, base_manwon=719, dual_income=True)
    base = dict(marriage_date=date(2022, 5, 1), engaged=True, income=income)

    owner = judge_newlywed(_profile(**base, partners=[PartnerInfo(owns_home=True)]), TODAY)
    assert owner["eligible"] is False
    assert owner["tier"] is None
    assert any("자가" in x for x in owner["reasons"])

    # 경계: partners=[] (기본값) → 기존 판정 그대로
    none_partner = judge_newlywed(_profile(**base), TODAY)
    assert none_partner["eligible"] is True
    assert none_partner["tier"] == "우선공급"

    # 경계: 파트너가 있어도 자가가 없으면 영향 없음
    renter = judge_newlywed(_profile(**base, partners=[PartnerInfo(owns_home=False)]), TODAY)
    assert renter["eligible"] is True
    assert renter["tier"] == "우선공급"


# ── ⑯ judge_notice 유형 분기: 국민 / 민영 / 판별불가 ──
def test_judge_notice_branches_by_housing_type():
    p = _profile()

    public = judge_notice(_notice(dtl="국민"), [], p, TODAY)
    assert public["supported"] is True
    assert public["housing_type"] == "국민"
    assert public["rank"] == judge_rank_public(_notice(dtl="국민"), p, TODAY)
    assert public["summary"].startswith("국민주택")

    private = judge_notice(_notice(dtl="민영"), [_ht()], p, TODAY)
    assert private["housing_type"] == "민영"
    # 기존 민영 응답 키가 전부 유지된다.
    assert {"supported", "score", "rank", "newlywed", "first_life", "summary"} <= set(private)
    assert private["rank"] == judge_rank(_notice(dtl="민영"), [_ht()], p, TODAY)

    # 에러: 민영도 국민도 아닌 공고(HUG 든든전세)
    unknown = judge_notice(_notice(source="hug", dtl="든든전세"), [], p, TODAY)
    assert unknown["supported"] is False
    assert unknown["housing_type"] is None
    assert "판정 미지원" in unknown["reason"]


# ── ⑰ 공공 수집원(lh/myhome/sh/gh)은 dtl 표기가 없어도 국민으로 본다 ──
@pytest.mark.parametrize("source", ["lh", "myhome", "sh", "gh"])
def test_public_sources_are_judged_as_public_housing(source):
    out = judge_notice(_notice(source=source, dtl=""), [], _profile(), TODAY)
    assert out["supported"] is True
    assert out["housing_type"] == "국민"


# ── ⑱ 계약: judge_rank_public 의 반환 키는 judge_rank 와 정확히 같다 ──
def test_public_rank_returns_same_keys_as_private_rank():
    p = _profile()
    public = judge_rank_public(_notice(), p, TODAY)
    private = judge_rank(_notice(dtl="민영"), [_ht()], p, TODAY)
    assert set(public) == set(private) == {"rank", "regulated", "reasons", "in_area"}


# ── ⑲ D19: 지역은 순위 요건이 아니다 — in_area 3상태 + rank 불변 ──
def test_applicant_regions_do_not_change_public_rank():
    p = _profile()
    none_given = judge_rank_public(_notice(), p, TODAY)
    matched = judge_rank_public(_notice(), p, TODAY, applicant_regions=["서울특별시"])
    missed = judge_rank_public(_notice(), p, TODAY, applicant_regions=["부산"])

    assert none_given["in_area"] is None
    assert matched["in_area"] is True
    assert missed["in_area"] is False
    assert none_given["rank"] == matched["rank"] == missed["rank"] == "1순위"
    assert any("해당지역" in x for x in matched["reasons"])
    assert any("기타지역" in x for x in missed["reasons"])
