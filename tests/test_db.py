from __future__ import annotations

import copy

import pytest
from sqlalchemy import delete, insert, select

from src.db import (
    Bookmark,
    Notice,
    SessionLocal,
    engine,
    global_id,
    init_db,
    migrate_global_ids,
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


def _notice(pblanc_no: str, **over) -> ApplyhomeNotice:
    d = copy.deepcopy(SAMPLE)
    d["PBLANC_NO"] = pblanc_no
    d["HOUSE_MANAGE_NO"] = pblanc_no
    d.update(over)
    return ApplyhomeNotice.model_validate(d)


@pytest.fixture
def session():
    init_db()
    s = SessionLocal()
    s.execute(delete(Notice))  # 테스트 격리: 테이블 비우기
    s.execute(delete(Bookmark))  # 이관 테스트가 자식 행을 직접 넣으므로 함께 비운다
    s.commit()
    yield s
    s.execute(delete(Notice))
    s.execute(delete(Bookmark))
    s.commit()
    s.close()


def _insert_native(session, pblanc_no: str, source: str = "applyhome") -> None:
    """이관 전 상태(접두 없는 native ID)의 notice 행을 직접 INSERT."""
    session.execute(
        insert(Notice).values(
            pblanc_no=pblanc_no, source=source, house_nm="이관테스트", raw={}
        )
    )


# ── 정상: 신규 insert ──
def test_insert_new(session):
    res = upsert_notices([_notice("A1"), _notice("A2")], session=session)
    assert res.new_count == 2
    assert res.updated_count == 0
    assert (
        session.scalar(select(Notice).where(Notice.pblanc_no == "applyhome:A1")).area_nm
        == "경기"
    )


# ── 경계: 재실행해도 중복 insert 없음(upsert) + 신규감지 ──
def test_upsert_idempotent_and_new_detection(session):
    upsert_notices([_notice("A1"), _notice("A2")], session=session)
    res = upsert_notices([_notice("A1"), _notice("A2"), _notice("A3")], session=session)
    assert res.new == ["applyhome:A3"]              # A3만 신규
    assert set(res.updated) == {"applyhome:A1", "applyhome:A2"}
    total = len(list(session.execute(select(Notice.pblanc_no))))
    assert total == 3                     # 중복 없이 3건


# ── 신규감지 핵심: first_seen_at 보존, 값은 갱신 ──
def test_first_seen_preserved_on_update(session):
    upsert_notices([_notice("A1", HOUSE_NM="원래이름")], session=session)
    first = session.scalar(select(Notice).where(Notice.pblanc_no == "applyhome:A1"))
    seen0 = first.first_seen_at
    session.expire_all()
    upsert_notices([_notice("A1", HOUSE_NM="바뀐이름")], session=session)
    after = session.scalar(select(Notice).where(Notice.pblanc_no == "applyhome:A1"))
    assert after.first_seen_at == seen0       # 최초 발견시각 보존
    assert after.house_nm == "바뀐이름"        # 값은 갱신됨


# ── 경계: 빈 입력은 no-op ──
def test_empty_noop(session):
    res = upsert_notices([], session=session)
    assert res.new_count == 0 and res.updated_count == 0
    assert len(list(session.execute(select(Notice.pblanc_no)))) == 0


# ── 정상: upsert 는 글로벌 ID로 저장하고 원본 ID·기관을 채운다 ──
def test_upsert_notices_uses_global_id(session):
    upsert_notices([_notice("X1")], source="applyhome", session=session)
    n = session.scalar(select(Notice).where(Notice.pblanc_no == "applyhome:X1"))
    assert n is not None
    assert n.native_id == "X1"
    assert n.agency == "기타"


# ── 경계: 이미 접두된 ID에는 접두사를 다시 붙이지 않는다 ──
def test_global_id_is_idempotent():
    assert global_id("lh", "lh:9") == "lh:9"
    assert global_id("lh", "9") == "lh:9"


# ── 정상: 이관이 notice 와 자식 행을 함께 옮긴다(북마크 유지) ──
def test_migrate_moves_child_rows(session):
    _insert_native(session, "M1")
    session.execute(insert(Bookmark).values(pblanc_no="M1"))
    session.commit()

    counts = migrate_global_ids()

    assert counts["notice"] == 1
    assert counts["bookmark"] == 1
    session.expire_all()
    assert session.scalar(select(Notice.pblanc_no)) == "applyhome:M1"
    assert session.scalar(select(Bookmark.pblanc_no)) == "applyhome:M1"


# ── 경계: 두 번 실행해도 결과가 같다(멱등) ──
def test_migrate_twice_is_noop(session):
    _insert_native(session, "M2")
    session.execute(insert(Bookmark).values(pblanc_no="M2"))
    session.commit()
    migrate_global_ids()

    counts = migrate_global_ids()

    assert counts["notice"] == 0
    assert counts["bookmark"] == 0
    session.expire_all()
    assert session.scalar(select(Notice.pblanc_no)) == "applyhome:M2"
    assert session.scalar(select(Bookmark.pblanc_no)) == "applyhome:M2"


# ── 에러/경계: notice 에 짝이 없는 고아 자식 행은 그대로 남는다(예외 없음) ──
def test_migrate_keeps_orphan_child(session):
    session.execute(insert(Bookmark).values(pblanc_no="ORPHAN"))
    session.commit()

    counts = migrate_global_ids()

    assert counts["bookmark"] == 0
    session.expire_all()
    assert session.scalar(select(Bookmark.pblanc_no)) == "ORPHAN"


# ── 정상: 수집원별 기본 기관이 실제로 컬럼에 저장된다(웹 기관 필터가 이 값에 의존) ──
@pytest.mark.parametrize(
    "source, expected", [("lh", "LH"), ("hug", "HUG"), ("sh", "SH"), ("gh", "GH")]
)
def test_agency_filled_per_source(session, source, expected):
    upsert_notices([_notice("AG1")], source=source, session=session)
    n = session.scalar(select(Notice).where(Notice.pblanc_no == f"{source}:AG1"))
    assert n.agency == expected
    assert n.native_id == "AG1"


# ── 경계: 모델이 agency 를 직접 실으면 소스 기본값보다 우선한다(D8, 마이홈) ──
def test_model_agency_overrides_source_default(session):
    from src.collectors.myhome import MyhomeNotice

    n = MyhomeNotice.model_validate(
        {"pblancId": "1", "houseSn": 0, "pblancNm": "x", "brtcNm": "경기도",
         "suplyInsttNm": "LH", "rentGtn": 10800000, "mtRntchrg": 54540, "_kind": "rent"}
    )
    upsert_notices([n], source="myhome", session=session)
    row = session.scalar(select(Notice).where(Notice.pblanc_no == "myhome:1-0"))
    assert row.agency == "LH"          # AGENCY_BY_SOURCE 에 myhome 이 없어도 모델 값이 이긴다
    assert row.rent_gtn == 10800000    # 임대료 컬럼 DB 왕복
    assert row.mt_rntchrg == 54540


# ── 에러/경계: 원본 ID 에 콜론이 있어도 소스 접두사는 붙는다(소스 간 PK 충돌 방지) ──
def test_global_id_prefixes_even_when_native_contains_colon():
    assert global_id("gh", "LH:001") == "gh:LH:001"
    assert global_id("lh", "LH:001") == "lh:LH:001"
    assert global_id("lh", "lh:9") == "lh:9"  # 자기 접두사는 중복하지 않는다
