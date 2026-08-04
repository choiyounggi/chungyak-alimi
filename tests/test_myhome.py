from __future__ import annotations

from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from src.collectors.myhome import (
    MYHOME_BASE,
    RENT_PATH,
    SALE_PATH,
    MyhomeNotice,
    fetch_myhome_notices,
)

MYHOME_BASE_PATH = httpx.URL(MYHOME_BASE).path

RENT_ROW = {
    "pblancId": "20942", "houseSn": 1, "sttusNm": "정정공고",
    "pblancNm": "물금2 천년나무 행복주택 잔여물량 모집", "suplyInsttNm": "LH",
    "houseTyNm": "아파트", "suplyTyNm": "행복주택", "beforePblancId": "20893",
    "rcritPblancDe": "20260701", "przwnerPresnatnDe": "20260813",
    "brtcNm": "경상남도", "signguNm": "양산시",
    "fullAdres": "경상남도 양산시 물금읍 청운로 42 ",
    "pnu": "4833025321108960001", "sumSuplyCo": 70,
    "rentGtn": 10800000, "mtRntchrg": 54540,
    "beginDe": "20260713", "endDe": "20260812",
    "url": "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?panId=2015122300020501",
}

# 분양 응답에는 suplyTyNm·suplyHoCo·totHshldCo·rentGtn·mtRntchrg 가 없다.
SALE_ROW = {
    k: v for k, v in RENT_ROW.items()
    if k not in ("suplyTyNm", "rentGtn", "mtRntchrg")
} | {
    "pblancId": "31001", "houseSn": 2, "sttusNm": "공고중",
    "pblancNm": "고양창릉 A4BL 공공분양주택 입주자 모집",
    "beforePblancId": "",
}


def _envelope(items: list[dict], *, total: int | None = None, result_code: str = "00") -> dict:
    return {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": "OK"},
            "body": {
                "totalCount": str(len(items) if total is None else total),
                "numOfRows": "100",
                "pageNo": "1",
                "item": items,
            },
        }
    }


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── 정상: 임대 행 매핑 ──
def test_maps_rent_row():
    n = MyhomeNotice.model_validate({**RENT_ROW, "_kind": "rent"})
    assert n.pblanc_no == "20942-1"
    assert n.house_manage_no == "20893-1"  # beforePblancId 기준 그룹 키(D25·D27)
    assert n.house_nm.startswith("[정정공고]")
    assert n.area_nm == "경남"
    assert n.agency == "LH"
    assert n.house_secd_nm == "아파트"
    assert n.house_dtl_secd_nm == "행복주택"
    assert n.rent_gtn == 10800000
    assert n.mt_rntchrg == 54540
    assert n.tot_suply_hshldco == 70
    assert n.rcrit_pblanc_de == date(2026, 7, 1)
    assert n.rcept_bgnde == date(2026, 7, 13)
    assert n.rcept_endde == date(2026, 8, 12)
    assert n.przwner_presnatn_de == date(2026, 8, 13)
    assert n.hsslpy_adres == "경상남도 양산시 물금읍 청운로 42"
    assert n.raw["pnu"] == "4833025321108960001"  # D28 — 보관만
    assert "_kind" not in n.raw


# ── 정상: 분양 행은 공급유형 기본값 ──
def test_sale_row_gets_default_supply_type():
    n = MyhomeNotice.model_validate({**SALE_ROW, "_kind": "sale"})
    assert n.house_dtl_secd_nm == "공공분양"
    assert n.rent_gtn is None
    assert n.mt_rntchrg is None
    assert n.pblanc_no == "31001-2"
    assert n.house_manage_no == "31001-2"  # beforePblancId 없으면 pblancId
    assert not n.house_nm.startswith("[정정공고]")


# ── 기관 매핑: LH 외는 기타 (D8) ──
def test_non_lh_agency_is_etc():
    n = MyhomeNotice.model_validate({**RENT_ROW, "suplyInsttNm": "인천도시공사", "_kind": "rent"})
    assert n.agency == "기타"
    assert n.bsns_mby_nm == "인천도시공사"


# ── 에러: 공고명이 비면 ValidationError ──
def test_blank_house_nm_raises():
    with pytest.raises(ValidationError):
        MyhomeNotice.model_validate({**RENT_ROW, "pblancNm": "", "sttusNm": "공고중", "_kind": "rent"})


# ── 경계: 날짜·숫자·URL의 이상값은 None ──
def test_bad_values_become_none():
    n = MyhomeNotice.model_validate({
        **RENT_ROW,
        "endDe": "",
        "rcritPblancDe": "공고문 참조",
        "sumSuplyCo": "",
        "rentGtn": "미정",
        "mtRntchrg": None,
        "url": "javascript:alert(1)",
        "fullAdres": "   ",
        "brtcNm": "",
        "_kind": "rent",
    })
    assert n.rcept_endde is None
    assert n.rcrit_pblanc_de is None
    assert n.tot_suply_hshldco is None
    assert n.rent_gtn is None
    assert n.mt_rntchrg is None
    assert n.pblanc_url is None
    assert n.hsslpy_adres is None
    assert n.area_nm is None


# ── 수집: 파싱 실패 행만 스킵되고 나머지는 남는다 (D9) ──
def test_bad_row_is_skipped():
    bad = {**RENT_ROW, "pblancId": "99999", "pblancNm": "", "sttusNm": "공고중"}

    def handler(request: httpx.Request) -> httpx.Response:
        if RENT_PATH in request.url.path:
            return httpx.Response(200, json=_envelope([RENT_ROW, bad]))
        return httpx.Response(200, json=_envelope([]))

    with _client(handler) as c:
        out = fetch_myhome_notices(per_page=100, client=c)
    assert [n.pblanc_no for n in out] == ["20942-1"]


# ── 경계: 빈 item 이면 더 이상 페이징하지 않는다 ──
def test_empty_item_stops_paging():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=_envelope([], total=0))

    with _client(handler) as c:
        out = fetch_myhome_notices(per_page=100, max_pages=20, client=c)
    assert out == []
    assert len(calls) == 2  # 임대·분양 각 1페이지씩만


# ── 에러: resultCode 가 00 이 아니면 빈 결과 ──
def test_non_ok_result_code_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope([RENT_ROW], result_code="30"))

    with _client(handler) as c:
        assert fetch_myhome_notices(client=c) == []


# ── 수집: 오퍼레이션 간 같은 pblancId+houseSn 은 1건 ──
def test_dedup_across_operations():
    def handler(request: httpx.Request) -> httpx.Response:
        kind_row = RENT_ROW if RENT_PATH in request.url.path else {
            k: v for k, v in RENT_ROW.items() if k not in ("suplyTyNm", "rentGtn", "mtRntchrg")
        }
        return httpx.Response(200, json=_envelope([kind_row]))

    with _client(handler) as c:
        out = fetch_myhome_notices(client=c)
    assert len(out) == 1
    assert out[0].pblanc_no == "20942-1"
    assert out[0].rent_gtn == 10800000  # 먼저 수집된 임대 행이 유지된다


# ── 페이징: totalCount 도달까지만 요청한다 (D11) ──
def test_pages_until_total_count():
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("pageNo")
        pages.append(f"{request.url.path}:{page}")
        if SALE_PATH in request.url.path:
            return httpx.Response(200, json=_envelope([], total=0))
        row = {**RENT_ROW, "pblancId": f"{page}00", "houseSn": 1}
        return httpx.Response(200, json=_envelope([row], total=2))

    with _client(handler) as c:
        out = fetch_myhome_notices(per_page=1, max_pages=20, client=c)
    assert [n.pblanc_no for n in out] == ["100-1", "200-1"]
    assert pages == [f"{MYHOME_BASE_PATH}{RENT_PATH}:1", f"{MYHOME_BASE_PATH}{RENT_PATH}:2",
                     f"{MYHOME_BASE_PATH}{SALE_PATH}:1"]


# ── 경계: item 이 dict 단건이어도 리스트로 정규화된다 ──
def test_single_item_dict_is_normalized():
    def handler(request: httpx.Request) -> httpx.Response:
        if RENT_PATH in request.url.path:
            body = _envelope([])
            body["response"]["body"]["item"] = RENT_ROW
            body["response"]["body"]["totalCount"] = "1"
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=_envelope([]))

    with _client(handler) as c:
        out = fetch_myhome_notices(client=c)
    assert len(out) == 1
    assert out[0].pblanc_no == "20942-1"


# ── 경계: max_pages 상한을 넘지 않는다 (D11) ──
def test_max_pages_caps_requests():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        page = request.url.params.get("pageNo")
        row = {**RENT_ROW, "pblancId": f"{request.url.path}-{page}", "houseSn": 1}
        return httpx.Response(200, json=_envelope([row], total=9999))

    with _client(handler) as c:
        out = fetch_myhome_notices(per_page=1, max_pages=3, client=c)
    assert len(calls) == 6  # 두 오퍼레이션 × 3페이지
    assert len(out) == 6


# ── 회귀: houseSn 은 0 이 정상값이다(실측 100행 중 73행). falsy 로 뭉개면 안 된다 ──
def test_house_sn_zero_is_kept():
    n = MyhomeNotice.model_validate({**RENT_ROW, "houseSn": 0, "_kind": "rent"})
    assert n.pblanc_no.endswith("-0")
    assert n.house_manage_no.endswith("-0")


def test_house_sn_missing_yields_empty_suffix():
    """None 은 값 없음 → 빈 접미. 0 과 구분되어야 한다(경계)."""
    row = {k: v for k, v in RENT_ROW.items() if k != "houseSn"}
    n = MyhomeNotice.model_validate({**row, "_kind": "rent"})
    assert n.pblanc_no.endswith("-")
    assert not n.pblanc_no.endswith("-0")
