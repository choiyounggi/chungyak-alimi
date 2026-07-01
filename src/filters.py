from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel

# 특별공급 라벨 → 청약홈 주택형(raw) 세대수 필드
SPECIAL_SUPPLY_KEYS = {
    "생애최초": "LFE_FRST_HSHLDCO",
    "신혼부부": "NWBB_HSHLDCO",
    "다자녀": "MNYCH_HSHLDCO",
    "노부모": "OLD_PARNTS_SUPORT_HSHLDCO",
    "기관추천": "INSTT_RECOMEND_HSHLDCO",
}


class FilterConfig(BaseModel):
    regions: list[str] = []
    house_types: list[str] = []
    supply_types: list[str] = []
    special_supply: list[str] = []
    min_households: int | None = None
    price_max_manwon: int | None = None
    exclude_keywords: list[str] = []
    only_open: bool = True  # 접수마감이 지난 공고 제외(미래/진행 청약만)


def load_filter_config(path: str = "config/filters.yaml") -> FilterConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return FilterConfig(**data)


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def match_notice(
    notice, house_types, cfg: FilterConfig, today: date | None = None
) -> tuple[bool, list[str]]:
    """공고(+주택형들)가 필터를 통과하는지 판정. (통과여부, 탈락사유목록) 반환.

    notice/house_types 는 pydantic 모델(수집 직후) 또는 ORM 행(DB) 모두 허용 —
    필요한 속성(area_nm, house_secd_nm, lttot_top_amount, raw ...)만 접근한다.
    빈 필터([]/None)는 '제한 없음'으로 통과시킨다.
    """
    today = today or date.today()
    fails: list[str] = []

    # 접수마감이 지난 공고 제외(미래/진행 청약만). 최종 마감 = max(일반, 특공).
    if cfg.only_open:
        deadlines = [d for d in (notice.rcept_endde, notice.spsply_rcept_endde) if d]
        deadline = max(deadlines) if deadlines else None
        if deadline is not None and deadline < today:
            fails.append("접수마감")

    if cfg.regions and notice.area_nm not in cfg.regions:
        fails.append(f"지역:{notice.area_nm}")

    if cfg.house_types and notice.house_secd_nm not in cfg.house_types:
        fails.append(f"주택유형:{notice.house_secd_nm}")

    if cfg.supply_types and notice.house_dtl_secd_nm not in cfg.supply_types:
        fails.append(f"공급유형:{notice.house_dtl_secd_nm}")

    if cfg.exclude_keywords and any(k in (notice.house_nm or "") for k in cfg.exclude_keywords):
        fails.append("제외키워드")

    if cfg.min_households is not None and (notice.tot_suply_hshldco or 0) < cfg.min_households:
        fails.append("세대수미달")

    # 분양가: 주택형 중 하나라도 상한 이하면 통과(살 수 있는 평형이 있으면 OK).
    # 분양가 정보가 전혀 없으면(임대 등) 가격 조건은 보류(통과).
    if cfg.price_max_manwon is not None:
        prices = [ht.lttot_top_amount for ht in house_types if ht.lttot_top_amount is not None]
        if prices and min(prices) > cfg.price_max_manwon:
            fails.append("분양가초과")

    # 특별공급: 관심 특공 세대수 > 0 인 주택형이 하나라도 있으면 통과.
    # 특공 세대수 필드를 실제로 가진 주택형(청약홈)만 판정 대상 — LH 등 특공 정보가
    # 없는 소스는 주택형이 보강돼도(raw에 특공 키 부재) 판정을 보류(통과)한다.
    # (그렇지 않으면 LH 보강 후 재평가에서 매칭됐던 공고가 '특공없음'으로 뒤집힌다.)
    if cfg.special_supply:
        keys = [SPECIAL_SUPPLY_KEYS[s] for s in cfg.special_supply if s in SPECIAL_SUPPLY_KEYS]
        has_special_data = any(k in ht.raw for ht in house_types for k in keys)
        if keys and has_special_data:
            has = any(_to_int(ht.raw.get(k)) > 0 for ht in house_types for k in keys)
            if not has:
                fails.append("특공없음")

    return (len(fails) == 0, fails)
