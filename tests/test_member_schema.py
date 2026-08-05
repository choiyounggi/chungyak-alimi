"""member / member_profile 스키마 (Task 01) — postgres 필요, _db_available 게이트.

test_bookmark.py와 동일한 게이트 패턴.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from src.db import Member, MemberProfile, SessionLocal, engine, init_db


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


@pytest.fixture
def session():
    init_db()
    s = SessionLocal()
    for t in (MemberProfile, Member):
        s.execute(delete(t))
    s.commit()
    yield s
    for t in (MemberProfile, Member):
        s.execute(delete(t))
    s.commit()
    s.close()


# ── 경계: init_db 멱등(2회 호출해도 예외 없음) ──
def test_init_db_idempotent():
    init_db()
    init_db()  # 재호출해도 예외 없이 스키마가 준비돼야 함
    # 두 테이블 모두 쿼리 가능해야 함 = 스키마가 실제로 생성됨(테이블 없으면 예외)
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Member)) >= 0
        assert s.scalar(select(func.count()).select_from(MemberProfile)) >= 0


# ── 정상: Member + MemberProfile 생성/조회 왕복 ──
def test_member_profile_roundtrip(session):
    m = Member(email="a@example.com", password_hash="x")
    session.add(m)
    session.flush()  # id 확보
    assert m.id is not None
    assert m.created_at is not None
    session.add(
        MemberProfile(
            member_id=m.id,
            birth_date=date(1994, 5, 1),
            household_type="pre_newlywed",
            residence_regions=["경기"],
            income_base_regions=["서울", "성남"],
            interest_regions=["서울"],
            car_value_manwon=2000,
            household_head_owns_home=True,
            is_first_home=True,
        )
    )
    session.commit()

    got = session.get(MemberProfile, m.id)
    assert got.household_type == "pre_newlywed"
    assert got.residence_regions == ["경기"]
    assert got.income_base_regions == ["서울", "성남"]
    assert got.car_value_manwon == 2000
    assert got.household_head_owns_home is True
    assert got.is_first_home is True


# ── 경계: 지역 목록/불리언 기본값 ──
def test_defaults(session):
    m = Member(email="b@example.com", password_hash="x")
    session.add(m)
    session.flush()
    session.add(MemberProfile(member_id=m.id))
    session.commit()

    got = session.get(MemberProfile, m.id)
    assert got.residence_regions == []
    assert got.income_base_regions == []
    assert got.interest_regions == []
    assert got.household_type == "general"
    assert got.household_all_homeless is True  # 기본 무주택
    assert got.is_household_head is False
    assert got.dependents == 0


# ── 에러: household_type 허용 외 값 → CHECK 위반(IntegrityError) ──
def test_household_type_check(session):
    m = Member(email="c@example.com", password_hash="x")
    session.add(m)
    session.flush()
    session.add(MemberProfile(member_id=m.id, household_type="invalid"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ── 에러: email UNIQUE 위반 ──
def test_email_unique(session):
    session.add(Member(email="dup@example.com", password_hash="x"))
    session.commit()
    session.add(Member(email="dup@example.com", password_hash="y"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
