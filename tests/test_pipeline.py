from __future__ import annotations

import logging

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
    global_id,
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


def _seed_matched(pblanc_no: str = "P_ENRICH") -> str:
    """공고 1건을 매칭 상태로 심고 저장된 글로벌 ID를 반환."""
    init_db()
    gid = global_id("applyhome", pblanc_no)
    with SessionLocal() as s:
        for t in (NotifyLog, MatchResult, NoticeHouseType, Notice):
            s.execute(delete(t))
        s.commit()
        n = ApplyhomeNotice.model_validate({**SAMPLE, "PBLANC_NO": pblanc_no, "HOUSE_MANAGE_NO": pblanc_no})
        upsert_notices([n], source="applyhome", session=s)
        save_match_results([(gid, True, [])], session=s)
    return gid


def _cleanup():
    with SessionLocal() as s:
        for t in (NotifyLog, MatchResult, NoticeHouseType, Notice):
            s.execute(delete(t))
        s.commit()


# ── 로깅 설정: httpx 요청 URL(API 키 포함) INFO 로그 차단 ──
def test_configure_logging_silences_httpx():
    import logging

    httpx_logger = logging.getLogger("httpx")
    prev_level = httpx_logger.level
    try:
        pipeline.configure_logging()
        assert httpx_logger.level == logging.WARNING
        assert not httpx_logger.isEnabledFor(logging.INFO)  # 키 담긴 요청 URL 미출력
        assert httpx_logger.isEnabledFor(logging.WARNING)  # 에러·경고는 계속 보임
    finally:
        httpx_logger.setLevel(prev_level)


# ── 폴리곤 보강: 저장 + 재실행 skip + 없으면 빈배열 ──
def test_enrich_polygons_store_and_skip(monkeypatch):
    gid = _seed_matched("P_POLY")
    monkeypatch.setattr(pipeline.settings, "vworld_key", "K")
    calls = []

    def fake(addr, **kw):
        calls.append(addr)
        return [[127.0, 37.0], [127.1, 37.0], [127.1, 37.1], [127.0, 37.0]]

    monkeypatch.setattr(pipeline, "fetch_parcel_polygon", fake)
    got = pipeline.enrich_polygons()
    assert got == 1
    with SessionLocal() as s:
        raw = s.scalar(select(Notice.raw).where(Notice.pblanc_no == gid))
        assert raw["_polygon"] and len(raw["_polygon"]) == 4

    # 재실행 → 이미 _polygon 있어 조회 안 함(skip)
    calls.clear()
    pipeline.enrich_polygons()
    assert calls == []
    _cleanup()


def test_enrich_polygons_none_marks_empty(monkeypatch):
    gid = _seed_matched("P_POLY2")
    monkeypatch.setattr(pipeline.settings, "vworld_key", "K")
    monkeypatch.setattr(pipeline, "fetch_parcel_polygon", lambda addr, **kw: None)
    got = pipeline.enrich_polygons()
    assert got == 0
    with SessionLocal() as s:
        raw = s.scalar(select(Notice.raw).where(Notice.pblanc_no == gid))
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
    gid = global_id("lh", "LHD1")
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
        save_match_results([(gid, True, [])], session=s)

    calls = []

    def fake(**kw):
        calls.append(kw["pan_id"])
        return {
            "adres": "경기도 고양시 도내동", "schedule": [], "pan_dtl_cts": "공고내용",
            "mvin": None, "images": [], "files": [],
        }

    monkeypatch.setattr(pipeline, "fetch_lh_detail", fake)
    assert pipeline.enrich_lh_detail() == 1
    with SessionLocal() as s:
        n2 = s.scalar(select(Notice).where(Notice.pblanc_no == gid))
        assert n2.raw["_lh_detail"]["adres"] == "경기도 고양시 도내동"
        assert n2.hsslpy_adres == "경기도 고양시 도내동"  # 주소 컬럼도 갱신

    calls.clear()
    pipeline.enrich_lh_detail()  # 재실행 → 이미 _lh_detail(images 포함) 있어 skip
    assert calls == []

    # 구버전 _lh_detail(images 키 없음)은 1회 재보강된다 (이미지 갤러리 마이그레이션)
    with SessionLocal() as s:
        n3 = s.scalar(select(Notice).where(Notice.pblanc_no == gid))
        legacy = {k: v for k, v in n3.raw["_lh_detail"].items() if k not in ("images", "files")}
        s.execute(
            pipeline.update(Notice)
            .where(Notice.pblanc_no == gid)
            .values(raw={**n3.raw, "_lh_detail": legacy})
        )
        s.commit()
    calls.clear()
    assert pipeline.enrich_lh_detail() == 1
    assert calls == ["LHD1"]  # 보강 호출은 원본 PAN_ID(글로벌 ID 아님)

    # 뷰어 URL 세대(lhImageView 미해석 이미지)도 1회 재보강된다
    with SessionLocal() as s:
        n4 = s.scalar(select(Notice).where(Notice.pblanc_no == gid))
        viewer_era = {
            **n4.raw["_lh_detail"],
            "images": [{"label": "단지조감도", "name": "a.jpg",
                        "url": "https://apply.lh.or.kr/lhapply/lhImageView2.do?fileid=9"}],
        }
        s.execute(
            pipeline.update(Notice)
            .where(Notice.pblanc_no == gid)
            .values(raw={**n4.raw, "_lh_detail": viewer_era})
        )
        s.commit()
    calls.clear()
    assert pipeline.enrich_lh_detail() == 1
    assert calls == ["LHD1"]  # 보강 호출은 원본 PAN_ID(글로벌 ID 아님)
    _cleanup()


def _seed_lh_matched(native: str) -> str:
    """LH 공고 1건을 매칭 상태로 심고 저장된 글로벌 ID를 반환."""
    from src.collectors.lh import LhNotice

    init_db()
    gid = global_id("lh", native)
    with SessionLocal() as s:
        for t in (NotifyLog, MatchResult, NoticeHouseType, Notice):
            s.execute(delete(t))
        s.commit()
        n = LhNotice.model_validate({
            "PAN_ID": native, "PAN_NM": "테스트공고", "CNP_CD_NM": "경기도",
            "CLSG_DT": "2026.08.01", "CCR_CNNT_SYS_DS_CD": "03",
            "SPL_INF_TP_CD": "050", "UPP_AIS_TP_CD": "05", "AIS_TP_CD": "05",
        })
        upsert_notices([n], source="lh", session=s)
        save_match_results([(gid, True, [])], session=s)
    return gid


def _fake_supply(calls):
    """fetch_lh_supply 스텁 — 호출된 pan_id 를 기록하고 주택형 1건을 돌려준다."""
    from src.collectors.lh import LhSupply

    def fn(**kw):
        calls.append(kw["pan_id"])
        item = LhSupply.model_validate({"HTY_NNA": "059.9800", "SPL_AR": "59.98", "HSH_CNT": "10"})
        item.pblanc_no = kw["pan_id"]  # 실제 fetch_lh_supply 와 동일하게 원본 PAN_ID
        return [item]

    return fn


# ── LH 공급 보강: 원본 PAN_ID로 호출하고 주택형은 글로벌 ID 1회만 붙여 저장 ──
def test_enrich_lh_supply_uses_native_pan_id(monkeypatch):
    gid = _seed_lh_matched("LHS1")
    calls = []
    monkeypatch.setattr(pipeline, "fetch_lh_supply", _fake_supply(calls))

    assert pipeline.enrich_lh_supply() == 1
    assert calls == ["LHS1"]  # 글로벌 ID("lh:LHS1")를 LH API에 넘기면 안 됨
    with SessionLocal() as s:
        stored = s.scalars(select(NoticeHouseType.pblanc_no)).all()
        assert stored == [gid]  # 이중 접두("lh:lh:LHS1") 아님
    _cleanup()


# ── LH 공급 보강: native_id 가 비어 있으면 pblanc_no 로 폴백 (경계값) ──
def test_enrich_lh_supply_falls_back_without_native_id(monkeypatch):
    gid = _seed_lh_matched("LHS2")
    with SessionLocal() as s:
        s.execute(pipeline.update(Notice).where(Notice.pblanc_no == gid).values(native_id=None))
        s.commit()
    calls = []
    monkeypatch.setattr(pipeline, "fetch_lh_supply", _fake_supply(calls))

    assert pipeline.enrich_lh_supply() == 1
    assert calls == [gid]
    _cleanup()


# ── LH 공급 보강: 1건 실패는 그 공고만 건너뛴다 (에러) ──
def test_enrich_lh_supply_skips_failed_row(monkeypatch):
    _seed_lh_matched("LHS3")

    def boom(**kw):
        raise RuntimeError("LH 공급정보 500")

    monkeypatch.setattr(pipeline, "fetch_lh_supply", boom)
    assert pipeline.enrich_lh_supply() == 0
    with SessionLocal() as s:
        assert s.scalars(select(NoticeHouseType.pblanc_no)).all() == []
    _cleanup()


# ── run_batch: 6개 수집원 배선 ──
_SOURCE_FETCHERS = {
    "applyhome": "fetch_apt_notices",
    "lh": "fetch_lh_notices",
    "myhome": "fetch_myhome_notices",
    "hug": "fetch_hug_notices",
    "sh": "fetch_sh_notices",
    "gh": "fetch_gh_notices",
}

_COUNTS = {"applyhome": 3, "house_types": 5, "lh": 2, "myhome": 7, "hug": 4, "sh": 6, "gh": 1}


def _stub_batch(monkeypatch, *, counts, failing=()):
    """run_batch 의 외부 의존성을 전부 스텁한다. upsert 호출을 (source, 건수)로 기록해 반환."""

    def stub_fetch(name):
        def fn(*a, **kw):
            if name in failing:
                raise RuntimeError(f"{name} 수집 실패")
            return [object()] * counts[name]

        return fn

    for source, attr in _SOURCE_FETCHERS.items():
        monkeypatch.setattr(pipeline, attr, stub_fetch(source))
    monkeypatch.setattr(pipeline, "fetch_apt_house_types", stub_fetch("house_types"))

    upserted: list[tuple[str, int]] = []

    def fake_upsert_notices(items, *, source="applyhome", **kw):
        upserted.append((source, len(items)))

    def fake_upsert_house_types(items, *, source="applyhome", **kw):
        upserted.append((f"house_types:{source}", len(items)))
        return len(items)

    monkeypatch.setattr(pipeline, "upsert_notices", fake_upsert_notices)
    monkeypatch.setattr(pipeline, "upsert_house_types", fake_upsert_house_types)
    monkeypatch.setattr(pipeline, "init_db", lambda: None)
    monkeypatch.setattr(pipeline, "evaluate_all", lambda cfg: (11, 4))
    monkeypatch.setattr(pipeline, "enrich_lh_supply", lambda: 0)
    monkeypatch.setattr(pipeline, "enrich_lh_detail", lambda: 0)
    monkeypatch.setattr(pipeline, "enrich_polygons", lambda: 0)
    monkeypatch.setattr(pipeline, "notify_new_matches", lambda: 0)
    return upserted


def test_run_batch_counts_all_sources(monkeypatch):
    upserted = _stub_batch(monkeypatch, counts=_COUNTS)
    out = pipeline.run_batch(notify=False)

    assert out["collected"] == 3
    assert out["house_types"] == 5
    assert out["lh_notices"] == 2
    assert out["myhome_notices"] == 7
    assert out["hug_notices"] == 4
    assert out["sh_notices"] == 6
    assert out["gh_notices"] == 1
    # 소스별로 각자의 source 를 달고 저장된다(글로벌 ID 접두의 근거)
    assert upserted == [
        ("applyhome", 3),
        ("house_types:applyhome", 5),
        ("lh", 2),
        ("myhome", 7),
        ("hug", 4),
        ("sh", 6),
        ("gh", 1),
    ]


def test_one_collector_failure_does_not_stop_batch(monkeypatch):
    upserted = _stub_batch(monkeypatch, counts=_COUNTS, failing=("gh",))
    out = pipeline.run_batch(notify=False)

    assert out["gh_notices"] == 0  # 실패한 소스만 0
    assert out["collected"] == 3
    assert out["lh_notices"] == 2
    assert out["myhome_notices"] == 7
    assert out["hug_notices"] == 4
    assert out["sh_notices"] == 6
    assert ("gh", 0) in upserted  # 빈 리스트로도 배치는 계속 진행
    assert out["evaluated"] == 11 and out["matched"] == 4  # 평가 단계까지 도달


def test_existing_result_keys_preserved(monkeypatch):
    _stub_batch(monkeypatch, counts=dict.fromkeys(_COUNTS, 0))  # 전 소스 빈 응답(경계)
    out = pipeline.run_batch(notify=False)

    assert set(out) >= {
        "collected", "house_types", "lh_notices", "lh_enriched", "lh_detailed",
        "polygons", "evaluated", "matched", "sent",
    }
    assert out["collected"] == 0 and out["gh_notices"] == 0
    assert out["sent"] == 0


# ── 보안 회귀: API 키가 로그(traceback 포함)에 평문으로 남지 않는다 ──
# 2026-08-04 보안 리뷰 + 실물 재현: httpx.HTTPStatusError 메시지가 쿼리스트링을
# 통째로 담아 `_safe()` 의 logger.exception() 으로 저널에 찍혔다.
def _format(msg: str) -> str:
    fmt = pipeline.SecretRedactingFormatter("%(message)s")
    rec = logging.LogRecord("t", logging.ERROR, "p", 1, msg, None, None)
    return fmt.format(rec)


def test_formatter_redacts_api_keys(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "hug_api_key", "HUGSECRET")
    monkeypatch.setattr(pipeline.settings, "odcloud_api_key", "ODCSECRET")
    out = _format("GET https://x/y?API_KEY=HUGSECRET&serviceKey=ODCSECRET")
    assert "HUGSECRET" not in out
    assert "ODCSECRET" not in out
    assert out.count("***") == 2


def test_formatter_leaves_message_intact_when_secret_empty(monkeypatch):
    """빈 시크릿을 replace 하면 모든 문자 사이에 마스크가 끼어든다 — 경계 케이스."""
    for k in ("hug_api_key", "odcloud_api_key", "vworld_key"):
        monkeypatch.setattr(pipeline.settings, k, "")
    assert _format("정상 메시지") == "정상 메시지"


def test_safe_logs_collector_failure_without_secret(monkeypatch, caplog):
    monkeypatch.setattr(pipeline.settings, "hug_api_key", "CANARY123")

    def boom():
        raise RuntimeError("GET https://khug/x?API_KEY=CANARY123 failed")

    fmt = pipeline.SecretRedactingFormatter("%(message)s")
    with caplog.at_level(logging.ERROR):
        assert pipeline._safe(boom, "HUG", []) == []
    assert any("CANARY123" not in fmt.format(r) for r in caplog.records)
    assert all("CANARY123" not in fmt.format(r) for r in caplog.records)
