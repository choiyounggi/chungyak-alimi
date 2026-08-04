from __future__ import annotations

import logging
from datetime import date

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..config import settings
from .lh import normalize_region

logger = logging.getLogger(__name__)

MYHOME_BASE = "https://apis.data.go.kr/1613000/HWSPR02"
RENT_PATH = "/rsdtRcritNtcList"      # 공공임대 모집공고
SALE_PATH = "/ltRsdtRcritNtcList"    # 공공분양 모집공고


class MyhomeNotice(BaseModel):
    """마이홈포털 모집공고 1건(공고×단지) — 통합 notice 스키마에 맞춘 정규화."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pblanc_no: str                        # f"{pblancId}-{houseSn}" (D27)
    house_manage_no: str                  # f"{beforePblancId or pblancId}-{houseSn}" (D25·D27)
    house_nm: str = Field(min_length=1)
    house_secd_nm: str | None = None      # houseTyNm
    house_dtl_secd_nm: str | None = None  # suplyTyNm, 분양은 "공공분양"
    rent_secd_nm: str | None = None
    area_nm: str | None = None            # normalize_region(brtcNm)
    hsslpy_adres: str | None = None       # fullAdres
    bsns_mby_nm: str | None = None        # suplyInsttNm
    agency: str = "기타"                   # suplyInsttNm=="LH" → "LH" (D8)
    rcrit_pblanc_de: date | None = None
    rcept_bgnde: date | None = None       # beginDe
    rcept_endde: date | None = None       # endDe
    spsply_rcept_bgnde: date | None = None
    spsply_rcept_endde: date | None = None
    przwner_presnatn_de: date | None = None
    tot_suply_hshldco: int | None = None  # sumSuplyCo
    mvn_prearnge_ym: str | None = None
    pblanc_url: str | None = None         # url
    rent_gtn: int | None = None           # rentGtn(원)
    mt_rntchrg: int | None = None         # mtRntchrg(원)

    raw: dict = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _map(cls, d):
        # 임대·분양 응답 모양이 달라 alias 하나로 두 형태를 못 받는다 → 여기서 통합 스키마로 매핑
        if not isinstance(d, dict) or "pblanc_no" in d:
            return d
        # `or ""` 를 쓰면 안 된다 — houseSn 은 0 이 정상값이고(실측 100행 중 73행),
        # 0 은 falsy 라 빈 문자열로 뭉개져 pblanc_no 가 "20960-" 처럼 깨진다.
        pid = "" if d.get("pblancId") is None else str(d.get("pblancId"))
        sn = "" if d.get("houseSn") is None else str(d.get("houseSn"))
        base = str(d.get("beforePblancId") or "") or pid
        nm = str(d.get("pblancNm") or "").strip()
        if d.get("sttusNm") == "정정공고":
            nm = f"[정정공고]{nm}"           # D26 — 기존 접두사 규약과 통일
        inst = d.get("suplyInsttNm") or None
        return {
            "pblanc_no": f"{pid}-{sn}",
            "house_manage_no": f"{base}-{sn}",
            "house_nm": nm,
            "house_secd_nm": d.get("houseTyNm") or None,
            "house_dtl_secd_nm": d.get("suplyTyNm") or ("공공분양" if d.get("_kind") == "sale" else None),
            "area_nm": normalize_region(d.get("brtcNm")) or None,
            "hsslpy_adres": (d.get("fullAdres") or "").strip() or None,
            "bsns_mby_nm": inst,
            "agency": "LH" if inst == "LH" else "기타",
            "rcrit_pblanc_de": d.get("rcritPblancDe"),
            "rcept_bgnde": d.get("beginDe"),
            "rcept_endde": d.get("endDe"),
            "przwner_presnatn_de": d.get("przwnerPresnatnDe"),
            "tot_suply_hshldco": d.get("sumSuplyCo"),
            "pblanc_url": d.get("url"),
            "rent_gtn": d.get("rentGtn"),
            "mt_rntchrg": d.get("mtRntchrg"),
            "raw": {k: v for k, v in d.items() if k != "_kind"},
        }

    @field_validator(
        "rcrit_pblanc_de", "rcept_bgnde", "rcept_endde", "przwner_presnatn_de", mode="before"
    )
    @classmethod
    def _ymd(cls, v):
        s = str(v or "").strip()
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None

    @field_validator("tot_suply_hshldco", "rent_gtn", "mt_rntchrg", mode="before")
    @classmethod
    def _num_or_none(cls, v):
        if v in ("", None):
            return None
        try:
            int(v)
        except (TypeError, ValueError):
            return None  # "미정" 같은 텍스트 → None
        return v

    @field_validator("pblanc_url", mode="before")
    @classmethod
    def _safe_url(cls, v):
        if v and str(v).startswith(("http://", "https://")):
            return v
        return None


def _body(payload) -> dict:
    """응답 봉투에서 body 블록만 안전하게 꺼낸다."""
    if not isinstance(payload, dict):
        return {}
    resp = payload.get("response")
    body = resp.get("body") if isinstance(resp, dict) else None
    return body if isinstance(body, dict) else {}


def _ok(payload, *, where: str) -> bool:
    """마이홈 API 는 HTTP 200 으로 오류를 반환 — header.resultCode 가 '00' 인지 확인."""
    resp = payload.get("response") if isinstance(payload, dict) else None
    header = resp.get("header") if isinstance(resp, dict) else None
    code = header.get("resultCode") if isinstance(header, dict) else None
    if code != "00":
        logger.warning("마이홈 %s 오류 resultCode=%s", where, code)
        return False
    return True


def _items(payload) -> list[dict]:
    item = _body(payload).get("item")
    if isinstance(item, dict):       # 단건이면 dict 로 온다
        return [item]
    if isinstance(item, list):
        return [r for r in item if isinstance(r, dict)]
    return []


def _total_count(payload) -> int:
    try:
        return int(_body(payload).get("totalCount"))
    except (TypeError, ValueError):
        return 0


def fetch_myhome_notices(
    *, per_page: int = 100, max_pages: int = 20, client: httpx.Client | None = None
) -> list[MyhomeNotice]:
    """마이홈포털 공공임대·공공분양 모집공고를 수집한다."""
    own_client = client is None
    client = client or httpx.Client(timeout=30.0)  # D10 — 타임아웃 명시, 재시도 없음
    out: list[MyhomeNotice] = []
    seen: set[str] = set()
    try:
        for path, kind in ((RENT_PATH, "rent"), (SALE_PATH, "sale")):
            fetched = 0
            for page in range(1, max_pages + 1):
                resp = client.get(
                    MYHOME_BASE + path,
                    params={
                        "serviceKey": settings.odcloud_api_key,
                        "numOfRows": per_page,
                        "pageNo": page,
                        "type": "json",
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
                if not _ok(payload, where=f"{kind}(page={page})"):
                    break
                rows = _items(payload)
                if not rows:
                    break
                for row in rows:
                    try:
                        n = MyhomeNotice.model_validate({**row, "_kind": kind})
                    except ValidationError as e:
                        logger.warning("마이홈 공고 파싱 실패 스킵(%s): %s", kind, e)
                        continue
                    if n.pblanc_no not in seen:  # 오퍼레이션 간 중복 제거
                        seen.add(n.pblanc_no)
                        out.append(n)
                fetched += len(rows)
                if fetched >= _total_count(payload) or len(rows) < per_page:
                    break
    finally:
        if own_client:
            client.close()
    return out
