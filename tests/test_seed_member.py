"""profile.yaml → 회원 계정 시드(Task 07) — postgres 필요, _db_available 게이트."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete

from scripts.seed_member import flatten_profile, main, seed_member
from src.db import Member, MemberProfile, SessionLocal, engine, init_db
from src.members import get_member_by_email, get_profile, profile_from_member, verify_password
from src.scoring import Profile

PROFILE_YAML = """\
birth_date: 1990-05-17
region: 경기
dependents: 2
children_minor: 1
is_household_head: true
household_all_homeless: true
real_estate_manwon: 12000
account:
  opened: 2015-03-02
  balance_manwon: 1500
income:
  monthly_manwon: 620
  base_manwon: 700
  dual_income: true
first_life:
  ever_owned_house: false
  income_tax_5y: true
  currently_earning: true
"""

EMAIL = "seed-target@example.com"
PASSWORD = "seed-pw-1234"


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


@pytest.fixture
def profile_file(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text(PROFILE_YAML, encoding="utf-8")
    return str(p)


# ── ① 정상: 1회 시드 → 회원 + 프로필, profile.yaml 값 반영 ──────────────────


def test_seed_creates_member_and_applies_profile(session, profile_file):
    member_id, created, applied = seed_member(
        EMAIL, PASSWORD, session=session, profile_path=profile_file
    )

    assert created is True
    assert applied is True

    m = get_member_by_email(EMAIL, session=session)
    assert m is not None and m.id == member_id
    # 비밀번호는 해시로만 저장된다(평문 저장 금지)
    assert m.password_hash != PASSWORD
    assert verify_password(m.password_hash, PASSWORD) is True

    prof = get_profile(member_id, session=session)
    assert prof is not None
    # 평탄한 컬럼
    assert prof.birth_date == date(1990, 5, 17)
    assert prof.region == "경기"
    assert prof.dependents == 2
    assert prof.children_minor == 1
    assert prof.is_household_head is True
    assert prof.real_estate_manwon == 12000
    # 중첩(account/income/first_life) → 대응 컬럼으로 풀림
    assert prof.account_opened == date(2015, 3, 2)
    assert prof.account_balance_manwon == 1500
    assert prof.income_monthly_manwon == 620
    assert prof.income_base_manwon == 700
    assert prof.income_dual is True
    assert prof.fl_ever_owned_house is False
    assert prof.fl_income_tax_5y is True
    assert prof.fl_currently_earning is True


def test_seeded_profile_round_trips_back_to_scoring_profile(session, profile_file):
    """이관 결과를 다시 scoring.Profile 로 되돌리면 원본 yaml 과 같아야 한다."""
    member_id, _, _ = seed_member(EMAIL, PASSWORD, session=session, profile_path=profile_file)

    restored = profile_from_member(get_profile(member_id, session=session))

    from src.scoring import load_profile

    assert restored == load_profile(profile_file)


# ── ② 멱등: 재실행해도 회원 1건 ─────────────────────────────────────────────


def test_seed_is_idempotent(session, profile_file):
    first_id, first_created, _ = seed_member(
        EMAIL, PASSWORD, session=session, profile_path=profile_file
    )
    second_id, second_created, _ = seed_member(
        EMAIL, "another-password", session=session, profile_path=profile_file
    )

    assert second_id == first_id
    assert first_created is True
    assert second_created is False
    assert session.query(Member).count() == 1
    # 재실행이 기존 비밀번호를 덮어쓰지 않는다
    m = get_member_by_email(EMAIL, session=session)
    assert verify_password(m.password_hash, PASSWORD) is True


# ── ③ 에러/경계: profile.yaml 이 없어도 회원은 생성된다 ─────────────────────


def test_seed_without_profile_file_still_creates_member(session, tmp_path, caplog):
    missing = str(tmp_path / "nope.yaml")

    member_id, created, applied = seed_member(
        EMAIL, PASSWORD, session=session, profile_path=missing
    )

    assert created is True
    assert applied is False  # 프로필은 기본값 유지
    assert get_profile(member_id, session=session) is not None
    assert any("profile" in r.message.lower() for r in caplog.records), "경고 로그 없음"


def test_seed_with_empty_profile_yaml_keeps_defaults(session, tmp_path):
    """경계: 빈 yaml 은 기본값 Profile 로 읽혀 예외 없이 적용된다."""
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")

    member_id, _, applied = seed_member(
        EMAIL, PASSWORD, session=session, profile_path=str(empty)
    )

    assert applied is True
    prof = get_profile(member_id, session=session)
    assert prof.region == ""
    assert prof.dependents == 0


# ── 평탄화 매핑 단위 ────────────────────────────────────────────────────────


def test_flatten_profile_maps_nested_fields():
    flat = flatten_profile(Profile())

    # 중첩 키는 남지 않고 전부 컬럼명으로 풀린다
    assert "account" not in flat and "income" not in flat and "first_life" not in flat
    for key in (
        "account_opened",
        "account_balance_manwon",
        "income_monthly_manwon",
        "income_base_manwon",
        "income_dual",
        "fl_ever_owned_house",
        "fl_income_tax_5y",
        "fl_currently_earning",
    ):
        assert key in flat, f"{key} 누락"


# ── ④ PII/자격증명: 출력에 비밀번호가 없고 이메일은 마스킹된다 ──────────────


def test_cli_output_never_contains_password(session, capsys, monkeypatch, profile_file):
    monkeypatch.setattr("scripts.seed_member.DEFAULT_PROFILE_PATH", profile_file)

    main(["--email", EMAIL, "--password", PASSWORD])

    out = capsys.readouterr().out
    assert PASSWORD not in out
    assert EMAIL not in out  # 원문 이메일(PII)도 그대로 찍지 않는다
    assert "seed-target" not in out
    assert "@example.com" in out  # 도메인만 남긴 마스킹 형태


def test_cli_seeds_member(session, capsys, monkeypatch, profile_file):
    monkeypatch.setattr("scripts.seed_member.DEFAULT_PROFILE_PATH", profile_file)

    main(["--email", EMAIL, "--password", PASSWORD])

    assert get_member_by_email(EMAIL, session=session) is not None


def test_cli_requires_email_and_password(session):
    with pytest.raises(SystemExit):
        main([])
