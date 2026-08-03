"""지도 대시보드 서버측 좌표 — 폴리곤 중심좌표(순수) + matched_dashboard 배선(gated).

_polygon_centroid는 순수 함수라 계산 자체는 DB 없이 검증한다(정상/경계/에러). matched_dashboard의
lat/lng 채움과 index 라우트 회귀는 postgres 필요 → test_web.py와 동일하게 _db_available 게이트로
감싼다. (src.web.app import 자체가 init_db로 DB를 요구하므로 test_web.py와 동일 전제.)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from src.db import (
    MatchResult,
    Notice,
    NoticeHouseType,
    SessionLocal,
    engine,
    init_db,
    save_match_results,
    upsert_house_types,
    upsert_notices,
)
from src.models import ApplyhomeHouseType, ApplyhomeNotice
from src.web.app import _polygon_centroid, app, matched_dashboard

from test_applyhome import SAMPLE
from test_housetype import SAMPLE_HT


# ── 순수: 폴리곤 중심좌표(위,경도) ──────────────────────────────
def test_centroid_normal():
    poly = [[126.70, 37.60], [126.72, 37.60], [126.72, 37.62], [126.70, 37.62]]
    lat, lng = _polygon_centroid(poly)
    assert lat == pytest.approx(37.61)
    assert lng == pytest.approx(126.71)


def test_centroid_single_point():
    assert _polygon_centroid([[126.70, 37.60]]) == pytest.approx((37.60, 126.70))


def test_centroid_boundary_empty_and_none():
    assert _polygon_centroid(None) is None
    assert _polygon_centroid([]) is None
    assert _polygon_centroid("nope") is None  # 형식 이상(list 아님)


def test_centroid_error_bad_points():
    # 유효 좌표가 하나도 없으면 None (문자열 좌표 / 길이 부족)
    assert _polygon_centroid([["x", "y"], [126.7]]) is None
    # 일부만 유효하면 유효분으로 평균
    assert _polygon_centroid([["x", "y"], [126.70, 37.60]]) == pytest.approx((37.60, 126.70))


# ── gated: matched_dashboard lat/lng + index 회귀 ──────────────
def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


gated = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


@pytest.fixture
def clean_db():
    init_db()
    s = SessionLocal()
    for t in (MatchResult, NoticeHouseType, Notice):
        s.execute(delete(t))
    s.commit()
    yield s
    for t in (MatchResult, NoticeHouseType, Notice):
        s.execute(delete(t))
    s.commit()
    s.close()


@gated
def test_matched_dashboard_lat_lng_with_and_without_polygon(clean_db):
    s = clean_db
    poly = [[126.70, 37.60], [126.72, 37.60], [126.72, 37.62], [126.70, 37.62]]
    # 폴리곤 있는 공고 P1 / 없는 공고 P2
    n1 = ApplyhomeNotice.model_validate(
        {**SAMPLE, "PBLANC_NO": "P1", "HOUSE_MANAGE_NO": "P1", "_polygon": poly}
    )
    n2 = ApplyhomeNotice.model_validate(
        {**SAMPLE, "PBLANC_NO": "P2", "HOUSE_MANAGE_NO": "P2"}
    )
    upsert_notices([n1, n2], session=s)
    upsert_house_types(
        [
            ApplyhomeHouseType.model_validate({**SAMPLE_HT, "PBLANC_NO": "P1"}),
            ApplyhomeHouseType.model_validate({**SAMPLE_HT, "PBLANC_NO": "P2"}),
        ],
        session=s,
    )
    save_match_results([("P1", True, []), ("P2", True, [])], session=s)

    by_no = {it["notice"].pblanc_no: it for it in matched_dashboard(s)}
    assert by_no["P1"]["lat"] == pytest.approx(37.61)
    assert by_no["P1"]["lng"] == pytest.approx(126.71)
    assert by_no["P2"]["lat"] is None  # 폴리곤 없음 → None (경계값)
    assert by_no["P2"]["lng"] is None


@gated
def test_index_route_still_ok_with_kakao_key(clean_db, monkeypatch):
    """index 라우트가 kakao_key 컨텍스트를 받아도 200 렌더(회귀 가드).

    kakao_key의 실제 HTML 주입(카카오 SDK)은 test_index_template.py에서 검증한다."""
    from src.web import app as webapp

    monkeypatch.setattr(webapp.settings, "kakao_js_key", "TESTKAKAOKEY")
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "내 관심 청약" in r.text
