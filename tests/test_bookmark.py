"""북마크 DB 계층 — 모델/함수 + 대시보드 bookmarked 플래그 (postgres 필요, gated).

test_web.py와 동일하게 _db_available 게이트로 감싼다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from src.db import (
    Bookmark,
    MatchResult,
    Notice,
    NoticeHouseType,
    SessionLocal,
    add_bookmark,
    bookmarked_pblanc_nos,
    engine,
    init_db,
    is_bookmarked,
    remove_bookmark,
    save_match_results,
    upsert_house_types,
    upsert_notices,
)
from src.models import ApplyhomeHouseType, ApplyhomeNotice
from src.web.app import app, bookmarked_dashboard, matched_dashboard

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
    for t in (Bookmark, MatchResult, NoticeHouseType, Notice):
        s.execute(delete(t))
    s.commit()
    # 매칭 공고 B1(+주택형) / 매칭 안 된 공고 B2(북마크만 가능)
    for pid in ("B1", "B2"):
        upsert_notices(
            [ApplyhomeNotice.model_validate({**SAMPLE, "PBLANC_NO": pid, "HOUSE_MANAGE_NO": pid})],
            session=s,
        )
        upsert_house_types(
            [ApplyhomeHouseType.model_validate({**SAMPLE_HT, "PBLANC_NO": pid})], session=s
        )
    save_match_results([("B1", True, [])], session=s)  # B1만 매칭
    yield s
    for t in (Bookmark, MatchResult, NoticeHouseType, Notice):
        s.execute(delete(t))
    s.commit()
    s.close()


# ── 정상: 추가 → 조회/판정 ──
def test_add_and_query(seeded):
    add_bookmark("B1", session=seeded)
    assert bookmarked_pblanc_nos(session=seeded) == {"B1"}
    assert is_bookmarked("B1", session=seeded) is True
    assert is_bookmarked("B2", session=seeded) is False


# ── 경계값: 멱등(중복 추가/없는 것 삭제) + 빈 상태 ──
def test_idempotent_and_empty(seeded):
    assert bookmarked_pblanc_nos(session=seeded) == set()  # 빈 상태
    add_bookmark("B1", session=seeded)
    add_bookmark("B1", session=seeded)  # 중복 추가 → 에러 없이 1건 유지
    assert bookmarked_pblanc_nos(session=seeded) == {"B1"}
    remove_bookmark("B1", session=seeded)
    remove_bookmark("B1", session=seeded)  # 없는 것 삭제 → 에러 없음(멱등)
    assert bookmarked_pblanc_nos(session=seeded) == set()


# ── 대시보드: matched_dashboard 아이템에 bookmarked 반영 ──
def test_matched_dashboard_bookmarked_flag(seeded):
    add_bookmark("B1", session=seeded)
    it = matched_dashboard(seeded)[0]
    assert it["notice"].pblanc_no == "B1"
    assert it["bookmarked"] is True
    remove_bookmark("B1", session=seeded)
    assert matched_dashboard(seeded)[0]["bookmarked"] is False


# ── 북마크 목록: 매칭 여부와 무관하게 북마크된 것만 (경계: 미매칭 B2 포함) ──
def test_bookmarked_dashboard_includes_unmatched(seeded):
    add_bookmark("B2", session=seeded)  # B2는 매칭 안 됐지만 북마크됨
    items = bookmarked_dashboard(seeded)
    nos = [it["notice"].pblanc_no for it in items]
    assert nos == ["B2"]  # 북마크된 것만
    assert items[0]["bookmarked"] is True
    assert items[0]["my_rank"] is None  # 미매칭 → 순위 없음


# ── 북마크 목록: 최근 북마크가 먼저(created_at 내림차순) ──
def test_bookmarked_dashboard_recency_order(seeded):
    # 명시적 created_at으로 순서를 결정(동일 타임스탬프 플래키 방지): B1 과거, B2 최근
    now = datetime.now(timezone.utc)
    seeded.add(Bookmark(pblanc_no="B1", created_at=now - timedelta(minutes=5)))
    seeded.add(Bookmark(pblanc_no="B2", created_at=now))
    seeded.commit()
    nos = [it["notice"].pblanc_no for it in bookmarked_dashboard(seeded)]
    assert nos == ["B2", "B1"]  # 최근 북마크(B2)가 먼저


# ── 북마크 목록: 빈 상태 ──
def test_bookmarked_dashboard_empty(seeded):
    assert bookmarked_dashboard(seeded) == []


# ── 엔드포인트: PUT/DELETE 토글(멱등·JSON) ──
def test_bookmark_put_delete_endpoints(seeded):
    c = TestClient(app)
    assert c.put("/bookmark/B1").json() == {"bookmarked": True}
    with SessionLocal() as s2:
        assert is_bookmarked("B1", session=s2) is True
    # 멱등: 다시 PUT 해도 200
    assert c.put("/bookmark/B1").status_code == 200
    assert c.delete("/bookmark/B1").json() == {"bookmarked": False}
    with SessionLocal() as s2:
        assert is_bookmarked("B1", session=s2) is False


# ── 엔드포인트 에러: 없는 공고 북마크 → 404 ──
def test_bookmark_put_missing_notice_404(seeded):
    assert TestClient(app).put("/bookmark/NOPE").status_code == 404


# ── 엔드포인트 인증: 인증 켜졌는데 미로그인 → 401 ──
def test_bookmark_requires_auth(seeded, monkeypatch):
    from src.web import app as webapp

    monkeypatch.setattr(webapp.settings, "web_user", "me")
    monkeypatch.setattr(webapp.settings, "web_password", "pw")
    c = TestClient(app)
    assert c.put("/bookmark/B1").status_code == 401
    assert c.delete("/bookmark/B1").status_code == 401
    # /bookmarks 페이지는 미인증 시 로그인으로 리다이렉트
    assert c.get("/bookmarks", follow_redirects=False).status_code == 303


# ── /bookmarks 페이지: 북마크된 공고만 렌더 ──
def test_bookmarks_page_renders(seeded):
    add_bookmark("B1", session=seeded)
    r = TestClient(app).get("/bookmarks")
    assert r.status_code == 200
    assert SAMPLE["HOUSE_NM"] in r.text     # 북마크된 B1 노출
    assert "bookmark-btn is-on" in r.text   # on 상태
    # 북마크 안 한 공고는 미노출
    remove_bookmark("B1", session=seeded)
    r2 = TestClient(app).get("/bookmarks")
    assert SAMPLE["HOUSE_NM"] not in r2.text
