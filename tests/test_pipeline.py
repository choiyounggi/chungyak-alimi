from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from src import pipeline
from src.db import (
    MatchResult,
    Notice,
    NoticeHouseType,
    NotifyLog,
    SessionLocal,
    engine,
    init_db,
    save_match_results,
    upsert_notices,
)
from src.models import ApplyhomeNotice

from test_applyhome import SAMPLE


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


def _seed_matched(pblanc_no: str = "P_ENRICH"):
    init_db()
    with SessionLocal() as s:
        for t in (NotifyLog, MatchResult, NoticeHouseType, Notice):
            s.execute(delete(t))
        s.commit()
        n = ApplyhomeNotice.model_validate({**SAMPLE, "PBLANC_NO": pblanc_no, "HOUSE_MANAGE_NO": pblanc_no})
        upsert_notices([n], source="applyhome", session=s)
        save_match_results([(pblanc_no, True, [])], session=s)


def _cleanup():
    with SessionLocal() as s:
        for t in (NotifyLog, MatchResult, NoticeHouseType, Notice):
            s.execute(delete(t))
        s.commit()


# ── 폴리곤 보강: 저장 + 재실행 skip + 없으면 빈배열 ──
def test_enrich_polygons_store_and_skip(monkeypatch):
    _seed_matched("P_POLY")
    monkeypatch.setattr(pipeline.settings, "vworld_key", "K")
    calls = []

    def fake(addr, **kw):
        calls.append(addr)
        return [[127.0, 37.0], [127.1, 37.0], [127.1, 37.1], [127.0, 37.0]]

    monkeypatch.setattr(pipeline, "fetch_parcel_polygon", fake)
    got = pipeline.enrich_polygons()
    assert got == 1
    with SessionLocal() as s:
        raw = s.scalar(select(Notice.raw).where(Notice.pblanc_no == "P_POLY"))
        assert raw["_polygon"] and len(raw["_polygon"]) == 4

    # 재실행 → 이미 _polygon 있어 조회 안 함(skip)
    calls.clear()
    pipeline.enrich_polygons()
    assert calls == []
    _cleanup()


def test_enrich_polygons_none_marks_empty(monkeypatch):
    _seed_matched("P_POLY2")
    monkeypatch.setattr(pipeline.settings, "vworld_key", "K")
    monkeypatch.setattr(pipeline, "fetch_parcel_polygon", lambda addr, **kw: None)
    got = pipeline.enrich_polygons()
    assert got == 0
    with SessionLocal() as s:
        raw = s.scalar(select(Notice.raw).where(Notice.pblanc_no == "P_POLY2"))
        assert raw["_polygon"] == []  # 조회했으나 없음(재조회 방지 sentinel)
    _cleanup()


# ── 폴리곤 보강: vworld_key 없으면 0 ──
def test_enrich_polygons_no_key(monkeypatch):
    _seed_matched("P_POLY3")
    monkeypatch.setattr(pipeline.settings, "vworld_key", "")
    assert pipeline.enrich_polygons() == 0
    _cleanup()


# ── LH 상세 보강: raw 병합 + 주소 컬럼 갱신 + 재실행 skip ──
def test_enrich_lh_detail(monkeypatch):
    from src.collectors.lh import LhNotice

    init_db()
    with SessionLocal() as s:
        for t in (NotifyLog, MatchResult, NoticeHouseType, Notice):
            s.execute(delete(t))
        s.commit()
        n = LhNotice.model_validate({
            "PAN_ID": "LHD1", "PAN_NM": "테스트공고", "CNP_CD_NM": "경기도",
            "CLSG_DT": "2026.08.01", "CCR_CNNT_SYS_DS_CD": "03",
            "SPL_INF_TP_CD": "050", "UPP_AIS_TP_CD": "05", "AIS_TP_CD": "05",
        })
        upsert_notices([n], source="lh", session=s)
        save_match_results([("LHD1", True, [])], session=s)

    calls = []

    def fake(**kw):
        calls.append(kw["pan_id"])
        return {"adres": "경기도 고양시 도내동", "schedule": [], "pan_dtl_cts": "공고내용", "mvin": None}

    monkeypatch.setattr(pipeline, "fetch_lh_detail", fake)
    assert pipeline.enrich_lh_detail() == 1
    with SessionLocal() as s:
        n2 = s.scalar(select(Notice).where(Notice.pblanc_no == "LHD1"))
        assert n2.raw["_lh_detail"]["adres"] == "경기도 고양시 도내동"
        assert n2.hsslpy_adres == "경기도 고양시 도내동"  # 주소 컬럼도 갱신

    calls.clear()
    pipeline.enrich_lh_detail()  # 재실행 → 이미 _lh_detail 있어 skip
    assert calls == []
    _cleanup()
