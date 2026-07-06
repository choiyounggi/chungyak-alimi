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
from src.web.app import app, matched_dashboard

from test_applyhome import SAMPLE
from test_housetype import SAMPLE_HT


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


@pytest.fixture
def seeded():
    init_db()
    s = SessionLocal()
    for t in (MatchResult, NoticeHouseType, Notice):
        s.execute(delete(t))
    s.commit()
    # 매칭 공고 1건 + 주택형 + match_result
    n = ApplyhomeNotice.model_validate({**SAMPLE, "PBLANC_NO": "W1", "HOUSE_MANAGE_NO": "W1"})
    ht = ApplyhomeHouseType.model_validate({**SAMPLE_HT, "PBLANC_NO": "W1"})
    upsert_notices([n], session=s)
    upsert_house_types([ht], session=s)
    save_match_results([("W1", True, [])], session=s)
    yield s
    for t in (MatchResult, NoticeHouseType, Notice):
        s.execute(delete(t))
    s.commit()
    s.close()


# ── 대시보드 데이터: 매칭 공고 + 분양가 계산 ──
def test_matched_dashboard(seeded):
    items = matched_dashboard(seeded)
    assert len(items) == 1
    it = items[0]
    assert it["notice"].pblanc_no == "W1"
    assert it["price_lo"] == 50724  # SAMPLE_HT LTTOT_TOP_AMOUNT


# ── 인덱스 렌더: 200 + 공고명 포함 ──
def test_index_renders(seeded):
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert SAMPLE["HOUSE_NM"] in r.text


# ── 상세 페이지: 렌더 + 주택형/특공 표시 ──
def test_detail_renders(seeded):
    client = TestClient(app)
    r = client.get("/notice/W1")
    assert r.status_code == 200
    assert SAMPLE["HOUSE_NM"] in r.text
    assert SAMPLE_HT["HOUSE_TY"] in r.text  # 주택형(055.9200A)
    assert "주택형별 모집" in r.text
    assert "자격요건" in r.text  # 공고문 안내 섹션


# ── 상세: 없는 공고 404 ──
def test_detail_not_found():
    assert TestClient(app).get("/notice/NOPE").status_code == 404


# ── healthz (인증 불필요) ──
def test_healthz():
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── 인증: 세션 로그인 플로우(미인증→로그인, 로그인→대시보드) ──
def test_login_flow(seeded, monkeypatch):
    from src.web import app as webapp

    monkeypatch.setattr(webapp.settings, "web_user", "me")
    monkeypatch.setattr(webapp.settings, "web_password", "pw")
    client = TestClient(app)

    # 미인증 접근 → 로그인 페이지로 리다이렉트
    r = client.get("/")
    assert r.status_code == 200
    assert "로그인" in r.text and "비밀번호" in r.text

    # 틀린 비번 → 401 + 폼 에러 문구
    bad = client.post("/login", data={"username": "me", "password": "wrong"})
    assert bad.status_code == 401
    assert "아이디 또는 비밀번호가 올바르지 않습니다" in bad.text

    # 아이디 미입력 → 아이디 필드 에러
    e1 = client.post("/login", data={"username": "", "password": "pw"})
    assert e1.status_code == 401
    assert "아이디를 입력해주세요" in e1.text

    # 비밀번호 미입력 → 비번 필드 에러
    e2 = client.post("/login", data={"username": "me", "password": ""})
    assert e2.status_code == 401
    assert "비밀번호를 입력해주세요" in e2.text

    # 올바른 로그인 → 세션 → 대시보드 접근
    ok = client.post("/login", data={"username": "me", "password": "pw"})
    assert ok.status_code == 200
    assert "내 관심 청약" in ok.text

    # 로그아웃 → 다시 로그인 페이지
    out = client.get("/logout")
    assert "로그인" in out.text


# ── 상세: 카카오 지도(키 설정 시 렌더, 없으면 미렌더) ──
def test_detail_map(seeded, monkeypatch):
    from src.web import app as webapp

    monkeypatch.setattr(webapp.settings, "kakao_js_key", "TESTKAKAOKEY")
    r = TestClient(app).get("/notice/W1")
    assert 'id="map"' in r.text
    assert "dapi.kakao.com" in r.text
    assert "TESTKAKAOKEY" in r.text
    # 로드 실패 fallback: sdk onerror + 엔진 부분실패 가드 + 안내 문구
    assert 'onerror="mapLoadFailed()"' in r.text
    assert "지도를 불러오지 못했어요" in r.text
    assert "새로고침" in r.text


def test_detail_no_map_without_key(seeded, monkeypatch):
    from src.web import app as webapp

    monkeypatch.setattr(webapp.settings, "kakao_js_key", "")
    r = TestClient(app).get("/notice/W1")
    assert 'id="map"' not in r.text
