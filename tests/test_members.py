"""member 데이터액세스 + Profile 어댑터 (Task 02) — postgres 필요, _db_available 게이트."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete

from src.db import Member, MemberProfile, SessionLocal, engine, init_db
from src.members import (
    create_member,
    get_member_by_email,
    get_profile,
    profile_from_member,
    update_profile,
)
from src.scoring import AccountInfo, FirstLifeInfo, IncomeInfo, Profile


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


# ── 정상: create → email 소문자 정규화로 조회, 빈 프로필 동반 생성 ──
def test_create_and_get_lowercase(session):
    m = create_member("A@Example.com ", "hash1", session=session)
    assert m.id is not None
    # 대소문자/공백 달라도 정규화되어 조회됨
    got = get_member_by_email("a@example.com", session=session)
    assert got is not None
    assert got.id == m.id
    assert got.email == "a@example.com"
    # 빈 MemberProfile 동반 생성
    assert get_profile(m.id, session=session) is not None


# ── 정상: profile_from_member 중첩(AccountInfo/IncomeInfo/FirstLifeInfo) 변환 ──
def test_profile_from_member_nested(session):
    m = create_member("b@example.com", "h", session=session)
    update_profile(
        m.id,
        {
            "birth_date": date(1993, 3, 3),
            "region": "서울",
            "dependents": 2,
            "real_estate_manwon": 12000,
            "account_opened": date(2015, 1, 1),
            "account_balance_manwon": 1000,
            "income_monthly_manwon": 500,
            "income_base_manwon": 600,
            "income_dual": True,
            "fl_ever_owned_house": False,
            "fl_income_tax_5y": True,
            "fl_currently_earning": True,
        },
        session=session,
    )
    p = profile_from_member(get_profile(m.id, session=session))
    assert isinstance(p, Profile)
    assert p.birth_date == date(1993, 3, 3)
    assert p.region == "서울"
    assert p.dependents == 2
    assert p.real_estate_manwon == 12000
    assert isinstance(p.account, AccountInfo)
    assert p.account.opened == date(2015, 1, 1)
    assert p.account.balance_manwon == 1000
    assert isinstance(p.income, IncomeInfo)
    assert p.income.monthly_manwon == 500
    assert p.income.base_manwon == 600
    assert p.income.dual_income is True
    assert isinstance(p.first_life, FirstLifeInfo)
    assert p.first_life.income_tax_5y is True
    assert p.first_life.currently_earning is True


# ── 정상: update_profile 지역 목록 강제 문자열화 ──
def test_update_profile_coerces_regions(session):
    m = create_member("c@example.com", "h", session=session)
    update_profile(
        m.id,
        {"residence_regions": ["경기", 123], "income_base_regions": ["서울"]},
        session=session,
    )
    prof = get_profile(m.id, session=session)
    assert prof.residence_regions == ["경기", "123"]  # 문자열화
    assert prof.income_base_regions == ["서울"]


# ── 에러: 중복 email → ValueError ──
def test_create_duplicate_raises(session):
    create_member("dup@example.com", "h", session=session)
    with pytest.raises(ValueError):
        create_member("DUP@example.com", "h2", session=session)  # 정규화 후 동일


# ── 경계: 빈(기본값) 프로필도 예외 없이 Profile 로 변환 ──
def test_empty_profile_converts(session):
    m = create_member("d@example.com", "h", session=session)
    p = profile_from_member(get_profile(m.id, session=session))
    assert isinstance(p, Profile)
    assert p.region == ""
    assert p.account.balance_manwon == 0
    assert p.income.monthly_manwon is None
    assert p.first_life.ever_owned_house is False
