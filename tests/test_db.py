from __future__ import annotations

import copy

import pytest
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import IntegrityError

from src.db import (
    Bookmark,
    Member,
    MemberProfile,
    Notice,
    SessionLocal,
    engine,
    get_notice_analysis,
    global_id,
    init_db,
    migrate_global_ids,
    upsert_notice_analysis,
    upsert_notices,
)
from src.members import create_member, hash_password
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


@pytest.fixture
def member_id(session):
    """북마크는 회원 소유(복합 PK + member FK)라 이관 테스트에도 회원이 한 명 필요하다."""
    for t in (MemberProfile, Member):
        session.execute(delete(t))
    session.commit()
    yield create_member("db-migrate@example.com", hash_password("pw-12345"), session=session).id
    for t in (MemberProfile, Member):
        session.execute(delete(t))
    session.commit()


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
def test_migrate_moves_child_rows(session, member_id):
    _insert_native(session, "M1")
    session.execute(insert(Bookmark).values(member_id=member_id, pblanc_no="M1"))
    session.commit()

    counts = migrate_global_ids()

    assert counts["notice"] == 1
    assert counts["bookmark"] == 1
    session.expire_all()
    assert session.scalar(select(Notice.pblanc_no)) == "applyhome:M1"
    assert session.scalar(select(Bookmark.pblanc_no)) == "applyhome:M1"


# ── 경계: 두 번 실행해도 결과가 같다(멱등) ──
def test_migrate_twice_is_noop(session, member_id):
    _insert_native(session, "M2")
    session.execute(insert(Bookmark).values(member_id=member_id, pblanc_no="M2"))
    session.commit()
    migrate_global_ids()

    counts = migrate_global_ids()

    assert counts["notice"] == 0
    assert counts["bookmark"] == 0
    session.expire_all()
    assert session.scalar(select(Notice.pblanc_no)) == "applyhome:M2"
    assert session.scalar(select(Bookmark.pblanc_no)) == "applyhome:M2"


# ── 에러/경계: notice 에 짝이 없는 고아 자식 행은 그대로 남는다(예외 없음) ──
def test_migrate_keeps_orphan_child(session, member_id):
    session.execute(insert(Bookmark).values(member_id=member_id, pblanc_no="ORPHAN"))
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


# ── notice_analysis: t2 — 공고 PDF 분석 결과 저장/조회 ──

_SAMPLE_FILES = [
    {
        "name": "공고문.pdf",
        "url": "https://example.org/a.pdf",
        "ok": True,
        "error": None,
        "page_count": 3,
        "text_chars": 120,
        "sections": [{"key": "priority", "title": "1순위 조건", "lines": ["무주택 세대주"]}],
    }
]


def _insert_notice(session, pblanc_no: str, source: str = "gh") -> None:
    session.execute(
        insert(Notice).values(pblanc_no=pblanc_no, source=source, house_nm="분석테스트", raw={})
    )
    session.commit()


# ── 정상: upsert 후 조회하면 files 내용이 그대로 돌아온다 ──
def test_upsert_and_get_notice_analysis(session):
    _insert_notice(session, "gh:P1")
    upsert_notice_analysis("gh:P1", "gh", _SAMPLE_FILES, session=session)
    session.commit()

    got = get_notice_analysis(session, "gh:P1")
    assert got is not None
    assert got.files == _SAMPLE_FILES
    assert got.source == "gh"


# ── 갱신: 같은 pblanc_no 로 재-upsert 하면 files 는 바뀌고 analyzed_at 은 보존된다(D4/D6) ──
def test_notice_analysis_update_preserves_analyzed_at(session):
    _insert_notice(session, "gh:P2")
    upsert_notice_analysis("gh:P2", "gh", _SAMPLE_FILES, session=session)
    session.commit()
    first = get_notice_analysis(session, "gh:P2")
    analyzed0 = first.analyzed_at
    session.expire_all()

    new_files = [{"name": "정정.pdf", "url": "https://example.org/b.pdf", "ok": True,
                  "error": None, "page_count": 1, "text_chars": 10, "sections": []}]
    upsert_notice_analysis("gh:P2", "gh", new_files, session=session)
    session.commit()

    after = get_notice_analysis(session, "gh:P2")
    assert after.analyzed_at == analyzed0   # 최초 분석시각 보존
    assert after.files == new_files          # 값은 갱신됨


# ── 경계: 빈 files 도 저장 가능하고, 존재하지 않는 pblanc_no 조회는 None ──
def test_notice_analysis_empty_files_and_missing_lookup(session):
    _insert_notice(session, "gh:P3")
    upsert_notice_analysis("gh:P3", "gh", [], session=session)
    session.commit()

    got = get_notice_analysis(session, "gh:P3")
    assert got is not None
    assert got.files == []
    assert get_notice_analysis(session, "gh:NOPE") is None


# ── 에러: notice 에 없는 pblanc_no 로 upsert → FK 위반(IntegrityError) ──
def test_notice_analysis_fk_violation(session):
    with pytest.raises(IntegrityError):
        upsert_notice_analysis("gh:GHOST", "gh", [], session=session)
    session.rollback()


_ANALYSIS_COLUMNS = {
    "pblanc_no": "character varying",
    "source": "character varying",
    "files": "jsonb",
    "analyzed_at": "timestamp with time zone",
    "updated_at": "timestamp with time zone",
}


# ── 카탈로그 검증(D5): 신규 테이블이 create_all 만으로 배포 DB 에 정확한 shape 로 도달한다 ──
def test_notice_analysis_catalog_shape():
    init_db()
    with SessionLocal() as s:
        rows = s.execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default"
                " FROM information_schema.columns"
                " WHERE table_name = 'notice_analysis' AND column_name = ANY(:names)"
            ),
            {"names": list(_ANALYSIS_COLUMNS)},
        ).all()
    found = {r[0]: r for r in rows}
    assert set(found) == set(_ANALYSIS_COLUMNS)  # 5개 컬럼 전부 실제 DB 에 존재
    for name, expected_type in _ANALYSIS_COLUMNS.items():
        _, data_type, is_nullable, default = found[name]
        assert data_type == expected_type, f"{name}: {data_type}"
        assert is_nullable == "NO", f"{name} 이 NULL 허용 상태"
    for name in ("files", "analyzed_at", "updated_at"):
        assert found[name][3] is not None, f"{name} 에 server_default 없음"
