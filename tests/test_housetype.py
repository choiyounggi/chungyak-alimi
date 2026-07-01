from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from src.db import (
    NoticeHouseType,
    SessionLocal,
    engine,
    init_db,
    upsert_house_types,
)
from src.models import ApplyhomeHouseType

SAMPLE_HT = {
    "PBLANC_NO": "2026820005",
    "HOUSE_MANAGE_NO": "2026820005",
    "MODEL_NO": "01",
    "HOUSE_TY": "055.9200A",
    "SUPLY_AR": "80.6230",
    "LTTOT_TOP_AMOUNT": "50724",
    "SUPLY_HSHLDCO": 0,
    "SPSPLY_HSHLDCO": 159,
}


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


# ── 모델 파싱 (DB 불필요) ──
def test_parse_house_type():
    ht = ApplyhomeHouseType.model_validate(SAMPLE_HT)
    assert ht.house_ty == "055.9200A"
    assert ht.suply_ar == 80.6230
    assert ht.lttot_top_amount == 50724  # 문자열 → int(만원)
    assert ht.raw["MODEL_NO"] == "01"


# ── 경계: 빈 분양가/면적 → None (임대 등) ──
def test_empty_price_area_none():
    d = {**SAMPLE_HT, "LTTOT_TOP_AMOUNT": "", "SUPLY_AR": None}
    ht = ApplyhomeHouseType.model_validate(d)
    assert ht.lttot_top_amount is None
    assert ht.suply_ar is None


# ── 에러: 필수 house_ty 누락 ──
def test_missing_house_ty_raises():
    d = {k: v for k, v in SAMPLE_HT.items() if k != "HOUSE_TY"}
    with pytest.raises(ValidationError):
        ApplyhomeHouseType.model_validate(d)


db_only = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


@pytest.fixture
def session():
    init_db()
    s = SessionLocal()
    s.execute(delete(NoticeHouseType))
    s.commit()
    yield s
    s.execute(delete(NoticeHouseType))
    s.commit()
    s.close()


def _ht(pblanc_no: str, house_ty: str, **over) -> ApplyhomeHouseType:
    d = {**SAMPLE_HT, "PBLANC_NO": pblanc_no, "HOUSE_TY": house_ty, **over}
    return ApplyhomeHouseType.model_validate(d)


# ── 정상: upsert 저장 ──
@db_only
def test_upsert_house_types(session):
    n = upsert_house_types([_ht("P1", "84A"), _ht("P1", "84B")], session=session)
    assert n == 2
    total = session.scalar(select(func.count()).select_from(NoticeHouseType))
    assert total == 2


# ── 경계: 배치 내 중복 키 → 이중반영 없이 1건 ──
@db_only
def test_dedup_in_batch(session):
    upsert_house_types(
        [_ht("P1", "84A", LTTOT_TOP_AMOUNT="1"), _ht("P1", "84A", LTTOT_TOP_AMOUNT="2")],
        session=session,
    )
    total = session.scalar(select(func.count()).select_from(NoticeHouseType))
    assert total == 1
    row = session.scalar(select(NoticeHouseType).where(NoticeHouseType.house_ty == "84A"))
    assert row.lttot_top_amount == 2  # 마지막 값 유지
