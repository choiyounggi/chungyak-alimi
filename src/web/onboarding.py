"""가입 직후 3스텝 온보딩 라우터(Task 05).

`app.py` 를 더 키우지 않으려고 `auth.py` 와 같은 `APIRouter` 분리 패턴을 쓴다(O2).

설계 요약
- **스텝별 신뢰 경계 검증**: 각 스텝은 자기 필드만 담은 pydantic 모델로 진입점에서 한 번
  검증하고, 통과한 값만 `members.update_profile` 로 넘긴다. 저장형 정규화(날짜 접기,
  허용 외 전형 제거, 파트너 자르기)는 `update_profile` 이 이미 하므로 여기서 다시 하지
  않는다 — 여기 검증은 **사용자에게 오류를 보여주기 위한 것**이다.
- **부분 저장**(O4): 스텝 POST 는 그 스텝 필드만 저장하고 `onboarding_step` 을
  `max(현재, 방금 완료한 step)` 으로만 올린다. 되돌아가 다시 제출해도 진행상태는 내려가지 않는다.
- **오류 표현**: 컨텍스트 키는 `values`(입력값 되살리기) / `errors`(필드 → 문구 한 줄).
  `auth.py` 의 `dict[str, list[str]]` 이 아니라 `profile.html` 의 `dict[str, str]` 관례를
  따르는데, `field()` 매크로를 `profile.html` 과 공유하며 그 렌더 결과를 바꾸지 않기 위해서다.
- **회원 식별자**는 언제나 세션에서만 온다(D14). 폼에 `member_id` 가 실려와도
  `extra="ignore"` 로 버린다.

반복 입력 필드의 와이어 포맷(서버 파싱 규칙)
- 거주이력: `residence_region` 과 `residence_since` 를 **같은 이름으로 반복** 전송하고
  **위치로 짝**짓는다(`getlist` 순서 = 브라우저 제출 순서). 화면은 늘 10행을 보내고,
  지역이 빈 행은 여기서 버린다(날짜만 있는 행도 의미가 없으므로 함께 버린다).
  손으로 만든 POST 가 두 목록 길이를 어긋나게 보내도 `zip_longest` 로 빈 값을 채운다.
  10개를 넘겨도 **자르지 않는다** — 모델의 `max_length` 로 400 을 내야 하기 때문이다.
- 선호전형: 체크된 것만 `preferred_types` 로 반복 전송된다.
- 파트너: 인덱스 네이밍 `partner_0_*` / `partner_1_*`. `label` 은 폼에서 받지 않고
  서버가 `PARTNER_LABELS` 로 정한다. 예비신혼을 고르지 않았으면 저장값은 `[]` 다.
"""
from __future__ import annotations

from datetime import date
from itertools import zip_longest
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from ..db import SessionLocal
from ..members import MAX_PARTNERS, get_profile, update_profile
from ..scoring import PREFERRED_TYPES, newlywed_period_exceeded
from .auth import require_login
from .forms import (
    _Count,
    _field_error,
    _OptCount,
    _OptDate,
    _REGION_FIELDS,
    _Regions,
)

router = APIRouter()

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# 거주이력 상한(D2 화면 계약) — 넘으면 자르지 않고 400 으로 돌려준다.
MAX_RESIDENCE_ROWS = 10
# 파트너 라벨은 서버가 정한다 — 자유 텍스트로 받을 이유가 없다.
PARTNER_LABELS: tuple[str, ...] = ("본인", "상대방")
# 선호 전형 체크박스(값은 scoring.PREFERRED_TYPES 와 같은 5종, 순서만 화면용).
PREFERRED_TYPE_LABELS: tuple[tuple[str, str], ...] = (
    ("newlywed", "신혼부부"),
    ("pre_newlywed", "예비신혼부부"),
    ("youth", "청년"),
    ("special", "특별공급"),
    ("general", "일반"),
)

_STEP_TITLES = {1: "기본·세대", 2: "자산·통장·소득", 3: "지역·선호·파트너"}
_NEXT_LOCATION = {1: "/onboarding/2", 2: "/onboarding/3", 3: "/"}
# 필드에 붙일 수 없는 오류를 담는 자리(폼 상단 배너). 필드명과 겹치지 않는 이름을 쓴다.
_FORM_LEVEL = "__form__"


class OnboardingStep1(BaseModel):
    """스텝 1 — 기본·세대. 나이(생년월일)와 세대 구성을 받는다."""

    model_config = ConfigDict(extra="ignore")

    birth_date: _OptDate = None
    household_type: Literal["general", "newlywed", "pre_newlywed", "youth"] = "general"
    marriage_date: _OptDate = None
    is_household_head: bool = False
    household_all_homeless: bool = False
    dependents: _Count = 0
    children_minor: _Count = 0

    @model_validator(mode="after")
    def _check_newlywed_period(self):
        """R4 — 신혼부부를 고르면 혼인신고일이 필수이고 인정기간을 넘길 수 없다.

        7년과 연수 계산은 `scoring` 이 정본이다(`newlywed_period_exceeded`). 폼과 특공
        판정이 서로 다른 답을 내지 않도록 여기서 규칙을 다시 정의하지 않는다.
        """
        if self.household_type != "newlywed":
            return self
        if self.marriage_date is None:
            raise PydanticCustomError("marriage_required", "혼인신고일이 필요합니다")
        if newlywed_period_exceeded(self.marriage_date):
            raise PydanticCustomError("marriage_expired", "혼인 인정기간을 넘겼습니다")
        return self


class OnboardingStep2(BaseModel):
    """스텝 2 — 자산·청약통장·소득.

    `car_value_manwon` 은 `owns_car` 체크 여부와 무관하게 항상 검증한다. 조건부 표시는
    화면(JS)의 일이고 서버는 늘 전체를 본다. 차량가액은 수집·표시만 하고 순위 판정에는
    쓰지 않는다(R6).
    """

    model_config = ConfigDict(extra="ignore")

    owns_car: bool = False
    car_value_manwon: _Count = 0
    real_estate_manwon: _Count = 0
    household_head_owns_home: bool = False
    fl_ever_owned_house: bool = False
    account_opened: _OptDate = None
    account_payment_count: _Count = 0
    account_balance_manwon: _Count = 0
    income_monthly_manwon: _OptCount = None
    income_base_manwon: _OptCount = None
    income_dual: bool = False


class ResidenceRow(BaseModel):
    """지역별 거주 시작일 한 행. 기간(년수)은 저장하지 않는다 — 조회 시 계산한다(D2)."""

    model_config = ConfigDict(extra="ignore")

    region: str = Field(min_length=1, max_length=50)
    since: _OptDate = None

    @field_validator("since")
    @classmethod
    def _not_in_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("거주 시작일은 오늘 이전이어야 합니다")
        return v


class PartnerRow(BaseModel):
    """예비신혼 상대방 한 명(D5). `label` 은 서버가 채운다."""

    model_config = ConfigDict(extra="ignore")

    label: str = Field("", max_length=20)
    lives_with_parents: bool = False
    owns_home: bool = False
    residence_region: str = Field("", max_length=50)
    income_base_region: str = Field("", max_length=50)


class OnboardingStep3(BaseModel):
    """스텝 3 — 지역별 거주기간·선호전형(복수)·예비신혼 파트너."""

    model_config = ConfigDict(extra="ignore")

    residence_history: list[ResidenceRow] = Field(
        default_factory=list, max_length=MAX_RESIDENCE_ROWS
    )
    income_base_regions: _Regions = []
    interest_regions: _Regions = []
    preferred_types: list[str] = Field(default_factory=list, max_length=len(PREFERRED_TYPES))
    partners: list[PartnerRow] = Field(default_factory=list, max_length=MAX_PARTNERS)

    @field_validator("preferred_types")
    @classmethod
    def _allowlist(cls, v: list[str]) -> list[str]:
        """허용값(scoring.PREFERRED_TYPES) 밖은 거부한다 — 걸러내지 않고 알려준다."""
        unknown = [t for t in v if t not in PREFERRED_TYPES]
        if unknown:
            raise ValueError("허용되지 않은 선호 전형입니다")
        return v

    @model_validator(mode="after")
    def _pre_newlywed_needs_both_partners(self):
        """예비신혼을 고르면 두 사람의 거주지가 모두 있어야 한다. 안 골랐으면 저장하지 않는다."""
        if "pre_newlywed" not in self.preferred_types:
            self.partners = []
            return self
        filled = [p for p in self.partners if p.residence_region.strip()]
        if len(filled) != MAX_PARTNERS:
            raise PydanticCustomError("partners_required", "두 사람의 거주지가 필요합니다")
        return self


_STEP_MODELS: dict[int, type[BaseModel]] = {
    1: OnboardingStep1,
    2: OnboardingStep2,
    3: OnboardingStep3,
}

# `model_validator(mode="after")` 오류는 loc 가 비어 있어 필드를 알 수 없다.
# PydanticCustomError 의 type 을 키로 삼아 어느 필드 슬롯에 붙일지 되돌린다.
_CROSS_FIELD_TARGET: dict[str, str] = {
    "marriage_required": "marriage_date",
    "marriage_expired": "marriage_date",
    "partners_required": "partners",
}
# 한 필드에 사유가 둘 이상인 경우(marriage_date)만 type 별 문구를 따로 둔다.
# 나머지는 `_field_error(field)` 가 유일한 출처다.
_CROSS_FIELD_MESSAGE: dict[str, str] = {
    "marriage_required": "신혼부부를 선택하면 혼인신고일을 입력해주세요",
    "marriage_expired": "혼인신고일로부터 7년 이내여야 신혼부부로 신청할 수 있습니다",
}


def _collect_errors(exc: ValidationError) -> dict[str, str]:
    """ValidationError → {필드: 문구}. 실패한 필드를 **모두** 한 번에 돌려준다."""
    errors: dict[str, str] = {}
    for err in exc.errors():
        etype = str(err.get("type", ""))
        if etype in _CROSS_FIELD_TARGET:
            field = _CROSS_FIELD_TARGET[etype]
            errors.setdefault(field, _CROSS_FIELD_MESSAGE.get(etype) or _field_error(field))
        elif err["loc"]:
            # 중첩 loc(residence_history, 0, "since")도 그룹 필드 슬롯 하나로 모은다.
            field = str(err["loc"][0])
            errors.setdefault(field, _field_error(field))
        else:
            errors.setdefault(_FORM_LEVEL, "입력값을 확인해주세요")
    return errors


def _scalar_values(prof, step: int) -> dict:
    """DB 행 → 그 스텝의 스칼라 폼 값(날짜/숫자는 문자열, 지역은 콤마, 체크박스는 bool)."""
    if prof is None:
        return {"household_type": "general"}
    values: dict = {}
    for name in _STEP_MODELS[step].model_fields:
        if name in ("residence_history", "preferred_types", "partners"):
            continue
        v = getattr(prof, name)
        if name in _REGION_FIELDS:
            values[name] = ", ".join(v or [])
        elif isinstance(v, bool):  # bool 은 int 의 하위형이라 숫자보다 먼저 본다
            values[name] = v
        elif v is None:
            values[name] = ""
        elif isinstance(v, date):
            values[name] = v.isoformat()
        else:
            values[name] = str(v)
    return values


def _blank_partner(index: int) -> dict:
    return {
        "label": PARTNER_LABELS[index],
        "lives_with_parents": False,
        "owns_home": False,
        "residence_region": "",
        "income_base_region": "",
    }


def _step3_payload_from_profile(prof) -> dict:
    """DB 행 → 스텝 3 폼 모양(pydantic 입력이자 템플릿 표시값)."""
    if prof is None:
        return {
            "residence_history": [],
            "income_base_regions": "",
            "interest_regions": "",
            "preferred_types": [],
            "partners": [],
        }
    rows = [
        {"region": str(h.get("region") or ""), "since": h.get("since") or ""}
        for h in (prof.residence_history or [])
        if isinstance(h, dict)
    ]
    partners = []
    for i, p in enumerate(prof.partners or []):
        if i >= MAX_PARTNERS or not isinstance(p, dict):
            continue
        row = _blank_partner(i)  # label 은 늘 서버 값으로 덮는다
        row["lives_with_parents"] = bool(p.get("lives_with_parents"))
        row["owns_home"] = bool(p.get("owns_home"))
        row["residence_region"] = str(p.get("residence_region") or "")
        row["income_base_region"] = str(p.get("income_base_region") or "")
        partners.append(row)
    return {
        "residence_history": rows,
        "income_base_regions": ", ".join(prof.income_base_regions or []),
        "interest_regions": ", ".join(prof.interest_regions or []),
        "preferred_types": [str(t) for t in (prof.preferred_types or [])],
        "partners": partners,
    }


def _text(value) -> str:
    """폼 값 → 문자열. 문자열이 아닌 것(멀티파트 업로드 등)은 빈 값으로 닫는다.

    `str(value)` 로 억지 변환하지 않는다 — 신뢰 경계에서는 모양이 다르면 받지 않는 쪽이 맞다.
    """
    return value.strip() if isinstance(value, str) else ""


def _step3_payload_from_form(form) -> dict:
    """폼 → 스텝 3 폼 모양. 반복 필드 파싱 규칙은 모듈 docstring 참고."""
    rows = []
    for region, since in zip_longest(
        form.getlist("residence_region"), form.getlist("residence_since"), fillvalue=""
    ):
        if not _text(region):  # 빈 행(그리고 지역 없이 날짜만 있는 행)은 버린다
            continue
        rows.append({"region": _text(region), "since": _text(since)})
    partners = [
        {
            "label": label,
            "lives_with_parents": bool(form.get(f"partner_{i}_lives_with_parents")),
            "owns_home": bool(form.get(f"partner_{i}_owns_home")),
            "residence_region": _text(form.get(f"partner_{i}_residence_region")),
            "income_base_region": _text(form.get(f"partner_{i}_income_base_region")),
        }
        for i, label in enumerate(PARTNER_LABELS)
    ]
    return {
        "residence_history": rows,
        "income_base_regions": _text(form.get("income_base_regions")),
        "interest_regions": _text(form.get("interest_regions")),
        "preferred_types": [t for t in form.getlist("preferred_types") if isinstance(t, str)],
        "partners": partners,
    }


def _context(step: int, reached: int, values: dict, errors: dict) -> dict:
    ctx = {
        "step": step,
        "reached": reached,
        "values": values,
        "errors": errors,
        "form_error": errors.get(_FORM_LEVEL),
        "step_title": _STEP_TITLES[step],
    }
    if step == 3:
        partners = list(values.get("partners") or [])
        partners += [_blank_partner(i) for i in range(len(partners), MAX_PARTNERS)]
        ctx |= {
            "rows": values.get("residence_history") or [],
            "preferred": values.get("preferred_types") or [],
            "partner_rows": partners[:MAX_PARTNERS],
            "preferred_type_labels": PREFERRED_TYPE_LABELS,
            "max_rows": MAX_RESIDENCE_ROWS,
        }
    return ctx


def _require_step(step: int) -> None:
    if step not in _STEP_MODELS:
        raise HTTPException(status_code=404, detail="온보딩 단계를 찾을 수 없습니다")


@router.get("/onboarding/{step}")
def onboarding_page(step: int, request: Request, member_id: int = Depends(require_login)):
    _require_step(step)
    with SessionLocal() as session:
        prof = get_profile(member_id, session=session)
        values = (
            _step3_payload_from_profile(prof) if step == 3 else _scalar_values(prof, step)
        )
        done = prof.onboarding_step if prof is not None else 0
    return _TEMPLATES.TemplateResponse(
        request, f"onboarding_{step}.html", _context(step, max(done, step), values, {})
    )


@router.post("/onboarding/{step}")
async def onboarding_submit(step: int, request: Request, member_id: int = Depends(require_login)):
    _require_step(step)
    form = await request.form()
    values = _step3_payload_from_form(form) if step == 3 else dict(form)
    try:
        data = _STEP_MODELS[step].model_validate(values)
    except ValidationError as exc:
        # 실패한 필드를 모두 되돌려 같은 슬롯에 인라인 표시하고, 입력값은 그대로 되살린다.
        with SessionLocal() as session:
            prof = get_profile(member_id, session=session)
            done = prof.onboarding_step if prof is not None else 0
        return _TEMPLATES.TemplateResponse(
            request,
            f"onboarding_{step}.html",
            _context(step, max(done, step), values, _collect_errors(exc)),
            status_code=400,
        )

    saved = data.model_dump()
    with SessionLocal() as session:
        prof = get_profile(member_id, session=session)
        done = prof.onboarding_step if prof is not None else 0
        # 되돌아가 다시 제출해도 진행상태는 내려가지 않는다(O4).
        saved["onboarding_step"] = max(done, step)
        update_profile(member_id, saved, session=session)
    return RedirectResponse(_NEXT_LOCATION[step], status_code=303)
