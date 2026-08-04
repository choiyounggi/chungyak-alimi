from __future__ import annotations

import pytest

from src.regions import normalize_region, region_matches


# ── 정상: 시/도 정규화 ──
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("서울특별시", "서울"),
        ("경기도", "경기"),
        ("인천광역시", "인천"),
        ("강원특별자치도", "강원"),
        ("전북특별자치도", "전북"),
        ("세종특별자치시", "세종"),
        ("충청남도", "충남"),
        ("제주특별자치도", "제주"),
        ("서울", "서울"),  # 이미 약칭(청약홈/SH/GH가 저장하는 형태)
        ("경기", "경기"),
    ],
)
def test_normalize_region_canonical(raw, expected):
    assert normalize_region(raw) == expected


def test_normalize_region_strips_whitespace():
    assert normalize_region("  경기도  ") == "경기"
    assert normalize_region("서울 특별시") == "서울"


def test_normalize_region_unknown_suffix_fallback():
    """_CANON에 없는 표기도 접미사 규칙으로 시/도 정규형이 된다.

    강원도→강원특별자치도, 전라북도→전북특별자치도처럼 개편이 실제로 있었으므로
    아직 _CANON에 없는 '…특별자치도' 표기가 들어와도 정규형이 나와야 한다.
    """
    assert normalize_region("전남특별자치도") == "전남"
    assert normalize_region("대구 광역시") == "대구"


# ── 예외맵: 시 단위 유지(성남) ──
def test_normalize_region_city_alias_keeps_city_granularity():
    assert normalize_region("성남시") == "성남"
    assert normalize_region("성남") == "성남"
    assert normalize_region("경기도 성남시") == "성남"


def test_region_matches_city_inside_province():
    """소득본거지가 성남이면 '경기' 공고는 해당지역(포함 규칙)."""
    assert region_matches("경기", ["성남시"]) is True
    # 역방향은 성립하지 않는다 — 경기 거주자가 성남 공고의 해당지역은 아니다.
    assert region_matches("성남", ["경기"]) is False


# ── 정상: 매칭 ──
def test_region_matches_exact():
    assert region_matches("서울", ["서울", "경기"]) is True
    assert region_matches("서울특별시", ["서울"]) is True
    assert region_matches("경기", ["서울특별시", "경기도"]) is True


# ── 불일치 ──
def test_region_matches_no_overlap():
    assert region_matches("부산", ["서울"]) is False
    assert region_matches("경기", ["서울", "인천"]) is False
    # 미등록 시(수원)는 예외맵에 없으므로 시/도로 승격되지 않는다 — 안전하게 불일치.
    assert region_matches("경기", ["수원시"]) is False


# ── 경계: 빈 입력 / None ──
@pytest.mark.parametrize("empty", [None, "", "   ", 123])
def test_normalize_region_empty_returns_blank(empty):
    assert normalize_region(empty) == ""


@pytest.mark.parametrize(
    ("area", "regions"),
    [
        ("", []),
        ("", ["서울"]),
        (None, ["서울"]),
        ("서울", []),
        ("서울", None),
        ("서울", ["", "   ", None]),
    ],
)
def test_region_matches_empty_is_false(area, regions):
    assert region_matches(area, regions) is False
