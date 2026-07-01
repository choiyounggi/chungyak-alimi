from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    create_engine,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings
from .filters import FilterConfig, match_notice
from .models import ApplyhomeHouseType, ApplyhomeNotice

# ApplyhomeNotice → notice 테이블에 저장할 컬럼(공고 식별/일정/필터축)
_COLS = (
    "pblanc_no",
    "house_manage_no",
    "house_nm",
    "house_secd_nm",
    "house_dtl_secd_nm",
    "rent_secd_nm",
    "area_nm",
    "hsslpy_adres",
    "bsns_mby_nm",
    "rcrit_pblanc_de",
    "rcept_bgnde",
    "rcept_endde",
    "spsply_rcept_bgnde",
    "spsply_rcept_endde",
    "przwner_presnatn_de",
    "tot_suply_hshldco",
    "mvn_prearnge_ym",
    "pblanc_url",
)


class Base(DeclarativeBase):
    pass


class Notice(Base):
    __tablename__ = "notice"

    pblanc_no: Mapped[str] = mapped_column(String, primary_key=True)
    house_manage_no: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, default="applyhome")
    house_nm: Mapped[str] = mapped_column(String)
    house_secd_nm: Mapped[str | None] = mapped_column(String)
    house_dtl_secd_nm: Mapped[str | None] = mapped_column(String)
    rent_secd_nm: Mapped[str | None] = mapped_column(String)
    area_nm: Mapped[str | None] = mapped_column(String)
    hsslpy_adres: Mapped[str | None] = mapped_column(String)
    bsns_mby_nm: Mapped[str | None] = mapped_column(String)

    rcrit_pblanc_de: Mapped[date | None] = mapped_column(Date)
    rcept_bgnde: Mapped[date | None] = mapped_column(Date)
    rcept_endde: Mapped[date | None] = mapped_column(Date)
    spsply_rcept_bgnde: Mapped[date | None] = mapped_column(Date)
    spsply_rcept_endde: Mapped[date | None] = mapped_column(Date)
    przwner_presnatn_de: Mapped[date | None] = mapped_column(Date)

    tot_suply_hshldco: Mapped[int | None] = mapped_column(Integer)
    mvn_prearnge_ym: Mapped[str | None] = mapped_column(String)
    pblanc_url: Mapped[str | None] = mapped_column(String)

    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NoticeHouseType(Base):
    __tablename__ = "notice_house_type"

    pblanc_no: Mapped[str] = mapped_column(String, primary_key=True)
    house_ty: Mapped[str] = mapped_column(String, primary_key=True)
    house_manage_no: Mapped[str | None] = mapped_column(String)
    model_no: Mapped[str | None] = mapped_column(String)
    suply_ar: Mapped[float | None] = mapped_column(Numeric(10, 4))   # 공급면적(㎡)
    lttot_top_amount: Mapped[int | None] = mapped_column(Integer)     # 분양최고가(만원)
    suply_hshldco: Mapped[int | None] = mapped_column(Integer)
    spsply_hshldco: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


_HT_COLS = (
    "pblanc_no",
    "house_ty",
    "house_manage_no",
    "model_no",
    "suply_ar",
    "lttot_top_amount",
    "suply_hshldco",
    "spsply_hshldco",
)


class MatchResult(Base):
    __tablename__ = "match_result"

    pblanc_no: Mapped[str] = mapped_column(String, primary_key=True)
    matched: Mapped[bool] = mapped_column(Boolean)
    fail_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


@dataclass
class UpsertResult:
    new: list[str] = field(default_factory=list)      # 이번에 처음 본 공고번호
    updated: list[str] = field(default_factory=list)   # 기존 공고 갱신

    @property
    def new_count(self) -> int:
        return len(self.new)

    @property
    def updated_count(self) -> int:
        return len(self.updated)


def _to_row(n: ApplyhomeNotice, source: str) -> dict:
    row = {c: getattr(n, c) for c in _COLS}
    row["raw"] = n.raw
    row["source"] = source
    return row


def upsert_notices(
    notices: list[ApplyhomeNotice],
    *,
    source: str = "applyhome",
    session: Session | None = None,
) -> UpsertResult:
    """공고를 upsert 한다. PBLANC_NO 충돌 시 갱신하되 first_seen_at 은 보존한다.

    반환값으로 신규(new) / 갱신(updated) 공고번호를 구분해 돌려준다(신규감지).
    """
    if not notices:
        return UpsertResult()

    # 같은 배치 내 중복 공고번호 제거(마지막 유지) → ON CONFLICT 이중 반영 방지
    deduped = {n.pblanc_no: n for n in notices}
    notices = list(deduped.values())

    own = session is None
    session = session or SessionLocal()
    try:
        incoming = [n.pblanc_no for n in notices]
        existing = {
            pid
            for (pid,) in session.execute(
                select(Notice.pblanc_no).where(Notice.pblanc_no.in_(incoming))
            )
        }

        rows = [_to_row(n, source) for n in notices]
        stmt = pg_insert(Notice).values(rows)
        update_set = {c: stmt.excluded[c] for c in (*_COLS, "raw", "source") if c != "pblanc_no"}
        update_set["updated_at"] = func.now()  # first_seen_at 은 제외 → 최초 발견시각 보존
        stmt = stmt.on_conflict_do_update(index_elements=["pblanc_no"], set_=update_set)
        session.execute(stmt)
        session.commit()

        return UpsertResult(
            new=[i for i in incoming if i not in existing],
            updated=[i for i in incoming if i in existing],
        )
    finally:
        if own:
            session.close()


def upsert_house_types(
    house_types: list[ApplyhomeHouseType],
    *,
    session: Session | None = None,
) -> int:
    """주택형(면적·분양가)을 (pblanc_no, house_ty) 기준으로 upsert. 처리 건수를 반환."""
    if not house_types:
        return 0

    own = session is None
    session = session or SessionLocal()
    try:
        # 같은 배치 내 (pblanc_no, house_ty) 중복 제거(마지막 유지)
        deduped = {(ht.pblanc_no, ht.house_ty): ht for ht in house_types}
        rows = []
        for ht in deduped.values():
            row = {c: getattr(ht, c) for c in _HT_COLS}
            row["raw"] = ht.raw
            rows.append(row)

        stmt = pg_insert(NoticeHouseType).values(rows)
        update_set = {c: stmt.excluded[c] for c in (*_HT_COLS, "raw") if c not in ("pblanc_no", "house_ty")}
        update_set["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(
            index_elements=["pblanc_no", "house_ty"], set_=update_set
        )
        session.execute(stmt)
        session.commit()
        return len(rows)
    finally:
        if own:
            session.close()


def save_match_results(
    results: list[tuple[str, bool, list[str]]],
    *,
    session: Session | None = None,
) -> int:
    """(pblanc_no, matched, fail_reasons) 목록을 match_result 에 upsert."""
    if not results:
        return 0
    own = session is None
    session = session or SessionLocal()
    try:
        deduped = {r[0]: r for r in results}
        rows = [
            {"pblanc_no": p, "matched": m, "fail_reasons": fr}
            for (p, m, fr) in deduped.values()
        ]
        stmt = pg_insert(MatchResult).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["pblanc_no"],
            set_={
                "matched": stmt.excluded.matched,
                "fail_reasons": stmt.excluded.fail_reasons,
                "evaluated_at": func.now(),
            },
        )
        session.execute(stmt)
        session.commit()
        return len(rows)
    finally:
        if own:
            session.close()


def evaluate_all(cfg: FilterConfig, *, session: Session | None = None) -> tuple[int, int]:
    """DB의 모든 공고를 필터로 평가해 match_result 에 저장. (평가건수, 매칭건수) 반환."""
    own = session is None
    session = session or SessionLocal()
    try:
        notices = session.scalars(select(Notice)).all()
        results: list[tuple[str, bool, list[str]]] = []
        for n in notices:
            hts = session.scalars(
                select(NoticeHouseType).where(NoticeHouseType.pblanc_no == n.pblanc_no)
            ).all()
            matched, fails = match_notice(n, hts, cfg)
            results.append((n.pblanc_no, matched, fails))
        save_match_results(results, session=session)
        return (len(results), sum(1 for _, m, _ in results if m))
    finally:
        if own:
            session.close()
