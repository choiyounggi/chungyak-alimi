"""프로필 스키마 확장 (signup-hardening Task 01) — 신규 6컬럼 + Profile 필드 + 어댑터.

순수 모델 계약(1~3)은 DB 없이 검증하고, 왕복/파생동기화는 _db_available 게이트를 건다
— postgres 가 없는 환경에서도 t03/t05 가 소비할 시그니처는 실제로 검증되어야 한다.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete, func, select, text

from src.db import Member, MemberProfile, SessionLocal, engine, init_db
from src.members import MAX_PARTNERS, create_member, get_profile, profile_from_member, update_profile
from src.scoring import PREFERRED_TYPES, PartnerInfo, Profile, ResidencePeriod


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


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


# ══ 순수 모델 계약(DB 불필요) ══


# ── 경계: 아무것도 안 넣은 Profile 의 신규 필드 기본값 ──
def test_scoring_models_default_empty():
    p = Profile()
    assert p.owns_car is False
    assert p.account_payment_count == 0
    assert p.residence_history == []
    assert p.preferred_types == []
    assert p.partners == []
    # 기본 리스트가 인스턴스 간 공유되면 한쪽 수정이 다른 쪽을 오염시킨다
    p.residence_history.append(ResidencePeriod(region="서울"))
    assert Profile().residence_history == []


# ── 계약: 허용 전형 집합이 t05 체크박스/ t03 판정과 공유하는 정확한 5종 ──
def test_preferred_types_exact_set():
    assert PREFERRED_TYPES == frozenset(
        {"newlywed", "pre_newlywed", "youth", "special", "general"}
    )
    assert isinstance(PREFERRED_TYPES, frozenset)


# ── 경계: JSONB 의 누락 키/None 이 예외가 아니라 기본값으로 접힌다 ──
def test_nested_models_tolerate_missing_and_none():
    # 누락 키
    r = ResidencePeriod(**{"region": "서울"})
    assert r.region == "서울"
    assert r.since is None
    # None 값(구버전 행/부분 입력) — ValidationError 가 나면 프로필 조회 전체가 죽는다
    r2 = ResidencePeriod(**{"region": None, "since": None})
    assert r2.region == ""
    assert r2.since is None
    p = PartnerInfo(**{"label": None, "owns_home": None, "residence_region": None})
    assert p.label == ""
    assert p.owns_home is False
    assert p.lives_with_parents is False
    assert p.residence_region == ""
    assert p.income_base_region == ""
    # ISO 문자열 → date 파싱(JSONB 는 date 를 문자열로 싣는다)
    assert ResidencePeriod(region="경기", since="2020-03-01").since == date(2020, 3, 1)


# ══ DB 왕복 / 쓰기 경계 ══


# ── 정상: 신규 6필드 저장 → 재조회 일치 + residence_regions 파생 동기화 ──
@needs_db
def test_update_profile_roundtrip_new_fields(session):
    m = create_member("ext-a@example.com", "h", session=session)
    update_profile(
        m.id,
        {
            "owns_car": True,
            "account_payment_count": 24,
            "residence_history": [
                {"region": "서울", "since": "2020-03-01"},
                {"region": "경기", "since": None},
            ],
            "preferred_types": ["newlywed", "general"],
            "partners": [{"label": "본인", "lives_with_parents": True, "owns_home": False,
                          "residence_region": "서울", "income_base_region": "서울"}],
            "onboarding_step": 2,
        },
        session=session,
    )
    prof = get_profile(m.id, session=session)
    assert prof.owns_car is True
    assert prof.account_payment_count == 24
    assert prof.residence_history == [
        {"region": "서울", "since": "2020-03-01"},
        {"region": "경기", "since": None},
    ]
    assert prof.preferred_types == ["newlywed", "general"]
    assert prof.partners == [
        {"label": "본인", "lives_with_parents": True, "owns_home": False,
         "residence_region": "서울", "income_base_region": "서울"}
    ]
    assert prof.onboarding_step == 2
    # D3 파생 동기화 — region_matches/대시보드가 쓰는 계약이 유지된다
    assert prof.residence_regions == ["서울", "경기"]


# ── 정상: profile_from_member 가 신규 필드를 중첩 모델로 실어 나른다 ──
@needs_db
def test_profile_from_member_carries_new_fields(session):
    m = create_member("ext-b@example.com", "h", session=session)
    update_profile(
        m.id,
        {
            "owns_car": True,
            "account_payment_count": 24,
            "residence_history": [{"region": "서울", "since": "2020-03-01"}],
            "preferred_types": ["youth"],
            "partners": [{"label": "상대", "owns_home": True, "residence_region": "부산"}],
        },
        session=session,
    )
    p = profile_from_member(get_profile(m.id, session=session))
    assert isinstance(p, Profile)
    assert p.owns_car is True
    assert p.account_payment_count == 24
    assert len(p.residence_history) == 1
    assert isinstance(p.residence_history[0], ResidencePeriod)
    assert p.residence_history[0].region == "서울"
    assert p.residence_history[0].since == date(2020, 3, 1)
    assert p.preferred_types == ["youth"]
    assert len(p.partners) == 1
    assert isinstance(p.partners[0], PartnerInfo)
    assert p.partners[0].owns_home is True
    assert p.partners[0].residence_region == "부산"
    assert p.partners[0].lives_with_parents is False


# ── 에러: 허용 외 선호전형은 저장 경계에서 걸러진다 ──
@needs_db
def test_preferred_types_filters_unknown_values(session):
    m = create_member("ext-c@example.com", "h", session=session)
    update_profile(
        m.id,
        {"preferred_types": ["newlywed", "foo", "", "DROP TABLE", 123, None]},
        session=session,
    )
    prof = get_profile(m.id, session=session)
    assert prof.preferred_types == ["newlywed"]
    # 전부 허용 외면 빈 리스트(예외 아님)
    update_profile(m.id, {"preferred_types": ["nope"]}, session=session)
    assert get_profile(m.id, session=session).preferred_types == []


# ── 에러: 파싱 불가한 since 는 저장 시점에 None 으로 떨어져 조회가 터지지 않는다 ──
@needs_db
def test_invalid_since_folds_to_none(session):
    m = create_member("ext-d@example.com", "h", session=session)
    update_profile(
        m.id,
        {"residence_history": [{"region": "서울", "since": "not-a-date"},
                               {"region": "부산", "since": 12345},
                               {"region": "대구", "since": []},
                               {"region": "광주", "since": "2020-13-45"}]},
        session=session,
    )
    prof = get_profile(m.id, session=session)
    assert [h["since"] for h in prof.residence_history] == [None, None, None, None]
    p = profile_from_member(prof)  # 예외 없이 변환돼야 한다
    assert [r.since for r in p.residence_history] == [None, None, None, None]
    assert [r.region for r in p.residence_history] == ["서울", "부산", "대구", "광주"]


# ── 경계: date.fromisoformat 이 받는 압축 ISO(YYYYMMDD)도 정규형으로 접힌다 ──
@needs_db
def test_compact_iso_since_normalized(session):
    m = create_member("ext-d2@example.com", "h", session=session)
    update_profile(
        m.id,
        {"residence_history": [{"region": "부산", "since": 20200301}]},
        session=session,
    )
    prof = get_profile(m.id, session=session)
    # 저장형은 언제나 'YYYY-MM-DD' 한 가지 — 표현이 섞이면 이후 비교가 깨진다
    assert prof.residence_history == [{"region": "부산", "since": "2020-03-01"}]
    assert profile_from_member(prof).residence_history[0].since == date(2020, 3, 1)


# ── 경계: 같은 요청에 둘 다 오면 residence_history 가 정본(A11) ──
@needs_db
def test_history_priority_over_regions(session):
    m = create_member("ext-e@example.com", "h", session=session)
    update_profile(
        m.id,
        {"residence_history": [{"region": "서울"}], "residence_regions": ["부산"]},
        session=session,
    )
    assert get_profile(m.id, session=session).residence_regions == ["서울"]


# ── 경계: history 없이 residence_regions 만 주면 기존 폼 경로 그대로(무회귀) ──
@needs_db
def test_regions_only_path_unchanged(session):
    m = create_member("ext-f@example.com", "h", session=session)
    update_profile(m.id, {"residence_regions": ["부산"]}, session=session)
    prof = get_profile(m.id, session=session)
    assert prof.residence_regions == ["부산"]
    assert prof.residence_history == []


# ── 경계: 빈 region 은 history 에는 남고 파생 목록에서만 빠진다 ──
@needs_db
def test_empty_region_excluded_from_derived(session):
    m = create_member("ext-g@example.com", "h", session=session)
    update_profile(
        m.id,
        {"residence_history": [{"region": "", "since": "2019-01-01"}, {"region": "서울"}]},
        session=session,
    )
    prof = get_profile(m.id, session=session)
    assert len(prof.residence_history) == 2
    assert prof.residence_regions == ["서울"]
    # 빈 리스트로 덮으면 파생 목록도 비워진다
    update_profile(m.id, {"residence_history": []}, session=session)
    prof = get_profile(m.id, session=session)
    assert prof.residence_history == []
    assert prof.residence_regions == []


# ── 경계: 파트너는 MAX_PARTNERS 개로 잘리고 dict 아닌 원소는 버려진다 ──
@needs_db
def test_partners_capped_and_nondict_dropped(session):
    m = create_member("ext-h@example.com", "h", session=session)
    update_profile(
        m.id,
        {"partners": ["문자열", {"label": "A"}, {"label": "B"}, {"label": "C"}]},
        session=session,
    )
    prof = get_profile(m.id, session=session)
    assert MAX_PARTNERS == 2
    assert len(prof.partners) == MAX_PARTNERS
    assert [p["label"] for p in prof.partners] == ["A", "B"]
    assert set(prof.partners[0]) == {
        "label", "lives_with_parents", "owns_home", "residence_region", "income_base_region"
    }
    # dict 아닌 원소는 Profile 변환에서도 조용히 빠진다
    assert len(profile_from_member(prof).partners) == 2


# ── 경계: date 객체를 넘겨도 JSONB 직렬화가 터지지 않는다(psycopg3 는 date 를 못 싣는다) ──
@needs_db
def test_date_object_since_serializes(session):
    m = create_member("ext-i@example.com", "h", session=session)
    update_profile(
        m.id,
        {"residence_history": [{"region": "인천", "since": date(2021, 5, 2)}]},
        session=session,
    )
    prof = get_profile(m.id, session=session)
    assert prof.residence_history == [{"region": "인천", "since": "2021-05-02"}]
    assert profile_from_member(prof).residence_history[0].since == date(2021, 5, 2)


# ── 경계: 신규 회원의 프로필은 신규 6필드가 전부 server_default 값 ──
@needs_db
def test_new_columns_default_on_fresh_profile(session):
    m = create_member("ext-j@example.com", "h", session=session)
    session.expire_all()
    prof = get_profile(m.id, session=session)
    assert prof.owns_car is False
    assert prof.account_payment_count == 0
    assert prof.residence_history == []
    assert prof.preferred_types == []
    assert prof.partners == []
    assert prof.onboarding_step == 0
    # 어댑터도 기본값 프로필을 예외 없이 변환한다
    p = profile_from_member(prof)
    assert p.residence_history == []
    assert p.partners == []


_NEW_COLUMNS = {
    "owns_car": "boolean",
    "account_payment_count": "integer",
    "residence_history": "jsonb",
    "preferred_types": "jsonb",
    "partners": "jsonb",
    "onboarding_step": "smallint",
}


# ── 경계: init_db 멱등 — 배포 DB 에 두 번 돌려도 신규 ALTER 가 실패하지 않는다 ──
@needs_db
def test_init_db_idempotent_with_new_columns():
    init_db()
    init_db()  # 재호출해도 예외 없이 스키마가 준비돼야 한다
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(MemberProfile)) >= 0
        rows = s.execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default"
                " FROM information_schema.columns"
                " WHERE table_name = 'member_profile' AND column_name = ANY(:names)"
            ),
            {"names": list(_NEW_COLUMNS)},
        ).all()
    _assert_new_columns(rows)


def _assert_new_columns(rows):
    found = {r[0]: r for r in rows}
    assert set(found) == set(_NEW_COLUMNS)  # 6개 전부 실제 DB 에 존재
    for name, expected_type in _NEW_COLUMNS.items():
        _, data_type, is_nullable, default = found[name]
        assert data_type == expected_type, f"{name}: {data_type}"
        assert is_nullable == "NO", f"{name} 이 NULL 허용 상태"
        assert default is not None, f"{name} 에 server_default 없음"


def _new_column_rows(s):
    return s.execute(
        text(
            "SELECT column_name, data_type, is_nullable, column_default"
            " FROM information_schema.columns"
            " WHERE table_name = 'member_profile' AND column_name = ANY(:names)"
        ),
        {"names": list(_NEW_COLUMNS)},
    ).all()


# ── 경계: 신규 컬럼이 없는 '기존 배포 DB' 에 멱등 ALTER 가 실제로 붙는다 ──
@needs_db
def test_alter_backfills_legacy_table(session):
    """create_all 은 이미 있는 테이블에 컬럼을 추가하지 않는다 — 배포 DB 를 살리는 건
    init_db() 의 ALTER 블록뿐이라, 그 경로를 실제로 지우고 되살려 검증한다."""
    m = create_member("ext-legacy@example.com", "h", session=session)
    update_profile(m.id, {"owns_car": True, "onboarding_step": 3}, session=session)
    try:
        with engine.begin() as conn:
            for name in _NEW_COLUMNS:
                conn.exec_driver_sql(f"ALTER TABLE member_profile DROP COLUMN {name}")
        with SessionLocal() as s:
            assert _new_column_rows(s) == []  # 신규 컬럼이 없는 구버전 상태 재현
    finally:
        init_db()  # 배포 시나리오: 코드가 올라가면서 경량 마이그레이션이 돈다
    with SessionLocal() as s:
        _assert_new_columns(_new_column_rows(s))
        # 기존 회원 행이 살아남고 신규 컬럼은 기본값으로 채워진다
        row = s.execute(
            text(
                "SELECT owns_car, account_payment_count, residence_history,"
                " preferred_types, partners, onboarding_step"
                " FROM member_profile WHERE member_id = :mid"
            ),
            {"mid": m.id},
        ).one()
    assert row == (False, 0, [], [], [], 0)
    init_db()  # 컬럼이 이미 있는 상태에서 한 번 더 — IF NOT EXISTS 멱등
