"""judge_rank 해당지역 판정(거주지 ∪ 소득본거지) — Task 09.

순수함수라 DB 불요. notice/house_types는 기존 test_scoring.py와 같은 경량 스텁.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.scoring import AccountInfo, Profile, judge_rank

TODAY = date(2026, 7, 6)


def _profile(**over) -> Profile:
    base = dict(
        birth_date=date(1990, 3, 15),
        is_household_head=True,
        household_all_homeless=True,
        region="서울",
        account=AccountInfo(opened=date(2016, 1, 1), balance_manwon=1500),
    )
    base.update(over)
    return Profile(**base)


def _notice(area: str | None = "서울"):
    return SimpleNamespace(
        raw={"SPECLT_RDN_EARTH_AT": "N"},
        area_nm=area,
        house_dtl_secd_nm="민영",
        house_secd_nm="APT",
        source="applyhome",
    )


def _ht(area: float = 84.9):
    return SimpleNamespace(suply_ar=area)


def _regions(residence=(), income_base=()) -> list[str]:
    """호출측(대시보드)이 거주지 ∪ 소득본거지로 구성하는 방식 그대로."""
    return list(dict.fromkeys([*residence, *income_base]))


# ── ① 거주지가 공고 지역과 일치 → 해당지역 ──
def test_in_area_by_residence():
    r = judge_rank(
        _notice("서울"),
        [_ht()],
        _profile(),
        TODAY,
        applicant_regions=_regions(residence=["서울특별시"], income_base=["부산"]),
    )
    assert r["in_area"] is True
    assert any("해당지역" in x for x in r["reasons"])
    assert r["rank"] == "1순위"


# ── ② 거주지는 불일치, 소득본거지만 일치 → 해당지역 (D19 사용자 정책) ──
def test_in_area_by_income_base_only():
    r = judge_rank(
        _notice("경기"),
        [_ht()],
        _profile(),
        TODAY,
        # 성남 근무(소득본거지) → 경기 공고는 포함 규칙으로 해당지역
        applicant_regions=_regions(residence=["서울"], income_base=["성남시"]),
    )
    assert r["in_area"] is True
    assert any("해당지역" in x for x in r["reasons"])


# ── ③ 둘 다 불일치 → 기타지역. 순위 판정에는 영향 없음 ──
def test_out_of_area_does_not_change_rank():
    regions = _regions(residence=["서울"], income_base=["성남시"])
    r = judge_rank(_notice("부산"), [_ht()], _profile(), TODAY, applicant_regions=regions)
    assert r["in_area"] is False
    assert any("기타지역" in x for x in r["reasons"])
    # 지역 사유는 통장/예치금/규제 요건이 아니므로 1순위를 떨어뜨리면 안 된다.
    assert r["rank"] == "1순위"


def test_out_of_area_keeps_second_rank_reasons():
    """이미 2순위인 경우에도 지역 사유가 기존 사유를 덮어쓰지 않는다."""
    p = _profile(account=AccountInfo(opened=date(2026, 5, 1), balance_manwon=1500))
    r = judge_rank(_notice("부산"), [_ht()], p, TODAY, applicant_regions=["서울"])
    assert r["rank"] == "2순위"
    assert any("통장 가입기간 부족" in x for x in r["reasons"])
    assert any("기타지역" in x for x in r["reasons"])


# ── ④ 경계: applicant_regions 미지정 → 기존 동작 그대로(하위호환) ──
def test_no_applicant_regions_is_backward_compatible():
    args = (_notice("서울"), [_ht()], _profile(), TODAY)
    legacy = judge_rank(*args)
    assert legacy["rank"] == "1순위"
    assert legacy["regulated"] is False
    assert legacy["reasons"] == []
    # 지역 판정을 하지 않았음을 in_area=None으로 구분(True/False와 다른 3상태).
    assert legacy["in_area"] is None
    assert not any("지역" in x for x in legacy["reasons"])

    # 명시적으로 None을 넘겨도 동일.
    assert judge_rank(*args, applicant_regions=None) == legacy


# ── ⑤ 경계: 빈 리스트 / 빈 area_nm → 기타지역 ──
@pytest.mark.parametrize(
    ("area", "regions"),
    [
        ("서울", []),
        (None, ["서울"]),
        ("", ["서울"]),
        (None, []),
        ("서울", ["", None]),
    ],
)
def test_empty_inputs_are_out_of_area(area, regions):
    r = judge_rank(_notice(area), [_ht()], _profile(), TODAY, applicant_regions=regions)
    assert r["in_area"] is False
    assert any("기타지역" in x for x in r["reasons"])
