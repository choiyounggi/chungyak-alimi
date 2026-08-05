"""회원/프로필 데이터액세스 + MemberProfile → scoring.Profile 어댑터(Task 02).

순위 로직(Task 09)·대시보드(Task 11)는 profile_from_member 로 회원 프로필을 scoring 에 넘긴다.
어댑터는 기존 scoring 함수 호환용 — 순위-지역(residence/income_base) 등 신규 필드는
Profile 에 없으므로 순위 로직이 MemberProfile 행에서 직접 읽는다.
"""
from __future__ import annotations

from datetime import date

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Member, MemberProfile
from .scoring import (
    PREFERRED_TYPES,
    AccountInfo,
    FirstLifeInfo,
    IncomeInfo,
    PartnerInfo,
    Profile,
    ResidencePeriod,
)

# argon2-cffi 기본 파라미터(m=65536, t=3, p=4)는 OWASP 최소치(m=19456, t=2, p=1) 이상.
_ph = PasswordHasher()

# 비밀번호 길이 상한 — 메모리 하드 함수에 무제한 입력은 그 자체로 DoS.
MAX_PASSWORD_LEN = 128

# update_profile 로 갱신 허용하는 컬럼(화이트리스트). member_id/PK 는 제외.
_UPDATABLE: frozenset[str] = frozenset(
    {
        "birth_date", "marriage_date", "engaged", "is_household_head",
        "household_all_homeless", "homeless_since", "dependents", "won_within_5y",
        "children_minor", "real_estate_manwon", "region",
        "account_opened", "account_balance_manwon",
        "income_monthly_manwon", "income_base_manwon", "income_dual",
        "fl_ever_owned_house", "fl_income_tax_5y", "fl_currently_earning",
        "car_value_manwon", "household_head_owns_home", "household_type",
        "is_first_home", "residence_regions", "income_base_regions", "interest_regions",
        "owns_car", "account_payment_count", "residence_history",
        "preferred_types", "partners", "onboarding_step",
    }
)
_LIST_FIELDS: frozenset[str] = frozenset(
    {"residence_regions", "income_base_regions", "interest_regions"}
)

# 예비신혼 파트너 상한(D5) — 쓰기 경계에서 자른다. 두 사람을 넘는 '예비신혼'은 없다.
MAX_PARTNERS = 2


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def hash_password(password: str) -> str:
    """평문 → argon2id 해시(salt·파라미터는 해시 문자열에 포함). 128자 초과면 ValueError."""
    if len(password) > MAX_PASSWORD_LEN:
        raise ValueError(f"비밀번호는 {MAX_PASSWORD_LEN}자 이하여야 합니다")
    return _ph.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _ph.verify(hash_, password)
    except VerifyMismatchError:
        return False


# 계정이 없을 때도 같은 비용의 검증을 수행해, 응답 시간으로 이메일 존재 여부가 새지 않게 한다.
_DUMMY_HASH = _ph.hash("chungyak-alimi-dummy-password")


def authenticate_member(email: str, password: str, *, session: Session) -> Member | None:
    """자격증명이 맞으면 Member, 아니면 None. 계정 부재도 더미 검증으로 타이밍을 은닉."""
    m = get_member_by_email(email, session=session)
    if m is None:
        verify_password(_DUMMY_HASH, password)
        return None
    return m if verify_password(m.password_hash, password) else None


def get_member_by_email(email: str, *, session: Session) -> Member | None:
    return session.scalar(select(Member).where(Member.email == _norm_email(email)))


def create_member(email: str, password_hash: str, *, session: Session) -> Member:
    """회원 생성(email 소문자 저장) + 빈 MemberProfile 동반 생성. 중복이면 ValueError."""
    norm = _norm_email(email)
    if get_member_by_email(norm, session=session) is not None:
        raise ValueError(f"이미 존재하는 이메일: {norm}")
    m = Member(email=norm, password_hash=password_hash)
    session.add(m)
    session.flush()  # id 확보
    session.add(MemberProfile(member_id=m.id))
    session.commit()
    return m


def get_profile(member_id: int, *, session: Session) -> MemberProfile | None:
    return session.get(MemberProfile, member_id)


def _iso_date_or_none(value) -> str | None:
    """date/ISO 문자열 → 'YYYY-MM-DD', 그 외(빈값·형식 오류)는 None.

    JSONB 는 date 객체를 직렬화하지 못한다. 파싱 불가한 값을 저장 시점에 None 으로
    떨궈, 나중에 Profile 로 변환할 때 프로필 조회가 통째로 터지지 않게 한다.
    """
    if isinstance(value, date):
        return value.isoformat()[:10]
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _norm_residence_history(value) -> list[dict]:
    """저장형 [{"region": str, "since": "YYYY-MM-DD"|None}] 로 정규화.

    region 이 빈 항목도 그대로 남긴다 — 걸러내는 건 residence_regions 파생 시점이다(D3).
    """
    out = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "region": str(item.get("region") or ""),
                "since": _iso_date_or_none(item.get("since")),
            }
        )
    return out


def _norm_partners(value) -> list[dict]:
    """저장형 파트너 dict 로 정규화하고 MAX_PARTNERS 개로 자른다(D5)."""
    out = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "label": str(item.get("label") or ""),
                "lives_with_parents": bool(item.get("lives_with_parents")),
                "owns_home": bool(item.get("owns_home")),
                "residence_region": str(item.get("residence_region") or ""),
                "income_base_region": str(item.get("income_base_region") or ""),
            }
        )
    return out[:MAX_PARTNERS]


def update_profile(member_id: int, values: dict, *, session: Session) -> MemberProfile:
    """허용 컬럼만 반영. 지역 목록 필드는 list[str] 로 강제. 프로필 없으면 ValueError.

    JSONB 확장 필드(residence_history/preferred_types/partners)는 저장형으로 정규화한다 —
    date 객체는 JSONB 로 직렬화되지 않고, 허용 외 전형·dict 아닌 원소는 여기서 걸러야
    이후 Profile 변환이 터지지 않는다.

    residence_history 가 오면 residence_regions 를 그 지역 목록으로 **덮어쓴다**(D3).
    같은 요청이 residence_regions 를 함께 줘도 history 가 이긴다 — 한 개념의 정본을
    하나로 못박아야 두 값이 갈라지지 않는다. history 가 없으면 residence_regions 를
    직접 반영하는 기존 동작 그대로다(기존 프로필 폼 경로).
    """
    prof = session.get(MemberProfile, member_id)
    if prof is None:
        raise ValueError(f"프로필 없음: member_id={member_id}")
    for key, value in values.items():
        if key not in _UPDATABLE:
            continue
        if key in _LIST_FIELDS:
            value = [str(x) for x in (value or [])]
        elif key == "residence_history":
            value = _norm_residence_history(value)
        elif key == "preferred_types":
            value = [t for t in (value or []) if t in PREFERRED_TYPES]
        elif key == "partners":
            value = _norm_partners(value)
        setattr(prof, key, value)
    if "residence_history" in values:
        prof.residence_regions = [h["region"] for h in prof.residence_history if h["region"]]
    session.commit()
    return prof


def profile_from_member(row: MemberProfile) -> Profile:
    """MemberProfile 행 → scoring.Profile(중첩 AccountInfo/IncomeInfo/FirstLifeInfo 포함).

    JSONB 원소 중 dict 가 아닌 값은 버린다 — DB 도 프로세스 밖이라 필드 접근 전에 형태를 본다.
    """
    return Profile(
        birth_date=row.birth_date,
        marriage_date=row.marriage_date,
        engaged=row.engaged,
        is_household_head=row.is_household_head,
        household_all_homeless=row.household_all_homeless,
        homeless_since=row.homeless_since,
        dependents=row.dependents,
        region=row.region,
        won_within_5y=row.won_within_5y,
        children_minor=row.children_minor,
        real_estate_manwon=row.real_estate_manwon,
        account=AccountInfo(
            opened=row.account_opened,
            balance_manwon=row.account_balance_manwon,
        ),
        income=IncomeInfo(
            monthly_manwon=row.income_monthly_manwon,
            base_manwon=row.income_base_manwon,
            dual_income=row.income_dual,
        ),
        first_life=FirstLifeInfo(
            ever_owned_house=row.fl_ever_owned_house,
            income_tax_5y=row.fl_income_tax_5y,
            currently_earning=row.fl_currently_earning,
        ),
        owns_car=row.owns_car,
        account_payment_count=row.account_payment_count,
        residence_history=[
            ResidencePeriod(**h) for h in (row.residence_history or []) if isinstance(h, dict)
        ],
        preferred_types=[str(t) for t in (row.preferred_types or [])],
        partners=[PartnerInfo(**p) for p in (row.partners or []) if isinstance(p, dict)],
    )
