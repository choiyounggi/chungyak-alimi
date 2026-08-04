from __future__ import annotations

import logging
from datetime import date

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..config import settings
from .lh import normalize_region

logger = logging.getLogger(__name__)

HUG_URL = "https://www.khug.or.kr/SelectListInfo.do"
# 목록 API에 공고 상세 링크가 없다 — 든든전세 모집공고 페이지를 고정 링크로 쓴다.
HUG_NOTICE_URL = "https://www.khug.or.kr/jeonse/web/s07/s070102.jsp"
ROW_CAP = 300  # 실측 상한(D30)


class HugItem(BaseModel):
    """HUG 든든전세 물건 1건(응답 행). 공고가 아니라 개별 물건이다."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    coll_anno_dt: str = Field(alias="COLL_ANNO_DT")
    rcvi_sdt: str | None = Field(default=None, alias="SBSR_RCVI_SDT")
    rcvi_edt: str | None = Field(default=None, alias="SBSR_RCVI_EDT")
    area_nm_raw: str = Field(alias="AREA_DCD_NM")
    signgu_nm: str | None = Field(default=None, alias="AREA_DTL_DCD_NM")
    tmd_nm: str | None = Field(default=None, alias="TMD_NM")
    prop_kind: str | None = Field(default=None, alias="PROP_KIND_CD2_NM")
    exus_ara: float | None = Field(default=None, alias="EXUS_ARA")
    leas_guar_wn: int | None = Field(default=None, alias="LEAS_GUAR_WN")

    raw: dict = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _stash_raw(cls, data):
        if isinstance(data, dict) and "raw" not in data:
            return {**data, "raw": dict(data)}
        return data

    @field_validator("exus_ara", "leas_guar_wn", mode="before")
    @classmethod
    def _num_or_none(cls, v):
        if v in ("", None):
            return None
        try:
            float(v)
        except (TypeError, ValueError):
            return None
        return v


class HugNotice(BaseModel):
    """(공고일자 × 시도) 단위로 집계한 HUG 공고 1건 (통합 notice 스키마)."""

    model_config = ConfigDict(extra="allow")

    pblanc_no: str
    house_manage_no: str | None = None
    house_nm: str
    house_secd_nm: str | None = None
    house_dtl_secd_nm: str | None = None
    rent_secd_nm: str | None = None
    area_nm: str | None = None
    hsslpy_adres: str | None = None
    bsns_mby_nm: str | None = None
    agency: str = "HUG"
    rent_gtn: int | None = None
    mt_rntchrg: int | None = None

    rcrit_pblanc_de: date | None = None
    rcept_bgnde: date | None = None
    rcept_endde: date | None = None
    spsply_rcept_bgnde: date | None = None
    spsply_rcept_endde: date | None = None
    przwner_presnatn_de: date | None = None

    tot_suply_hshldco: int | None = None
    mvn_prearnge_ym: str | None = None
    pblanc_url: str | None = None

    raw: dict = Field(default_factory=dict, exclude=True)


def _ymd(v: str | None) -> str | None:
    """'20260807170000'/'20260807' 앞 8자리를 'YYYY-MM-DD'로. 형식이 아니면 None."""
    s = str(v or "")[:8]
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None


def _as_date(v: str | None) -> date | None:
    s = _ymd(v)
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:  # "20261345" 같은 8자리 비날짜
        return None


def _first(values: list[str | None]) -> str | None:
    """그룹 내 첫 유효값. 접수기간은 한 공고 안에서 동일하다(실측)."""
    return next((v for v in values if v), None)


def aggregate(items: list[HugItem]) -> list[HugNotice]:
    """물건 목록을 (공고일자 × 시도) 단위 공고로 집계한다(D29)."""
    groups: dict[tuple[str, str], list[HugItem]] = {}
    for it in items:
        groups.setdefault((it.coll_anno_dt, it.area_nm_raw), []).append(it)

    out: list[HugNotice] = []
    for (anno_dt, area_raw), group in groups.items():
        kinds = [it.prop_kind for it in group if it.prop_kind]
        # 최빈값, 동률이면 사전순 첫 값 — 입력 순서와 무관하게 결정적이어야 한다.
        house_secd_nm = min(set(kinds), key=lambda k: (-kinds.count(k), k)) if kinds else None
        deposits = [it.leas_guar_wn for it in group if it.leas_guar_wn is not None]

        out.append(
            HugNotice(
                pblanc_no=f"{anno_dt}-{area_raw}",
                house_nm=f"HUG 든든전세주택 입주자 모집 ({_ymd(anno_dt) or anno_dt}) "
                f"{normalize_region(area_raw)}",
                house_secd_nm=house_secd_nm,
                house_dtl_secd_nm="든든전세",
                area_nm=normalize_region(area_raw),
                bsns_mby_nm="주택도시보증공사",
                agency="HUG",
                rent_gtn=min(deposits) if deposits else None,
                rcrit_pblanc_de=_as_date(anno_dt),
                rcept_bgnde=_as_date(_first([it.rcvi_sdt for it in group])),
                rcept_endde=_as_date(_first([it.rcvi_edt for it in group])),
                tot_suply_hshldco=len(group),
                # 읍면동까지만 있어 지오코딩에 부적합 — hsslpy_adres 는 넣지 않는다.
                pblanc_url=HUG_NOTICE_URL,
                raw={"_hug_items": [it.raw for it in group], "_area": area_raw},
            )
        )

    out.sort(key=lambda n: n.pblanc_no)
    return out


def fetch_hug_notices(*, client: httpx.Client | None = None) -> list[HugNotice]:
    """HUG 든든전세 모집공고를 수집한다. 키 미설정이면 빈 리스트."""
    if not settings.hug_api_key:
        return []

    own_client = client is None
    client = client or httpx.Client(timeout=30.0)  # D10: 명시 타임아웃, 재시도 없음
    try:
        resp = client.get(HUG_URL, params={"API_KEY": settings.hug_api_key})
        resp.raise_for_status()
        data = resp.json()
    finally:
        if own_client:
            client.close()

    if not isinstance(data, list):
        logger.warning("HUG 응답이 배열이 아님 — 스킵 (type=%s)", type(data).__name__)
        return []
    if len(data) >= ROW_CAP:
        logger.warning("HUG 응답이 상한(%d)에 걸렸을 수 있음 — 일부 물건 누락 가능", ROW_CAP)

    items: list[HugItem] = []
    for row in data:
        try:
            items.append(HugItem.model_validate(row))
        except ValidationError as e:
            logger.warning("HUG 물건 파싱 실패 스킵: %s", e)
    return aggregate(items)
