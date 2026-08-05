"""비밀번호 해싱/검증 + authenticate_member (Task 04) — argon2id.

순수 해싱 케이스는 DB 없이 돌고, authenticate_member 케이스만 postgres 필요 → _db_available 게이트.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete

from src import members
from src.db import Member, MemberProfile, SessionLocal, engine, init_db
from src.members import (
    _ph,
    authenticate_member,
    create_member,
    hash_password,
    verify_password,
)


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


db_only = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


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


# --- 해싱 파라미터 -----------------------------------------------------------


def test_hasher_meets_owasp_minimum():
    """argon2id 기본 파라미터가 OWASP 최소치(m=19456, t=2, p=1) 이상인지 확인."""
    assert _ph.memory_cost >= 19456
    assert _ph.time_cost >= 2
    assert _ph.parallelism >= 1


# --- ① 정상: hash → verify 왕복 ---------------------------------------------


def test_hash_then_verify_roundtrip():
    password = "correct horse battery staple"
    h = hash_password(password)

    assert verify_password(h, password) is True
    assert password not in h  # 평문이 해시에 남지 않는다
    assert h.startswith("$argon2id$")  # 알고리즘/파라미터가 해시 문자열에 포함


def test_hash_is_salted_per_call():
    """같은 비밀번호라도 호출마다 salt 가 달라 해시가 달라진다."""
    password = "same-password"
    first, second = hash_password(password), hash_password(password)

    assert first != second
    assert verify_password(first, password) is True
    assert verify_password(second, password) is True


# --- ② 에러: 틀린 비밀번호 ---------------------------------------------------


def test_verify_rejects_wrong_password():
    h = hash_password("real-password")

    assert verify_password(h, "wrong-password") is False


# --- ③ 에러: 길이 상한 -------------------------------------------------------


def test_hash_password_rejects_over_128_chars():
    with pytest.raises(ValueError):
        hash_password("a" * 129)


# --- ④ 경계: 길이 128 / 빈 문자열 --------------------------------------------


def test_hash_password_accepts_exactly_128_chars():
    password = "a" * 128
    h = hash_password(password)

    assert verify_password(h, password) is True


def test_hash_password_accepts_empty_string():
    """빈 비밀번호는 해싱 레이어에서 막지 않는다(폼 검증은 라우트 책임)."""
    h = hash_password("")

    assert verify_password(h, "") is True
    assert verify_password(h, "x") is False


# --- ⑤ authenticate_member (DB 필요) ----------------------------------------


@db_only
def test_authenticate_member_returns_member_on_correct_password(session):
    create_member("Yeonggi@Example.com", hash_password("pw-1234"), session=session)

    m = authenticate_member("yeonggi@example.com", "pw-1234", session=session)

    assert m is not None
    assert m.email == "yeonggi@example.com"


@db_only
def test_authenticate_member_returns_none_on_wrong_password(session):
    create_member("a@example.com", hash_password("pw-1234"), session=session)

    assert authenticate_member("a@example.com", "nope", session=session) is None


@db_only
def test_authenticate_member_returns_none_for_unknown_email(session):
    """존재하지 않는 계정도 예외 없이 None — 더미 검증으로 타이밍을 은닉한다."""
    assert authenticate_member("ghost@example.com", "whatever", session=session) is None


@db_only
def test_authenticate_member_handles_empty_email(session):
    """경계: 빈 email/비밀번호도 예외 없이 None."""
    assert authenticate_member("", "", session=session) is None


# --- ⑥ 타이밍 은닉 메커니즘 --------------------------------------------------


@pytest.fixture
def verify_spy(monkeypatch):
    """authenticate_member 가 실제로 수행한 검증 호출을 (hash, password) 로 기록한다."""
    calls: list[tuple[str, str]] = []
    real = members.verify_password

    def spy(hash_: str, password: str) -> bool:
        calls.append((hash_, password))
        return real(hash_, password)

    monkeypatch.setattr(members, "verify_password", spy)
    return calls


@db_only
def test_unknown_email_still_verifies_against_dummy_hash(session, verify_spy):
    """계정이 없어도 고정 더미 해시로 1회 검증한다 — 이 호출이 빠지면 응답 시간이 계정 존재를 누설."""
    assert authenticate_member("ghost@example.com", "whatever", session=session) is None

    assert verify_spy == [(members._DUMMY_HASH, "whatever")]


@db_only
def test_existing_email_verifies_exactly_once_like_unknown_email(session, verify_spy):
    """존재하는 계정도 검증 1회 — 두 분기의 argon2 비용이 같아야 타이밍이 구분되지 않는다."""
    m = create_member("real@example.com", hash_password("pw-1234"), session=session)

    assert authenticate_member("real@example.com", "wrong", session=session) is None
    assert verify_spy == [(m.password_hash, "wrong")]
