from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel

# ── 규칙 상수 (민영주택 기준, 2026-07 확인 — 제도 변경 시 여기만 수정) ──
# 소득초과자 추첨제 부동산가액 상한(만원): 국토부 2021.11 개편(3억3,100만원)
LOTTERY_ASSET_CAP_MANWON = 33_100
# 신혼부부 특공 소득 상한(% of 도시근로자 월평균소득): (외벌이, 맞벌이)
NEWLYWED_PRIORITY_PCT = (100, 120)  # 우선공급
NEWLYWED_GENERAL_PCT = (140, 160)  # 일반공급
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


class Profile(BaseModel):
    birth_date: date | None = None
    marriage_date: date | None = None  # 혼인신고일(미혼 null)
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


def load_profile(path: str = "config/profile.yaml") -> Profile | None:
    """프로필 파일이 없으면 None(판정 기능 비활성) — 알림/웹은 기존 그대로 동작."""
    p = Path(path)
    if not p.exists():
        return None
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Profile(**data)


def _full_years(start: date, end: date) -> float:
    return max(0.0, (end - start).days / 365.25)


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
    homeless_pts = min(32, 2 * (int(hy) + 1)) if p.household_all_homeless else 0

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


def judge_rank(notice, house_types, p: Profile, today: date | None = None) -> dict:
    """민영주택 1·2순위 판정. (통장 가입기간 + 예치금 + 규제지역 추가요건)"""
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

    blocking = [r for r in reasons if not r.startswith("일부")]
    return {"rank": "2순위" if blocking else "1순위", "regulated": regulated, "reasons": reasons}


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
    """신혼부부 특공(민영): 혼인 7년 이내 + 무주택세대 + 소득/자산 구간."""
    today = today or date.today()
    reasons: list[str] = []
    if p.marriage_date is None:
        return {"eligible": False, "tier": None, "reasons": ["미혼"]}
    my = _full_years(p.marriage_date, today)
    if my > 7:
        reasons.append(f"혼인 {my:.1f}년(>7년)")
    if not p.household_all_homeless:
        reasons.append("무주택세대 아님")
    if reasons:
        return {"eligible": False, "tier": None, "reasons": reasons}

    pri, gen = NEWLYWED_PRIORITY_PCT, NEWLYWED_GENERAL_PCT
    idx = 1 if p.income.dual_income else 0
    tier, why = _income_tier(_income_pct(p), pri[idx], gen[idx], p)
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


def judge_notice(notice, house_types, p: Profile, today: date | None = None) -> dict:
    """공고 1건에 대한 종합 판정. 민영(청약홈)만 지원 — 공공/LH는 별도 기준(순차제)."""
    today = today or date.today()
    source = getattr(notice, "source", None) or (notice.raw or {}).get("_source")
    dtl = notice.house_dtl_secd_nm or ""
    if source == "lh" or "민영" not in dtl:
        return {
            "supported": False,
            "reason": "공공·국민주택은 별도 기준(납입액 순차제) — 판정 미지원",
        }

    score = score_points(p, today)
    rank = judge_rank(notice, house_types, p, today)
    newlywed = judge_newlywed(p, today)
    first_life = judge_first_life(p, today)

    parts = [f"가점 {score['total']}점 · {rank['rank']}"]
    if newlywed["tier"]:
        parts.append(f"신혼 {newlywed['tier']}")
    if first_life["tier"]:
        parts.append(f"생초 {first_life['tier']}")
    return {
        "supported": True,
        "score": score,
        "rank": rank,
        "newlywed": newlywed,
        "first_life": first_life,
        "summary": " | ".join(parts),
    }
