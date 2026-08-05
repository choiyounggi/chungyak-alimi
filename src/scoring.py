from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

from .regions import normalize_region, region_matches

# ── 규칙 상수 (민영주택 기준, 2026-07 확인 — 제도 변경 시 여기만 수정) ──
# 소득초과자 추첨제 부동산가액 상한(만원): 국토부 2021.11 개편(3억3,100만원)
LOTTERY_ASSET_CAP_MANWON = 33_100
# 신혼부부 특공 소득 상한(% of 도시근로자 월평균소득): (외벌이, 맞벌이)
NEWLYWED_PRIORITY_PCT = (100, 120)  # 우선공급
NEWLYWED_GENERAL_PCT = (140, 160)  # 일반공급
# 신혼부부 인정기간(년) — 폼 검증(R4)과 judge_newlywed 이 같은 값을 본다
NEWLYWED_MAX_YEARS = 7
# 생애최초 특공 소득 상한(%)
FIRSTLIFE_PRIORITY_PCT = 130
FIRSTLIFE_GENERAL_PCT = 160
# 민영 예치금(만원): 거주지역군 → [(전용면적 상한㎡, 예치금), ...] (None=모든 면적)
DEPOSIT_TABLE = {
    "서울부산": [(85, 300), (102, 600), (135, 1000), (None, 1500)],
    "기타광역시": [(85, 250), (102, 400), (135, 700), (None, 1000)],
    "기타시군": [(85, 200), (102, 300), (135, 400), (None, 500)],
}
_GWANGYEOK = ("대구", "인천", "광주", "대전", "울산")
_CAPITAL = ("서울", "경기", "인천")

# ── 규칙 상수 (국민주택/공공, 주택공급에 관한 규칙 제27조·제28조 — 2026-08 확인) ──
# 지역군별 청약통장 가입기간(개월) 하한. regulated=투기과열지구·조정대상지역,
# capital=수도권(_CAPITAL), other=그 외.
PUBLIC_ACCOUNT_MONTHS = {"regulated": 24, "capital": 12, "other": 6}
# 지역군별 납입횟수(회) 하한 — 납입횟수가 국민주택 순위에 반영되는 지점이다.
PUBLIC_PAYMENT_COUNTS = {"regulated": 24, "capital": 12, "other": 6}
# 규제지역 해당지역 우선공급 거주기간(년). 미달은 사유에만 표기하고 순위를 낮추지 않는다(R3).
REGULATED_RESIDENCE_YEARS = 2.0
# 국민주택(공공) 공고를 내는 수집원. house_dtl_secd_nm 에 "국민"이 없어도 이 소스면 국민으로 본다.
PUBLIC_SOURCES = ("lh", "myhome", "sh", "gh")


class AccountInfo(BaseModel):
    opened: date | None = None  # 청약통장 가입일
    balance_manwon: int = 0  # 예치금(만원)


class IncomeInfo(BaseModel):
    monthly_manwon: int | None = None  # 세전 가구 월평균소득(만원)
    base_manwon: int | None = None  # 전년도 도시근로자 가구원수별 월평균소득 100%(모집공고 확인)
    dual_income: bool = False  # 맞벌이


class FirstLifeInfo(BaseModel):
    ever_owned_house: bool = False  # 세대구성원 과거 포함 주택 소유 이력
    income_tax_5y: bool = False  # 소득세 납부 5년 이상
    currently_earning: bool = False  # 현재 근로/사업소득


# 선호 전형(복수선택) 허용값 — 쓰기 경계(members.update_profile)에서 이 집합으로 거른다(D4).
PREFERRED_TYPES: frozenset[str] = frozenset(
    {"newlywed", "pre_newlywed", "youth", "special", "general"}
)


def _drop_none(data):
    """JSONB 에 남은 None 을 지워 필드 기본값이 적용되게 한다.

    부분 입력·구버전 행 하나가 ValidationError 를 내면 프로필 조회 전체가 죽는다.
    호출부마다 sanitize 하는 대신 모델 자체를 넓혀 관용을 한 곳에 모은다.
    """
    return {k: v for k, v in data.items() if v is not None} if isinstance(data, dict) else data


class ResidencePeriod(BaseModel):
    """지역별 거주 시작일. 기간(년수)은 저장하지 않고 조회 시 today - since 로 계산한다(D2)."""

    region: str = ""
    since: date | None = None

    @model_validator(mode="before")
    @classmethod
    def _fold_none(cls, data):
        return _drop_none(data)


class PartnerInfo(BaseModel):
    """예비신혼 상대방. 예비신혼은 아직 한 세대가 아니라 세대 필드와 따로 본다(R5)."""

    label: str = ""
    lives_with_parents: bool = False
    owns_home: bool = False
    residence_region: str = ""
    income_base_region: str = ""

    @model_validator(mode="before")
    @classmethod
    def _fold_none(cls, data):
        return _drop_none(data)


class Profile(BaseModel):
    birth_date: date | None = None
    marriage_date: date | None = None  # 혼인신고일(미혼 null)
    engaged: bool = False  # 예비신혼부부(입주 전 혼인신고 예정 — 신혼 특공 신청 가능)
    is_household_head: bool = False  # 세대주
    household_all_homeless: bool = True  # 세대구성원 전원 무주택
    homeless_since: date | None = None  # 마지막 주택 처분일(계속 무주택이면 null)
    dependents: int = 0  # 부양가족 수(본인 제외)
    region: str = ""  # 거주 시/도 (예치금·해당지역 판정)
    won_within_5y: bool = False  # 5년 내 세대구성원 당첨 이력
    children_minor: int = 0  # 미성년 자녀 수
    account: AccountInfo = AccountInfo()
    income: IncomeInfo = IncomeInfo()
    real_estate_manwon: int = 0  # 세대 부동산가액(만원, 추첨제 자산기준)
    first_life: FirstLifeInfo = FirstLifeInfo()
    # 온보딩 확장(signup-hardening) — 판정 함수는 Task 03 이 소비한다
    owns_car: bool = False  # 자차 보유(공공 특공 자산요건 판정 입력)
    account_payment_count: int = 0  # 청약통장 납입횟수(국민주택 순위 요건)
    residence_history: list[ResidencePeriod] = []  # 지역별 거주 시작일
    preferred_types: list[str] = []  # 선호 전형(복수) — 값은 PREFERRED_TYPES
    partners: list[PartnerInfo] = []  # 예비신혼 상대방(최대 2)


def load_profile(path: str = "config/profile.yaml") -> Profile | None:
    """프로필 파일이 없으면 None(판정 기능 비활성) — 알림/웹은 기존 그대로 동작."""
    p = Path(path)
    if not p.exists():
        return None
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Profile(**data)


def _full_years(start: date, end: date) -> float:
    return max(0.0, (end - start).days / 365.25)


def newlywed_period_exceeded(marriage_date: date | None, today: date | None = None) -> bool:
    """혼인 인정기간(7년) 초과 여부. judge_newlywed 과 같은 계산을 쓴다 —
    폼 검증과 특공 판정이 서로 다른 답을 내면 안 된다(R4)."""
    if marriage_date is None:
        return False
    return _full_years(marriage_date, today or date.today()) > NEWLYWED_MAX_YEARS


def homeless_years(p: Profile, today: date) -> float:
    """무주택기간(년): 만30세부터, 단 30세 이전 혼인 시 혼인신고일부터.
    주택 처분 이력이 있으면 처분일 이후부터."""
    if p.birth_date is None:
        return 0.0
    age30 = date(p.birth_date.year + 30, p.birth_date.month, min(p.birth_date.day, 28))
    start = age30
    if p.marriage_date and p.marriage_date < age30:
        start = p.marriage_date
    if p.homeless_since and p.homeless_since > start:
        start = p.homeless_since
    return _full_years(start, today)


def score_points(p: Profile, today: date | None = None) -> dict:
    """청약 가점(84점 만점): 무주택기간(32) + 부양가족(35) + 통장가입기간(17)."""
    today = today or date.today()

    hy = homeless_years(p, today)
    if not p.household_all_homeless or hy <= 0:
        homeless_pts = 0  # 유주택 세대 또는 무주택기간 미기산(만30세 미만 미혼)
    else:
        homeless_pts = min(32, 2 * (int(hy) + 1))

    dependents_pts = min(35, 5 * (p.dependents + 1))

    if p.account.opened is None:
        account_pts = 0
    else:
        ay = _full_years(p.account.opened, today)
        account_pts = 1 if ay < 0.5 else min(17, int(ay) + 2)

    return {
        "homeless": homeless_pts,
        "dependents": dependents_pts,
        "account": account_pts,
        "total": homeless_pts + dependents_pts + account_pts,
    }


def _deposit_group(region: str) -> str:
    if any(r in region for r in ("서울", "부산")):
        return "서울부산"
    if any(r in region for r in _GWANGYEOK):
        return "기타광역시"
    return "기타시군"


def _required_deposit(region: str, area_m2: float) -> int:
    for cap, amount in DEPOSIT_TABLE[_deposit_group(region)]:
        if cap is None or area_m2 <= cap:
            return amount
    return DEPOSIT_TABLE[_deposit_group(region)][-1][1]


def _is_regulated(raw: dict) -> bool:
    """투기과열지구 또는 조정대상지역."""
    if raw.get("SPECLT_RDN_EARTH_AT") == "Y":
        return True
    return raw.get("MDAT_TRGET_AREA_SECD") not in (None, "", "N")


def _full_months(start: date, end: date) -> int:
    """만 개월수(캘린더 기준).

    국민주택 요건이 '개월'로 규정돼 있어 `_full_years * 12`로 환산하지 않는다 —
    365일은 `365/365.25*12 = 11.99`가 되어 12개월 경계를 오판한다.
    """
    if end < start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def residence_years_in(p: Profile, region: str | None, today: date | None = None) -> float:
    """`region`에서의 거주 연수. 이력이 여러 건이면 **최대값**, 없으면 0.0.

    지역 비교는 `regions.normalize_region` 정규형으로 한다("서울특별시" == "서울").
    `since`가 없는 이력과 빈 지역은 계산에서 제외한다 — 예외를 던지지 않는다.
    """
    today = today or date.today()
    target = normalize_region(region)
    if not target:
        return 0.0
    years = [
        _full_years(h.since, today)
        for h in p.residence_history
        if h.since is not None and normalize_region(h.region) == target
    ]
    return max(years) if years else 0.0


def _residence_reasons(notice, p: Profile, today: date, regulated: bool) -> list[str]:
    """규제지역 공고의 해당지역 우선공급 거주기간 미달을 사유 목록으로 돌려준다(R3).

    정책 주의: 거주기간을 순위 요건이 아니라 **표기 사유**로만 다루는 것은 기존
    D19(지역은 1·2순위 요건이 아니다)와 같은 원칙의 *사용자 지정 정책*이며,
    공식 청약 규칙의 해당지역 우선공급 판정과는 다를 수 있다.
    """
    if not regulated:
        return []
    years = residence_years_in(p, notice.area_nm, today)
    if years >= REGULATED_RESIDENCE_YEARS:
        return []
    return [
        f"규제지역 해당지역 우선공급 거주기간 부족"
        f"({years:.1f}년<{REGULATED_RESIDENCE_YEARS:g}년)"
    ]


def _public_area_group(notice, regulated: bool) -> str:
    """국민주택 지역군: 규제 → regulated, 수도권 → capital, 그 외 → other."""
    if regulated:
        return "regulated"
    return "capital" if (notice.area_nm or "") in _CAPITAL else "other"


def judge_rank(
    notice,
    house_types,
    p: Profile,
    today: date | None = None,
    applicant_regions: list[str] | None = None,
) -> dict:
    """민영주택 1·2순위 판정. (통장 가입기간 + 예치금 + 규제지역 추가요건)

    `applicant_regions`는 호출측이 구성한 **거주지 ∪ 소득본거지** 목록이다.
    주어지면 공고 지역과 맞는지 판정해 `in_area`(True/False)를 함께 돌려주고,
    None(기본)이면 지역 판정을 하지 않아 `in_area`는 None이 된다(하위호환).

    정책(D19): 소득본거지를 해당지역으로 인정하는 것은 **사용자 지정 정책**이며
    공식 청약 규칙이 아니다(공식 규칙은 원칙적으로 거주지 기준).
    또한 지역은 1·2순위 요건이 아니므로 `in_area`는 `rank`에 영향을 주지 않는다.
    "해당지역 1순위"는 `rank == "1순위" and in_area`로 표현한다.

    정책(R3): 규제지역 공고면 해당지역 우선공급 거주기간(2년) 미달을 `reasons`에
    표기하지만 `rank`는 낮추지 않는다 — D19와 같은 원칙의 사용자 지정 정책이다.
    """
    today = today or date.today()
    raw = notice.raw or {}
    regulated = _is_regulated(raw)
    reasons: list[str] = []

    ay = _full_years(p.account.opened, today) if p.account.opened else 0.0
    need_years = 2.0 if regulated else (1.0 if (notice.area_nm or "") in _CAPITAL else 0.5)
    if ay < need_years:
        reasons.append(f"통장 가입기간 부족({ay:.1f}년<{need_years:g}년)")

    areas = [float(ht.suply_ar) for ht in house_types if ht.suply_ar is not None]
    if areas and p.region:
        need_min = _required_deposit(p.region, min(areas))
        need_max = _required_deposit(p.region, max(areas))
        if p.account.balance_manwon < need_min:
            reasons.append(f"예치금 부족({p.account.balance_manwon}<{need_min}만원)")
        elif p.account.balance_manwon < need_max:
            reasons.append(f"일부 큰 평형은 예치금 부족(최대 {need_max}만원 필요)")

    if regulated:
        if not p.is_household_head:
            reasons.append("규제지역: 세대주 아님")
        if p.won_within_5y:
            reasons.append("규제지역: 5년 내 당첨 이력")

    # 순위는 지역 사유가 붙기 전에 확정한다 — 기타지역이 2순위로 떨어뜨리면 안 된다.
    blocking = [r for r in reasons if not r.startswith("일부")]
    rank = "2순위" if blocking else "1순위"

    # R3: 거주기간은 사유로만 붙는다 — rank 확정 뒤에 붙여 blocking 계산에 들어가지 않게 한다.
    reasons += _residence_reasons(notice, p, today, regulated)

    in_area: bool | None = None
    if applicant_regions is not None:
        in_area = region_matches(notice.area_nm, applicant_regions)
        reasons.append("해당지역(거주지/소득본거지 매칭)" if in_area else "기타지역")

    return {"rank": rank, "regulated": regulated, "reasons": reasons, "in_area": in_area}


def judge_rank_public(
    notice,
    p: Profile,
    today: date | None = None,
    applicant_regions: list[str] | None = None,
) -> dict:
    """국민주택(공공) 1·2순위 판정 — 가입기간 + 납입횟수 + 무주택 + 규제지역 세대주.

    반환 키는 `judge_rank`(민영)와 **정확히 같다**(`rank`/`regulated`/`reasons`/`in_area`) —
    호출측(대시보드)이 두 결과를 같은 코드로 다루기 때문이다.
    민영과 달리 예치금·전용면적을 보지 않으므로 `house_types`를 받지 않는다.

    사유가 하나라도 있으면 2순위, 없으면 1순위.
    `p.account.opened`가 없으면 가입기간 0개월로 계산한다 — 예외를 던지지 않는다.

    정책(D19 동일): `applicant_regions`가 주어지면 `in_area`를 함께 돌려주지만
    지역은 1·2순위 요건이 아니므로 `rank`에 영향을 주지 않는다.
    정책(R3): 규제지역 거주기간 미달은 사유에만 표기하고 순위를 낮추지 않는다.
    """
    today = today or date.today()
    regulated = _is_regulated(notice.raw or {})
    group = _public_area_group(notice, regulated)
    reasons: list[str] = []

    months = _full_months(p.account.opened, today) if p.account.opened else 0
    need_months = PUBLIC_ACCOUNT_MONTHS[group]
    if months < need_months:
        reasons.append(f"통장 가입기간 부족({months}개월<{need_months}개월)")

    need_count = PUBLIC_PAYMENT_COUNTS[group]
    if p.account_payment_count < need_count:
        reasons.append(f"납입횟수 부족({p.account_payment_count}회<{need_count}회)")

    if not p.household_all_homeless:
        reasons.append("무주택 세대구성원 아님")

    if regulated and not p.is_household_head:
        reasons.append("규제지역: 세대주 아님")

    # 순위는 거주기간·지역 사유가 붙기 전에 확정한다(R3 / D19).
    rank = "2순위" if reasons else "1순위"
    reasons += _residence_reasons(notice, p, today, regulated)

    in_area: bool | None = None
    if applicant_regions is not None:
        in_area = region_matches(notice.area_nm, applicant_regions)
        reasons.append("해당지역(거주지/소득본거지 매칭)" if in_area else "기타지역")

    return {"rank": rank, "regulated": regulated, "reasons": reasons, "in_area": in_area}


def _income_pct(p: Profile) -> float | None:
    inc = p.income
    if inc.monthly_manwon is None or not inc.base_manwon:
        return None
    return inc.monthly_manwon / inc.base_manwon * 100


def _income_tier(pct: float | None, priority: int, general: int, p: Profile) -> tuple[str, str]:
    """소득 구간 → (구간명, 사유). 소득 미입력이면 판정 보류."""
    if pct is None:
        return "판정불가", "소득 정보 미입력"
    if pct <= priority:
        return "우선공급", f"소득 {pct:.0f}% ≤ {priority}%"
    if pct <= general:
        return "일반공급", f"소득 {pct:.0f}% ≤ {general}%"
    if p.real_estate_manwon <= LOTTERY_ASSET_CAP_MANWON:
        return "추첨제", f"소득 {pct:.0f}% 초과 + 부동산 {p.real_estate_manwon:,}만원 ≤ 3.31억"
    return "부적격", f"소득 {pct:.0f}% 초과 + 부동산가액 초과"


def judge_newlywed(p: Profile, today: date | None = None) -> dict:
    """신혼부부 특공(민영): 혼인 7년 이내 + 무주택세대 + 소득/자산 구간.

    정책(R5, 사용자 지정): `partners`(예비신혼 상대방) 중 한 명이라도 자가를 보유하면
    부적격으로 본다. 예비신혼은 아직 한 세대가 아니므로 `household_all_homeless`와
    **별개로** 보는 것이며, 공식 청약 규칙의 판정과 다를 수 있다.
    `partners`가 비어 있으면(기본값) 기존 판정과 완전히 동일하다.
    """
    today = today or date.today()
    reasons: list[str] = []
    if p.marriage_date is None and not p.engaged:
        return {"eligible": False, "tier": None, "reasons": ["미혼"]}
    my = _full_years(p.marriage_date, today) if p.marriage_date else 0.0
    if newlywed_period_exceeded(p.marriage_date, today):
        reasons.append(f"혼인 {my:.1f}년(>{NEWLYWED_MAX_YEARS}년)")
    if not p.household_all_homeless:
        reasons.append("무주택세대 아님")
    if any(pt.owns_home for pt in p.partners):
        reasons.append("예비신혼 상대방 자가 보유")
    if reasons:
        return {"eligible": False, "tier": None, "reasons": reasons}

    pri, gen = NEWLYWED_PRIORITY_PCT, NEWLYWED_GENERAL_PCT
    idx = 1 if p.income.dual_income else 0
    tier, why = _income_tier(_income_pct(p), pri[idx], gen[idx], p)
    if p.marriage_date is None:
        why += " · 예비신혼부부(입주 전 혼인신고 필요)"
    if p.children_minor > 0:
        why += f" · 자녀 {p.children_minor}명(구간 내 1순위)"
    return {"eligible": tier not in ("부적격",), "tier": tier, "reasons": [why]}


def judge_first_life(p: Profile, today: date | None = None) -> dict:
    """생애최초 특공(민영): 생애 무소유 + 소득세 5년 + 소득/자산 구간.
    미혼·무자녀 1인가구는 추첨제만 가능(2021.11 개편)."""
    today = today or date.today()
    reasons: list[str] = []
    fl = p.first_life
    if fl.ever_owned_house:
        reasons.append("과거 주택 소유 이력")
    if not p.household_all_homeless:
        reasons.append("무주택세대 아님")
    if not fl.income_tax_5y:
        reasons.append("소득세 납부 5년 미만")
    if not fl.currently_earning:
        reasons.append("현재 소득 없음")
    if reasons:
        return {"eligible": False, "tier": None, "reasons": reasons}

    single = p.marriage_date is None and p.children_minor == 0
    if single:
        if p.real_estate_manwon <= LOTTERY_ASSET_CAP_MANWON:
            return {"eligible": True, "tier": "추첨제", "reasons": ["1인가구는 추첨제만 가능"]}
        return {"eligible": False, "tier": None, "reasons": ["1인가구 + 부동산가액 초과"]}

    tier, why = _income_tier(_income_pct(p), FIRSTLIFE_PRIORITY_PCT, FIRSTLIFE_GENERAL_PCT, p)
    return {"eligible": tier not in ("부적격",), "tier": tier, "reasons": [why]}


def _housing_type(notice) -> str | None:
    """공고를 "민영"/"국민"으로 판별한다. 어느 쪽도 아니면 None(판정 미지원).

    민영 판정은 기존 D20 그대로 **청약홈 소스에 한한다**(다른 수집원의 민영 표기는
    예치금·전용면적 정보를 신뢰할 수 없다). 국민은 공고 표기 또는 공공 수집원으로 본다.
    """
    source = getattr(notice, "source", None) or (notice.raw or {}).get("_source")
    dtl = notice.house_dtl_secd_nm or ""
    if source == "applyhome" and "민영" in dtl:
        return "민영"
    if "국민" in dtl or source in PUBLIC_SOURCES:
        return "국민"
    return None


def judge_notice(notice, house_types, p: Profile, today: date | None = None) -> dict:
    """공고 1건에 대한 종합 판정. 민영/국민주택을 유형별로 분기한다(R2).

    두 유형이 **같은 키**를 돌려주고, 소비자는 `housing_type`으로 분기한다 —
    요약 문자열을 파싱해 분기하지 말 것. 판별 불가 공고는 기존대로 `supported=False`.

    주의: 국민 경로의 `score`/`newlywed`/`first_life`는 **민영 기준 참고값**이다.
    국민주택 순위(순차제: 가입기간·납입횟수)에 맞게 판정된 것은 `rank`뿐이다.
    """
    today = today or date.today()
    housing_type = _housing_type(notice)
    if housing_type is None:
        return {
            "supported": False,
            "housing_type": None,
            "reason": "민영·국민 어느 유형으로도 판별할 수 없는 공고 — 판정 미지원",
        }

    score = score_points(p, today)
    if housing_type == "민영":
        rank = judge_rank(notice, house_types, p, today)
        head = f"가점 {score['total']}점 · {rank['rank']}"
    else:
        # 국민주택은 가점제가 아니라 순차제이므로 요약에 가점을 넣지 않는다.
        rank = judge_rank_public(notice, p, today)
        head = f"국민주택 · {rank['rank']}"

    newlywed = judge_newlywed(p, today)
    first_life = judge_first_life(p, today)

    parts = [head]
    if newlywed["tier"]:
        parts.append(f"신혼 {newlywed['tier']}")
    if first_life["tier"]:
        parts.append(f"생초 {first_life['tier']}")
    return {
        "supported": True,
        "housing_type": housing_type,
        "score": score,
        "rank": rank,
        "newlywed": newlywed,
        "first_life": first_life,
        "summary": " | ".join(parts),
    }
