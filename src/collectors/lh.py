from __future__ import annotations

from datetime import date, timedelta

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..config import settings

LH_BASE = "https://apis.data.go.kr/B552555"
LIST_PATH = "/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1"
SPL_PATH = "/lhLeaseNoticeSplInfo1/getLeaseNoticeSplInfo1"

# 상위매물유형: 05 분양주택, 06 임대주택, 39 신혼희망타운, 13 주거복지
DEFAULT_TYPES = ("05", "06", "39", "13")

# LH 지역명(풀네임) → 청약홈 약칭 통일
REGION_MAP = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산",
    "세종특별자치시": "세종", "경기도": "경기", "강원도": "강원", "강원특별자치도": "강원",
    "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주",
}


def normalize_region(name: str | None) -> str | None:
    if not name:
        return name
    return REGION_MAP.get(name, name)


class LhNotice(BaseModel):
    """LH 분양임대공고 1건 (통합 notice 스키마에 맞춘 정규화)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pblanc_no: str = Field(alias="PAN_ID")
    house_nm: str = Field(alias="PAN_NM")
    area_nm: str | None = Field(default=None, alias="CNP_CD_NM")
    house_secd_nm: str | None = Field(default=None, alias="UPP_AIS_TP_NM")
    house_dtl_secd_nm: str | None = Field(default=None, alias="AIS_TP_CD_NM")
    rcept_bgnde: date | None = Field(default=None, alias="PAN_NT_ST_DT")
    rcept_endde: date | None = Field(default=None, alias="CLSG_DT")
    pblanc_url: str | None = Field(default=None, alias="DTL_URL")

    # 통합 notice 컬럼 호환용(LH 목록에 없는 값은 None)
    house_manage_no: str | None = None
    rent_secd_nm: str | None = None
    hsslpy_adres: str | None = None
    bsns_mby_nm: str | None = None
    rcrit_pblanc_de: date | None = None
    spsply_rcept_bgnde: date | None = None
    spsply_rcept_endde: date | None = None
    przwner_presnatn_de: date | None = None
    tot_suply_hshldco: int | None = None
    mvn_prearnge_ym: str | None = None

    raw: dict = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _prep(cls, data):
        if isinstance(data, dict) and "raw" not in data:
            d = {**data, "raw": dict(data)}
            if d.get("CNP_CD_NM"):
                d["CNP_CD_NM"] = normalize_region(d["CNP_CD_NM"])
            return d
        return data

    @field_validator("rcept_bgnde", "rcept_endde", mode="before")
    @classmethod
    def _lhdate(cls, v):
        if not v:
            return None
        return str(v).replace(".", "-").strip()  # "2026.07.14" → "2026-07-14"


def _extract_ds_list(body, key: str = "dsList") -> list[dict]:
    for block in body if isinstance(body, list) else []:
        if isinstance(block, dict) and key in block:
            return block[key] or []
    return []


class LhSupply(BaseModel):
    """LH 공급정보(주택형별 면적·세대수). 분양가는 LH API 미제공('공고문 참조')."""

    model_config = ConfigDict(populate_by_name=True, extra="allow", protected_namespaces=())

    house_ty: str = Field(alias="HTY_NNA")          # 주택형
    suply_ar: float | None = Field(default=None, alias="SPL_AR")   # 공급면적(㎡)
    suply_hshldco: int | None = Field(default=None, alias="HSH_CNT")  # 세대수

    # notice_house_type 호환용 (LH 미제공)
    pblanc_no: str = ""
    house_manage_no: str | None = None
    model_no: str | None = None
    lttot_top_amount: int | None = None  # LH 분양가 없음
    spsply_hshldco: int | None = None

    raw: dict = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _stash_raw(cls, data):
        if isinstance(data, dict) and "raw" not in data:
            return {**data, "raw": dict(data)}
        return data

    @field_validator("suply_ar", "suply_hshldco", mode="before")
    @classmethod
    def _num_or_none(cls, v):
        if v in ("", None):
            return None
        try:
            float(v)
        except (TypeError, ValueError):
            return None  # "공고문 참조" 같은 텍스트 → None
        return v


def fetch_lh_supply(
    *,
    pan_id: str,
    ccr: str | None,
    spl: str | None,
    upp: str | None,
    ais: str | None,
    client: httpx.Client | None = None,
) -> list[LhSupply]:
    """특정 LH 공고(PAN_ID)의 공급정보(주택형별 면적·세대수)를 조회한다."""
    own_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        resp = client.get(
            LH_BASE + SPL_PATH,
            params={
                "serviceKey": settings.odcloud_api_key,
                "PAN_ID": pan_id,
                "CCR_CNNT_SYS_DS_CD": ccr,
                "SPL_INF_TP_CD": spl,
                "UPP_AIS_TP_CD": upp,
                "AIS_TP_CD": ais,
            },
        )
        resp.raise_for_status()
        rows = _extract_ds_list(resp.json(), "dsList01")
        out = []
        for row in rows:
            if not row.get("HTY_NNA"):
                continue
            item = LhSupply.model_validate(row)
            item.pblanc_no = pan_id
            out.append(item)
        return out
    finally:
        if own_client:
            client.close()


def fetch_lh_notices(
    *,
    types: tuple[str, ...] = DEFAULT_TYPES,
    since: date | None = None,
    until: date | None = None,
    per_page: int = 100,
    max_pages: int = 10,
    client: httpx.Client | None = None,
) -> list[LhNotice]:
    """LH 분양임대공고를 매물유형별로 수집한다(게시일 범위 [since, until])."""
    since = since or (date.today() - timedelta(days=60))
    until = until or (date.today() + timedelta(days=180))
    st, ed = since.strftime("%Y.%m.%d"), until.strftime("%Y.%m.%d")

    own_client = client is None
    client = client or httpx.Client(timeout=30.0)
    out: list[LhNotice] = []
    seen: set[str] = set()
    try:
        for tp in types:
            for page in range(1, max_pages + 1):
                resp = client.get(
                    LH_BASE + LIST_PATH,
                    params={
                        "serviceKey": settings.odcloud_api_key,
                        "PG_SZ": per_page,
                        "PAGE": page,
                        "PAN_NT_ST_DT": st,
                        "CLSG_DT": ed,
                        "UPP_AIS_TP_CD": tp,
                    },
                )
                resp.raise_for_status()
                rows = _extract_ds_list(resp.json())
                if not rows:
                    break
                for row in rows:
                    n = LhNotice.model_validate(row)
                    if n.pblanc_no not in seen:  # 유형 간 중복 제거
                        seen.add(n.pblanc_no)
                        out.append(n)
                if len(rows) < per_page:
                    break
    finally:
        if own_client:
            client.close()
    return out
