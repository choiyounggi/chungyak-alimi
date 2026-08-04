from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    create_engine,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings
from .filters import FilterConfig, find_superseded, match_notice
from .models import ApplyhomeHouseType, ApplyhomeNotice
from .scoring import judge_notice, load_profile

# 정정공고로 대체된 공고의 탈락 사유 접두사(뒤에 :최신공고번호)
SUPERSEDED_REASON = "정정공고로 대체"

# 공급기관 축(D7) — 닫힌 5종. 웹 필터 칩과 값을 공유한다.
AGENCIES = ("LH", "SH", "GH", "HUG", "기타")

# 수집원 → 기본 공급기관(D8). myhome 은 행마다 달라 모델이 agency 를 직접 싣는다.
AGENCY_BY_SOURCE = {
    "applyhome": "기타", "lh": "LH", "hug": "HUG", "sh": "SH", "gh": "GH",
}


def global_id(source: str, native: str) -> str:
    """기관 간 공고번호 충돌을 막는 글로벌 ID(D1). 이미 접두된 값은 그대로 둔다.

    판정은 `":" in native` 가 아니라 **자기 소스 접두사**로 한다 — 원본 ID 자체에
    콜론이 들어오면 전자는 접두를 건너뛰어 서로 다른 소스가 같은 PK 로 충돌한다.
    """
    native = str(native)
    prefix = f"{source}:"
    return native if native.startswith(prefix) else prefix + native


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
    "native_id",
    "agency",
    "rent_gtn",
    "mt_rntchrg",
)


class Base(DeclarativeBase):
    pass


class Notice(Base):
    __tablename__ = "notice"

    pblanc_no: Mapped[str] = mapped_column(String, primary_key=True)
    house_manage_no: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, default="applyhome")
    native_id: Mapped[str | None] = mapped_column(String)   # 소스 원본 공고번호(D2)
    agency: Mapped[str | None] = mapped_column(String)      # 공급기관 LH/SH/GH/HUG/기타(D7)
    rent_gtn: Mapped[int | None] = mapped_column(BigInteger)    # 임대보증금(원, D4)
    mt_rntchrg: Mapped[int | None] = mapped_column(BigInteger)  # 월임대료(원, D4)
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
    # 내 순위 판정("1순위"/"2순위", 공공·프로필없음 등 판정불가는 NULL) — 평가 시 저장
    my_rank: Mapped[str | None] = mapped_column(String)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotifyLog(Base):
    __tablename__ = "notify_log"

    pblanc_no: Mapped[str] = mapped_column(String, primary_key=True)
    channel: Mapped[str] = mapped_column(String, primary_key=True, default="telegram")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Bookmark(Base):
    """사용자가 즐겨찾기한 공고(단일 사용자라 pblanc_no만으로 식별)."""

    __tablename__ = "bookmark"

    pblanc_no: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Member(Base):
    """회원 계정(멀티유저). email 로그인, 비밀번호는 argon2 해시(Task 04).

    id 는 내부 전용 대리키(BIGINT identity) — URL/API 에 노출하지 않고, 리소스 접근은
    항상 세션의 현재 회원 기준으로만 한다(D1). email 은 UNIQUE 자연키.
    """

    __tablename__ = "member"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemberProfile(Base):
    """회원별 리치 프로필(member 와 1:1, ON DELETE CASCADE). scoring.Profile 로 변환해
    순위 판정에 사용한다(Task 02). 지역 목록은 파이썬(온더플라이)에서 매칭하므로 JSONB(D8)."""

    __tablename__ = "member_profile"
    __table_args__ = (
        CheckConstraint(
            "household_type IN ('newlywed','pre_newlywed','youth','general')",
            name="ck_member_profile_household_type",
        ),
    )

    member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="CASCADE"), primary_key=True
    )
    # 인적/세대
    birth_date: Mapped[date | None] = mapped_column(Date)
    marriage_date: Mapped[date | None] = mapped_column(Date)
    engaged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_household_head: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    household_all_homeless: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    homeless_since: Mapped[date | None] = mapped_column(Date)
    dependents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    won_within_5y: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    children_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    real_estate_manwon: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    region: Mapped[str] = mapped_column(String, nullable=False, server_default=text("''"))
    # 청약통장
    account_opened: Mapped[date | None] = mapped_column(Date)
    account_balance_manwon: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # 소득(월평균/기준소득 미입력은 NULL)
    income_monthly_manwon: Mapped[int | None] = mapped_column(Integer)
    income_base_manwon: Mapped[int | None] = mapped_column(Integer)
    income_dual: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # 생애최초
    fl_ever_owned_house: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    fl_income_tax_5y: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    fl_currently_earning: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # 신규 필드
    car_value_manwon: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    household_head_owns_home: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    household_type: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'general'"))
    is_first_home: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    residence_regions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    income_base_regions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    interest_regions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    # 경량 마이그레이션: create_all은 기존 테이블에 컬럼을 추가하지 않는다
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE match_result ADD COLUMN IF NOT EXISTS my_rank VARCHAR")
        for ddl in (
            "ALTER TABLE notice ADD COLUMN IF NOT EXISTS native_id VARCHAR",
            "ALTER TABLE notice ADD COLUMN IF NOT EXISTS agency VARCHAR",
            "ALTER TABLE notice ADD COLUMN IF NOT EXISTS rent_gtn BIGINT",
            "ALTER TABLE notice ADD COLUMN IF NOT EXISTS mt_rntchrg BIGINT",
        ):
            conn.exec_driver_sql(ddl)
    migrate_global_ids()


def migrate_global_ids() -> dict:
    """pblanc_no 를 '<source>:<native>' 로 이관(D5·D6). 멱등 — 이미 접두된 행은 건너뛴다.

    자식 테이블을 먼저 갱신한다. notice 를 먼저 바꾸면 조인 키가 사라진다.
    반환: 테이블별 갱신 행 수.

    LIKE 패턴의 `%` 는 `%%` 로 escape 한다 — exec_driver_sql 은 드라이버(psycopg)의
    paramstyle 을 그대로 통과시켜 `%` 를 플레이스홀더로 해석한다.
    """
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE notice SET native_id = pblanc_no WHERE native_id IS NULL"
        )
        conn.exec_driver_sql(
            "UPDATE notice SET agency = CASE source WHEN 'lh' THEN 'LH' ELSE '기타' END"
            " WHERE agency IS NULL"
        )
        for table in ("notice_house_type", "match_result", "notify_log", "bookmark"):
            r = conn.exec_driver_sql(
                f"UPDATE {table} c SET pblanc_no = n.source || ':' || c.pblanc_no"
                " FROM notice n WHERE n.pblanc_no = c.pblanc_no"
                " AND c.pblanc_no NOT LIKE '%%:%%'"
            )
            counts[table] = r.rowcount
        r = conn.exec_driver_sql(
            "UPDATE notice SET pblanc_no = source || ':' || pblanc_no"
            " WHERE pblanc_no NOT LIKE '%%:%%'"
        )
        counts["notice"] = r.rowcount
    return counts


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
    row = {c: getattr(n, c, None) for c in _COLS}
    row["native_id"] = n.pblanc_no
    row["pblanc_no"] = global_id(source, n.pblanc_no)
    row["agency"] = getattr(n, "agency", None) or AGENCY_BY_SOURCE.get(source, "기타")
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
    deduped = {global_id(source, n.pblanc_no): n for n in notices}
    notices = list(deduped.values())

    own = session is None
    session = session or SessionLocal()
    try:
        incoming = [global_id(source, n.pblanc_no) for n in notices]
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
    source: str = "applyhome",
    session: Session | None = None,
) -> int:
    """주택형(면적·분양가)을 (pblanc_no, house_ty) 기준으로 upsert. 처리 건수를 반환."""
    if not house_types:
        return 0

    own = session is None
    session = session or SessionLocal()
    try:
        # 같은 배치 내 (pblanc_no, house_ty) 중복 제거(마지막 유지)
        deduped = {(global_id(source, ht.pblanc_no), ht.house_ty): ht for ht in house_types}
        rows = []
        for ht in deduped.values():
            row = {c: getattr(ht, c) for c in _HT_COLS}
            row["pblanc_no"] = global_id(source, ht.pblanc_no)
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
    results: list[tuple],
    *,
    session: Session | None = None,
) -> int:
    """(pblanc_no, matched, fail_reasons[, my_rank]) 목록을 match_result 에 upsert.

    my_rank는 선택(생략 시 NULL) — 기존 3-튜플 호출과 호환."""
    if not results:
        return 0
    own = session is None
    session = session or SessionLocal()
    try:
        deduped = {r[0]: r for r in results}
        rows = [
            {"pblanc_no": r[0], "matched": r[1], "fail_reasons": r[2],
             "my_rank": r[3] if len(r) > 3 else None}
            for r in deduped.values()
        ]
        stmt = pg_insert(MatchResult).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["pblanc_no"],
            set_={
                "matched": stmt.excluded.matched,
                "fail_reasons": stmt.excluded.fail_reasons,
                "my_rank": stmt.excluded.my_rank,
                "evaluated_at": func.now(),
            },
        )
        session.execute(stmt)
        session.commit()
        return len(rows)
    finally:
        if own:
            session.close()


def evaluate_all(
    cfg: FilterConfig, *, today: date | None = None, session: Session | None = None
) -> tuple[int, int]:
    """DB의 모든 공고를 필터로 평가해 match_result 에 저장. (평가건수, 매칭건수) 반환."""
    own = session is None
    session = session or SessionLocal()
    try:
        notices = session.scalars(select(Notice)).all()
        superseded = find_superseded(notices)
        profile = load_profile()  # 없으면 None → my_rank 전부 NULL
        results: list[tuple] = []
        for n in notices:
            # 정정공고로 대체된 공고는 노출 대상에서 제외(최신 정정만 남긴다)
            if n.pblanc_no in superseded:
                results.append(
                    (n.pblanc_no, False, [f"{SUPERSEDED_REASON}:{superseded[n.pblanc_no]}"])
                )
                continue
            hts = session.scalars(
                select(NoticeHouseType).where(NoticeHouseType.pblanc_no == n.pblanc_no)
            ).all()
            matched, fails = match_notice(n, hts, cfg, today=today)
            my_rank = None
            if matched and profile is not None:
                judged = judge_notice(n, hts, profile, today=today)
                if judged["supported"]:
                    my_rank = judged["rank"]["rank"]
            results.append((n.pblanc_no, matched, fails, my_rank))
        save_match_results(results, session=session)
        return (len(results), sum(1 for r in results if r[1]))
    finally:
        if own:
            session.close()


def pending_notifications(
    *, channel: str = "telegram", session: Session | None = None
) -> list[Notice]:
    """매칭됐지만 아직 해당 채널로 발송하지 않은 공고 목록."""
    own = session is None
    session = session or SessionLocal()
    try:
        already = select(NotifyLog.pblanc_no).where(NotifyLog.channel == channel)
        q = (
            select(Notice)
            .join(MatchResult, Notice.pblanc_no == MatchResult.pblanc_no)
            .where(MatchResult.matched.is_(True), Notice.pblanc_no.not_in(already))
            .order_by(Notice.rcept_endde)
        )
        return list(session.scalars(q).all())
    finally:
        if own:
            session.close()


def mark_notified(pblanc_no: str, *, channel: str = "telegram", session: Session | None = None) -> None:
    own = session is None
    session = session or SessionLocal()
    try:
        stmt = pg_insert(NotifyLog).values(pblanc_no=pblanc_no, channel=channel)
        stmt = stmt.on_conflict_do_nothing(index_elements=["pblanc_no", "channel"])
        session.execute(stmt)
        session.commit()
    finally:
        if own:
            session.close()


def house_types_of(pblanc_no: str, *, session: Session) -> list[NoticeHouseType]:
    return list(
        session.scalars(
            select(NoticeHouseType).where(NoticeHouseType.pblanc_no == pblanc_no)
        ).all()
    )


def add_bookmark(pblanc_no: str, *, session: Session | None = None) -> None:
    """공고를 북마크에 추가(멱등 — 이미 있으면 무시)."""
    own = session is None
    session = session or SessionLocal()
    try:
        stmt = pg_insert(Bookmark).values(pblanc_no=pblanc_no)
        stmt = stmt.on_conflict_do_nothing(index_elements=["pblanc_no"])
        session.execute(stmt)
        session.commit()
    finally:
        if own:
            session.close()


def remove_bookmark(pblanc_no: str, *, session: Session | None = None) -> None:
    """북마크 해제(없어도 에러 없음 — 멱등)."""
    own = session is None
    session = session or SessionLocal()
    try:
        session.execute(delete(Bookmark).where(Bookmark.pblanc_no == pblanc_no))
        session.commit()
    finally:
        if own:
            session.close()


def is_bookmarked(pblanc_no: str, *, session: Session) -> bool:
    return (
        session.scalar(select(Bookmark.pblanc_no).where(Bookmark.pblanc_no == pblanc_no))
        is not None
    )


def bookmarked_pblanc_nos(*, session: Session) -> set[str]:
    """북마크된 공고번호 집합."""
    return set(session.scalars(select(Bookmark.pblanc_no)).all())
