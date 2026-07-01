from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 청약홈 분양정보(getAPTLttotPblancDetail)에서 비어있을 수 있는 날짜 필드들.
# API 는 미정 항목을 null 또는 "" 로 준다 → None 으로 정규화한다.
_DATE_FIELDS = (
    "rcrit_pblanc_de",
    "rcept_bgnde",
    "rcept_endde",
    "spsply_rcept_bgnde",
    "spsply_rcept_endde",
    "przwner_presnatn_de",
)


class ApplyhomeNotice(BaseModel):
    """청약홈 APT 분양정보 상세 공고 1건 (정규화)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pblanc_no: str = Field(alias="PBLANC_NO")
    house_manage_no: str | None = Field(default=None, alias="HOUSE_MANAGE_NO")
    house_nm: str = Field(alias="HOUSE_NM")
    house_secd_nm: str | None = Field(default=None, alias="HOUSE_SECD_NM")
    house_dtl_secd_nm: str | None = Field(default=None, alias="HOUSE_DTL_SECD_NM")
    rent_secd_nm: str | None = Field(default=None, alias="RENT_SECD_NM")
    area_nm: str | None = Field(default=None, alias="SUBSCRPT_AREA_CODE_NM")
    hsslpy_adres: str | None = Field(default=None, alias="HSSPLY_ADRES")
    bsns_mby_nm: str | None = Field(default=None, alias="BSNS_MBY_NM")

    rcrit_pblanc_de: date | None = Field(default=None, alias="RCRIT_PBLANC_DE")
    rcept_bgnde: date | None = Field(default=None, alias="RCEPT_BGNDE")
    rcept_endde: date | None = Field(default=None, alias="RCEPT_ENDDE")
    spsply_rcept_bgnde: date | None = Field(default=None, alias="SPSPLY_RCEPT_BGNDE")
    spsply_rcept_endde: date | None = Field(default=None, alias="SPSPLY_RCEPT_ENDDE")
    przwner_presnatn_de: date | None = Field(default=None, alias="PRZWNER_PRESNATN_DE")

    tot_suply_hshldco: int | None = Field(default=None, alias="TOT_SUPLY_HSHLDCO")
    mvn_prearnge_ym: str | None = Field(default=None, alias="MVN_PREARNGE_YM")
    pblanc_url: str | None = Field(default=None, alias="PBLANC_URL")

    # 원본 응답 보존 (Phase 2 에서 raw jsonb 로 저장)
    raw: dict = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _stash_raw(cls, data):
        if isinstance(data, dict) and "raw" not in data:
            return {**data, "raw": dict(data)}
        return data

    @field_validator(*_DATE_FIELDS, mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if v in ("", None):
            return None
        return v


class ApplyhomeHouseType(BaseModel):
    """청약홈 APT 주택형별 상세 1건 (면적·분양가). getAPTLttotPblancMdl"""

    # protected_namespaces=() : model_no 가 pydantic 보호 접두사(model_)와 겹치는 경고 방지
    model_config = ConfigDict(populate_by_name=True, extra="allow", protected_namespaces=())

    pblanc_no: str = Field(alias="PBLANC_NO")
    house_manage_no: str | None = Field(default=None, alias="HOUSE_MANAGE_NO")
    model_no: str | None = Field(default=None, alias="MODEL_NO")
    house_ty: str = Field(alias="HOUSE_TY")
    suply_ar: float | None = Field(default=None, alias="SUPLY_AR")  # 공급면적(㎡)
    lttot_top_amount: int | None = Field(default=None, alias="LTTOT_TOP_AMOUNT")  # 분양최고가(만원)
    suply_hshldco: int | None = Field(default=None, alias="SUPLY_HSHLDCO")  # 일반공급 세대
    spsply_hshldco: int | None = Field(default=None, alias="SPSPLY_HSHLDCO")  # 특별공급 세대

    raw: dict = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _stash_raw(cls, data):
        if isinstance(data, dict) and "raw" not in data:
            return {**data, "raw": dict(data)}
        return data

    @field_validator("suply_ar", "lttot_top_amount", "suply_hshldco", "spsply_hshldco", mode="before")
    @classmethod
    def _empty_num_to_none(cls, v):
        if v in ("", None):
            return None
        return v
