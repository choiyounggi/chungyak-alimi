from __future__ import annotations

import logging
from datetime import date

import httpx
import pytest

from src.collectors import hug
from src.collectors.hug import HUG_NOTICE_URL, ROW_CAP, HugItem, aggregate, fetch_hug_notices


def make_row(**over) -> dict:
    """실측 응답 행(9필드)을 축약한 픽스처."""
    base = {
        "COLL_ANNO_DT": "20260724",
        "SBSR_RCVI_SDT": "20260724100000",
        "SBSR_RCVI_EDT": "20260807170000",
        "AREA_DCD_NM": "서울특별시",
        "AREA_DTL_DCD_NM": "서울 강북구",
        "TMD_NM": "서울특별시 강북구 수유동",
        "PROP_KIND_CD2_NM": "다세대주택",
        "EXUS_ARA": "42.29",
        "LEAS_GUAR_WN": "188100000",
    }
    return {**base, **over}


def items(rows: list[dict]) -> list[HugItem]:
    return [HugItem.model_validate(r) for r in rows]


def fake_client(rows, *, calls: list | None = None, status: int = 200) -> httpx.Client:
    """HUG 오픈API 페이크(D23) — 실제 네트워크 호출 없음."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, json=rows)

    return httpx.Client(transport=httpx.MockTransport(handler))


# ── 정상: (공고일자 × 시도) 단위 집계 (D29) ──
def test_aggregates_by_region(monkeypatch):
    monkeypatch.setattr(hug.settings, "hug_api_key", "K")
    rows = [
        make_row(),
        make_row(LEAS_GUAR_WN="260000000"),
        make_row(AREA_DCD_NM="인천광역시", AREA_DTL_DCD_NM="인천 남동구"),
    ]
    calls: list[httpx.Request] = []
    with fake_client(rows, calls=calls) as client:
        out = fetch_hug_notices(client=client)

    assert calls[0].url.params["API_KEY"] == "K"  # 키는 설정에서만 온다
    assert [n.pblanc_no for n in out] == ["20260724-서울특별시", "20260724-인천광역시"]

    seoul = out[0]
    assert seoul.tot_suply_hshldco == 2
    assert seoul.area_nm == "서울"  # normalize_region (D17)
    assert seoul.house_nm == "HUG 든든전세주택 입주자 모집 (2026-07-24) 서울"
    assert seoul.agency == "HUG"
    assert seoul.bsns_mby_nm == "주택도시보증공사"
    assert seoul.house_dtl_secd_nm == "든든전세"
    assert seoul.pblanc_url == HUG_NOTICE_URL
    assert seoul.hsslpy_adres is None  # 읍면동까지만 있어 지오코딩 불가
    assert len(seoul.raw["_hug_items"]) == 2
    assert seoul.raw["_area"] == "서울특별시"
    assert seoul.raw["_hug_items"][0]["TMD_NM"] == "서울특별시 강북구 수유동"

    assert out[1].tot_suply_hshldco == 1
    assert out[1].area_nm == "인천"


# ── rent_gtn 은 그룹 최솟값, 전부 없으면 None ──
def test_rent_gtn_is_group_minimum():
    out = aggregate(
        items(
            [
                make_row(LEAS_GUAR_WN="260000000"),
                make_row(LEAS_GUAR_WN="180000000"),
                make_row(LEAS_GUAR_WN=""),  # 숫자 아님 → None → 후보에서 제외
            ]
        )
    )
    assert len(out) == 1
    assert out[0].rent_gtn == 180_000_000

    empty = aggregate(items([make_row(LEAS_GUAR_WN=""), make_row(LEAS_GUAR_WN=None)]))
    assert empty[0].rent_gtn is None


# ── house_secd_nm 은 최빈값, 동률이면 사전순 첫 값(결정적) ──
def test_house_secd_nm_is_mode_and_deterministic():
    rows = [
        make_row(PROP_KIND_CD2_NM="오피스텔"),
        make_row(PROP_KIND_CD2_NM="다세대주택"),
        make_row(PROP_KIND_CD2_NM="오피스텔"),
        make_row(PROP_KIND_CD2_NM="다세대주택"),
    ]
    first = aggregate(items(rows))
    second = aggregate(items(rows))
    reversed_order = aggregate(items(list(reversed(rows))))

    assert first[0].house_secd_nm == "다세대주택"  # 2:2 동률 → 사전순 첫 값
    assert second[0].house_secd_nm == first[0].house_secd_nm
    assert reversed_order[0].house_secd_nm == first[0].house_secd_nm

    # 동률이 아니면 최빈값
    mode = aggregate(items(rows + [make_row(PROP_KIND_CD2_NM="오피스텔")]))
    assert mode[0].house_secd_nm == "오피스텔"


# ── 접수일시 14자리 → 앞 8자리를 date 로 ──
def test_receipt_dates_parsed_from_14_digits():
    out = aggregate(items([make_row()]))
    assert out[0].rcrit_pblanc_de == date(2026, 7, 24)
    assert out[0].rcept_bgnde == date(2026, 7, 24)
    assert out[0].rcept_endde == date(2026, 8, 7)

    # 경계: 접수일시가 비어 있어도 크래시 없이 None
    blank = aggregate(items([make_row(SBSR_RCVI_SDT="", SBSR_RCVI_EDT=None)]))
    assert blank[0].rcept_bgnde is None
    assert blank[0].rcept_endde is None


# ── 파싱 실패 행은 스킵하고 나머지는 집계 (D9) ──
def test_bad_row_skipped(monkeypatch, caplog):
    monkeypatch.setattr(hug.settings, "hug_api_key", "K")
    rows = [{"AREA_DCD_NM": "서울특별시"}, make_row()]  # 첫 행에 COLL_ANNO_DT 없음
    with caplog.at_level(logging.WARNING, logger="src.collectors.hug"):
        with fake_client(rows) as client:
            out = fetch_hug_notices(client=client)

    assert len(out) == 1
    assert out[0].tot_suply_hshldco == 1
    assert any("파싱 실패" in r.message for r in caplog.records)


# ── 빈 응답 ──
def test_empty_response_returns_empty(monkeypatch):
    monkeypatch.setattr(hug.settings, "hug_api_key", "K")
    with fake_client([]) as client:
        assert fetch_hug_notices(client=client) == []


# ── 리스트가 아닌 응답(오류 봉투 등)은 경고 후 빈 리스트 ──
def test_non_list_response_returns_empty(monkeypatch, caplog):
    monkeypatch.setattr(hug.settings, "hug_api_key", "K")
    with caplog.at_level(logging.WARNING, logger="src.collectors.hug"):
        with fake_client({"error": "invalid key"}) as client:
            assert fetch_hug_notices(client=client) == []
    assert caplog.records


# ── 키 미설정이면 호출 자체를 하지 않는다 ──
def test_no_api_key_returns_empty_without_request(monkeypatch):
    monkeypatch.setattr(hug.settings, "hug_api_key", "")
    calls: list[httpx.Request] = []
    with fake_client([make_row()], calls=calls) as client:
        assert fetch_hug_notices(client=client) == []
    assert calls == []


# ── 경계: 응답이 상한(300)이면 누락 가능 경고 (D30) ──
def test_row_cap_warning(monkeypatch, caplog):
    monkeypatch.setattr(hug.settings, "hug_api_key", "K")
    rows = [make_row() for _ in range(ROW_CAP)]
    with caplog.at_level(logging.WARNING, logger="src.collectors.hug"):
        with fake_client(rows) as client:
            out = fetch_hug_notices(client=client)

    assert out[0].tot_suply_hshldco == ROW_CAP
    assert any("상한" in r.message for r in caplog.records)


# ── HTTP 오류는 그대로 올라간다(_safe() 가 소스 단위로 격리) ──
def test_http_error_raises(monkeypatch):
    monkeypatch.setattr(hug.settings, "hug_api_key", "K")
    with fake_client([], status=500) as client:
        with pytest.raises(httpx.HTTPStatusError):
            fetch_hug_notices(client=client)
