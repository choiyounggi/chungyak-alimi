from __future__ import annotations

import copy

import pytest
from sqlalchemy import delete, select

from src.db import Notice, SessionLocal, engine, init_db, upsert_notices
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
    s.commit()
    yield s
    s.execute(delete(Notice))
    s.commit()
    s.close()


# ── 정상: 신규 insert ──
def test_insert_new(session):
    res = upsert_notices([_notice("A1"), _notice("A2")], session=session)
    assert res.new_count == 2
    assert res.updated_count == 0
    assert session.scalar(select(Notice).where(Notice.pblanc_no == "A1")).area_nm == "경기"


# ── 경계: 재실행해도 중복 insert 없음(upsert) + 신규감지 ──
def test_upsert_idempotent_and_new_detection(session):
    upsert_notices([_notice("A1"), _notice("A2")], session=session)
    res = upsert_notices([_notice("A1"), _notice("A2"), _notice("A3")], session=session)
    assert res.new == ["A3"]              # A3만 신규
    assert set(res.updated) == {"A1", "A2"}
    total = len(list(session.execute(select(Notice.pblanc_no))))
    assert total == 3                     # 중복 없이 3건


# ── 신규감지 핵심: first_seen_at 보존, 값은 갱신 ──
def test_first_seen_preserved_on_update(session):
    upsert_notices([_notice("A1", HOUSE_NM="원래이름")], session=session)
    first = session.scalar(select(Notice).where(Notice.pblanc_no == "A1"))
    seen0 = first.first_seen_at
    session.expire_all()
    upsert_notices([_notice("A1", HOUSE_NM="바뀐이름")], session=session)
    after = session.scalar(select(Notice).where(Notice.pblanc_no == "A1"))
    assert after.first_seen_at == seen0       # 최초 발견시각 보존
    assert after.house_nm == "바뀐이름"        # 값은 갱신됨


# ── 경계: 빈 입력은 no-op ──
def test_empty_noop(session):
    res = upsert_notices([], session=session)
    assert res.new_count == 0 and res.updated_count == 0
    assert len(list(session.execute(select(Notice.pblanc_no)))) == 0
