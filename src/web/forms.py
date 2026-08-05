"""폼 필드 애너테이션·헬퍼 — `/profile` 폼과 온보딩 3스텝 폼이 공유한다(O6).

`app.py` 에 있던 것을 그대로 옮겨온 것이라 이름·동작이 같다. `ProfileForm` 과
`GET/POST /profile` 라우트 자체는 `app.py` 에 남아 있고, 여기에는 **재사용되는 조각만** 둔다.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BeforeValidator, Field

# 세대유형 — DB CHECK 제약(ck_member_profile_household_type)과 같은 닫힌 4값(D7).
HOUSEHOLD_TYPES: tuple[tuple[str, str], ...] = (
    ("general", "일반"),
    ("newlywed", "신혼부부"),
    ("pre_newlywed", "예비신혼부부"),
    ("youth", "청년"),
)
# 두 사람의 거주지·소득본거지를 함께 입력받아야 하는 세대유형
COUPLE_HOUSEHOLD_TYPES: tuple[str, ...] = ("newlywed", "pre_newlywed")

_DATE_FIELDS = frozenset({"birth_date", "marriage_date", "homeless_since", "account_opened"})
_NUMBER_FIELDS = frozenset(
    {
        "dependents", "children_minor", "real_estate_manwon", "account_balance_manwon",
        "income_monthly_manwon", "income_base_manwon", "car_value_manwon",
        "account_payment_count",
    }
)
_REGION_FIELDS = frozenset({"residence_regions", "income_base_regions", "interest_regions"})


def _blank_to(default):
    """빈 문자열(미입력)을 그 필드의 '값 없음'으로 접는다 — 폼은 미입력도 ''로 보낸다."""

    def convert(v):
        return default if isinstance(v, str) and not v.strip() else v

    return convert


def _split_regions(v):
    """콤마 구분 문자열 → list[str](공백 트림, 빈 항목 제거). 빈 입력은 []."""
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return v


_OptDate = Annotated[date | None, BeforeValidator(_blank_to(None))]
# ge 는 Optional 의 **안쪽 int** 에 붙여야 한다. Annotated[int | None, Field(ge=0)] 로 두면
# 제약이 유니온 전체에 걸려 None 이 들어온 순간 `None >= 0` 으로 TypeError 가 난다.
# 상한은 저장 컬럼(INTEGER)의 최대치 — 이걸 넘기면 검증을 통과하고도 INSERT 가 터진다.
_INT_MAX = 2_147_483_647
_NonNegInt = Annotated[int, Field(ge=0, le=_INT_MAX)]
_Count = Annotated[_NonNegInt, BeforeValidator(_blank_to(0))]
_OptCount = Annotated[_NonNegInt | None, BeforeValidator(_blank_to(None))]
_Regions = Annotated[
    list[Annotated[str, Field(max_length=50)]],
    BeforeValidator(_split_regions),
    Field(max_length=20),
]

# 온보딩 스텝 3의 그룹 필드(반복 행·체크박스 그룹) 오류 문구. 교차 필드 오류
# (신혼 7년 등)는 필드명이 아니라 pydantic 오류 type 으로 매핑하므로 onboarding.py 가 쥔다.
_GROUP_FIELD_MESSAGES: dict[str, str] = {
    "residence_history": "거주지를 입력하고 거주 시작일은 오늘 이전 날짜로, 최대 10개까지 넣어주세요",
    "preferred_types": "선호 전형을 목록에서 선택해주세요",
    "partners": "예비신혼부부를 선택하면 두 사람의 거주지를 모두 입력해주세요",
}


def _field_error(field: str) -> str:
    """필드별 오류 문구 — '무엇이 틀렸나'가 아니라 '무엇을 하면 되나'를 적는다."""
    if field in _DATE_FIELDS:
        return "날짜를 YYYY-MM-DD 형식으로 입력해주세요"
    if field in _NUMBER_FIELDS:
        return f"0 이상의 숫자를 입력해주세요(최대 {_INT_MAX:,})"
    if field == "household_type":
        return "세대유형을 목록에서 선택해주세요"
    if field in _REGION_FIELDS:
        return "지역은 콤마로 구분해 20개까지, 각 50자 이내로 입력해주세요"
    if field in _GROUP_FIELD_MESSAGES:
        return _GROUP_FIELD_MESSAGES[field]
    return "입력값을 확인해주세요"
