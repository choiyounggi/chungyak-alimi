"""북마크 회원별화 — 모델(복합 PK)/DB 함수/이관/엔드포인트 (postgres 필요, gated).

핵심 불변식: 한 회원의 북마크는 다른 회원에게 절대 보이지 않는다. 소유 판정은
요청이 보낸 값이 아니라 **세션의 member_id** 로 하고, 조회 술어를 쿼리 안에 둔다.
test_web.py와 동일하게 _db_available 게이트로 감싼다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from src.db import (
    Bookmark,
    MatchResult,
    Member,
    MemberProfile,
    Notice,
    NoticeHouseType,
    SessionLocal,
    add_bookmark,
    bookmarked_pblanc_nos,
    engine,
    init_db,
    is_bookmarked,
    migrate_global_bookmarks_to_member,
    remove_bookmark,
    save_match_results,
    upsert_house_types,
    upsert_notices,
)
from src.members import create_member, hash_password
from src.models import ApplyhomeHouseType, ApplyhomeNotice
from src.web.app import app, bookmarked_dashboard, matched_dashboard

from test_applyhome import SAMPLE
from test_auth_routes import BASE_URL, login_client
from test_housetype import SAMPLE_HT

EMAIL_A = "bookmark-a@example.com"
EMAIL_B = "bookmark-b@example.com"
PASSWORD = "pw-12345"


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


def _bookmark_pk_columns() -> set[str]:
    """현재 bookmark 테이블의 PK 컬럼 집합 — 복합 PK 재구성이 실제로 걸렸는지 본다."""
    with engine.connect() as c:
        rows = c.exec_driver_sql(
            "SELECT a.attname FROM pg_index i"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
            " WHERE i.indrelid = 'bookmark'::regclass AND i.indisprimary"
        ).fetchall()
    return {r[0] for r in rows}


def _wipe(session) -> None:
    for t in (Bookmark, MatchResult, NoticeHouseType, Notice, MemberProfile, Member):
        session.execute(delete(t))
    session.commit()


@pytest.fixture
def seeded():
    """공고 B1(매칭)/B2(미매칭) + 회원 A·B. 반환: (session, a_id, b_id)."""
    init_db()
    s = SessionLocal()
    _wipe(s)
    for pid in ("B1", "B2"):
        upsert_notices(
            [ApplyhomeNotice.model_validate({**SAMPLE, "PBLANC_NO": pid, "HOUSE_MANAGE_NO": pid})],
            session=s,
        )
        upsert_house_types(
            [ApplyhomeHouseType.model_validate({**SAMPLE_HT, "PBLANC_NO": pid})], session=s
        )
    save_match_results([("applyhome:B1", True, [])], session=s)  # B1만 매칭
    a = create_member(EMAIL_A, hash_password(PASSWORD), session=s).id
    b = create_member(EMAIL_B, hash_password(PASSWORD), session=s).id
    yield s, a, b
    _wipe(s)
    s.close()


# ── 정상: 추가 → 조회/판정이 회원 범위로만 보인다(격리) ──
def test_add_and_query_is_member_scoped(seeded):
    s, a, b = seeded
    add_bookmark(a, "applyhome:B1", session=s)
    assert bookmarked_pblanc_nos(a, session=s) == {"applyhome:B1"}
    assert bookmarked_pblanc_nos(b, session=s) == set()           # 회원 B에겐 안 보임
    assert is_bookmarked(a, "applyhome:B1", session=s) is True
    assert is_bookmarked(b, "applyhome:B1", session=s) is False   # 남의 북마크는 False
    assert is_bookmarked(a, "applyhome:B2", session=s) is False


# ── 정상: 같은 공고를 두 회원이 각자 북마크(복합 PK가 아니면 불가능) ──
def test_two_members_can_bookmark_same_notice(seeded):
    s, a, b = seeded
    add_bookmark(a, "applyhome:B1", session=s)
    add_bookmark(b, "applyhome:B1", session=s)
    assert bookmarked_pblanc_nos(a, session=s) == {"applyhome:B1"}
    assert bookmarked_pblanc_nos(b, session=s) == {"applyhome:B1"}
    assert _bookmark_pk_columns() == {"member_id", "pblanc_no"}
    # 한쪽이 해제해도 다른 쪽은 유지된다
    remove_bookmark(a, "applyhome:B1", session=s)
    assert bookmarked_pblanc_nos(a, session=s) == set()
    assert bookmarked_pblanc_nos(b, session=s) == {"applyhome:B1"}


# ── 경계값: 멱등(중복 추가/없는 것 삭제/남의 것 삭제) + 빈 상태 ──
def test_idempotent_and_empty(seeded):
    s, a, b = seeded
    assert bookmarked_pblanc_nos(a, session=s) == set()            # 빈 상태
    add_bookmark(a, "applyhome:B1", session=s)
    add_bookmark(a, "applyhome:B1", session=s)                     # 중복 추가 → 1건 유지
    assert bookmarked_pblanc_nos(a, session=s) == {"applyhome:B1"}
    remove_bookmark(b, "applyhome:B1", session=s)                  # 남의 북마크 삭제 시도 → 무효
    assert bookmarked_pblanc_nos(a, session=s) == {"applyhome:B1"}
    remove_bookmark(a, "applyhome:B1", session=s)
    remove_bookmark(a, "applyhome:B1", session=s)                  # 없는 것 삭제 → 에러 없음
    assert bookmarked_pblanc_nos(a, session=s) == set()


# ── 회원 삭제 → 그 회원 북마크도 함께 사라진다(ON DELETE CASCADE) ──
def test_member_delete_cascades_bookmarks(seeded):
    s, a, b = seeded
    add_bookmark(a, "applyhome:B1", session=s)
    add_bookmark(b, "applyhome:B1", session=s)
    s.execute(delete(MemberProfile).where(MemberProfile.member_id == a))
    s.execute(delete(Member).where(Member.id == a))
    s.commit()
    assert bookmarked_pblanc_nos(a, session=s) == set()
    assert bookmarked_pblanc_nos(b, session=s) == {"applyhome:B1"}  # 남의 것은 그대로


# ── 대시보드: matched_dashboard 아이템의 bookmarked 는 그 회원 기준 ──
def test_matched_dashboard_bookmarked_flag_is_member_scoped(seeded):
    s, a, b = seeded
    add_bookmark(a, "applyhome:B1", session=s)
    it = matched_dashboard(s, a)[0]
    assert it["notice"].pblanc_no == "applyhome:B1"
    assert it["bookmarked"] is True
    assert matched_dashboard(s, b)[0]["bookmarked"] is False        # 회원 B 시점
    remove_bookmark(a, "applyhome:B1", session=s)
    assert matched_dashboard(s, a)[0]["bookmarked"] is False


# ── 북마크 목록: 매칭 여부와 무관하게 그 회원이 북마크한 것만 ──
def test_bookmarked_dashboard_includes_unmatched(seeded):
    s, a, b = seeded
    add_bookmark(a, "applyhome:B2", session=s)   # B2는 매칭 안 됐지만 북마크됨
    items = bookmarked_dashboard(s, a)
    assert [it["notice"].pblanc_no for it in items] == ["applyhome:B2"]
    assert items[0]["bookmarked"] is True
    assert items[0]["my_rank"] is None            # 미매칭 → 순위 없음
    assert bookmarked_dashboard(s, b) == []       # 다른 회원 목록엔 안 나온다


# ── 북마크 목록: 최근 북마크가 먼저(created_at 내림차순) ──
def test_bookmarked_dashboard_recency_order(seeded):
    s, a, _b = seeded
    # 명시적 created_at으로 순서를 결정(동일 타임스탬프 플래키 방지): B1 과거, B2 최근
    now = datetime.now(timezone.utc)
    s.add(Bookmark(member_id=a, pblanc_no="applyhome:B1", created_at=now - timedelta(minutes=5)))
    s.add(Bookmark(member_id=a, pblanc_no="applyhome:B2", created_at=now))
    s.commit()
    nos = [it["notice"].pblanc_no for it in bookmarked_dashboard(s, a)]
    assert nos == ["applyhome:B2", "applyhome:B1"]  # 최근 북마크(B2)가 먼저


# ── 경계: 빈 상태 ──
def test_bookmarked_dashboard_empty(seeded):
    s, a, _b = seeded
    assert bookmarked_dashboard(s, a) == []


# ── 이관: 전역(member_id NULL) 행 → 대상 회원. 멱등 + 복합 PK 복구 ──
def test_migrate_global_bookmarks_to_member(seeded):
    _s, a, b = seeded
    try:
        # 전역 북마크 시절의 스키마/행을 재현한다(pblanc_no 단일 PK + member_id NULL).
        with engine.begin() as c:
            c.exec_driver_sql("ALTER TABLE bookmark DROP CONSTRAINT bookmark_pkey")
            c.exec_driver_sql("ALTER TABLE bookmark ALTER COLUMN member_id DROP NOT NULL")
            c.exec_driver_sql("ALTER TABLE bookmark ADD PRIMARY KEY (pblanc_no)")
            c.exec_driver_sql("INSERT INTO bookmark (pblanc_no) VALUES ('applyhome:B1')")

        assert migrate_global_bookmarks_to_member(a) == 1
        with SessionLocal() as s2:
            assert bookmarked_pblanc_nos(a, session=s2) == {"applyhome:B1"}
            assert bookmarked_pblanc_nos(b, session=s2) == set()
        assert _bookmark_pk_columns() == {"member_id", "pblanc_no"}  # 복합 PK 복구됨

        # 재호출 멱등 — 옮길 행이 없고 예외도 없다
        assert migrate_global_bookmarks_to_member(a) == 0
        with SessionLocal() as s2:
            assert bookmarked_pblanc_nos(a, session=s2) == {"applyhome:B1"}
    finally:
        with engine.begin() as c:
            c.exec_driver_sql("DELETE FROM bookmark")
        init_db()  # 스키마를 정상 상태로 되돌려 뒤 테스트에 영향 없게


# ── 경계: 이관할 게 없는(신규) DB 에서도 예외 없이 0건 ──
def test_migrate_on_clean_db_is_noop(seeded):
    _s, a, _b = seeded
    assert migrate_global_bookmarks_to_member(a) == 0


# ── 엔드포인트: 로그인 회원 기준으로 토글되고 다른 회원에겐 안 보인다 ──
def test_bookmark_endpoints_are_member_scoped(seeded):
    _s, a, b = seeded
    ca = login_client(EMAIL_A, PASSWORD)
    assert ca.put("/bookmark/applyhome:B1").json() == {"bookmarked": True}
    assert ca.put("/bookmark/applyhome:B1").status_code == 200          # 멱등
    with SessionLocal() as s2:
        assert is_bookmarked(a, "applyhome:B1", session=s2) is True
        assert is_bookmarked(b, "applyhome:B1", session=s2) is False

    cb = login_client(EMAIL_B, PASSWORD)
    assert SAMPLE["HOUSE_NM"] in ca.get("/bookmarks").text              # A에겐 노출
    assert SAMPLE["HOUSE_NM"] not in cb.get("/bookmarks").text          # B에겐 비노출
    # B가 해제를 시도해도 A의 북마크는 남는다(요청자 세션 기준으로만 지운다)
    assert cb.delete("/bookmark/applyhome:B1").json() == {"bookmarked": False}
    with SessionLocal() as s2:
        assert is_bookmarked(a, "applyhome:B1", session=s2) is True

    assert ca.delete("/bookmark/applyhome:B1").json() == {"bookmarked": False}
    with SessionLocal() as s2:
        assert is_bookmarked(a, "applyhome:B1", session=s2) is False


# ── 엔드포인트 인증: 미로그인 API → 401, 미로그인 페이지 → 303 ──
def test_bookmark_endpoints_require_login(seeded):
    c = TestClient(app, base_url=BASE_URL)
    assert c.put("/bookmark/applyhome:B1").status_code == 401
    assert c.delete("/bookmark/applyhome:B1").status_code == 401
    r = c.get("/bookmarks", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    with SessionLocal() as s2:                                          # 아무것도 저장되지 않음
        assert s2.scalar(select(func.count()).select_from(Bookmark)) == 0


# ── 엔드포인트 에러: 없는 공고 북마크 → 404(로그인 상태에서도) ──
def test_bookmark_put_missing_notice_404(seeded):
    assert login_client(EMAIL_A, PASSWORD).put("/bookmark/NOPE").status_code == 404


# ── /bookmarks 페이지: 북마크된 공고만 렌더 ──
def test_bookmarks_page_renders(seeded):
    _s, a, _b = seeded
    c = login_client(EMAIL_A, PASSWORD)
    with SessionLocal() as s2:
        add_bookmark(a, "applyhome:B1", session=s2)
    r = c.get("/bookmarks")
    assert r.status_code == 200
    assert SAMPLE["HOUSE_NM"] in r.text      # 북마크된 B1 노출
    assert "bookmark-btn is-on" in r.text    # on 상태
    with SessionLocal() as s2:
        remove_bookmark(a, "applyhome:B1", session=s2)
    assert SAMPLE["HOUSE_NM"] not in c.get("/bookmarks").text
