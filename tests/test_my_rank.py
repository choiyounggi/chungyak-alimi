from __future__ import annotations

import copy
from datetime import date

import pytest
from sqlalchemy import delete, select

from src import db as db_mod
from src.db import (
    MatchResult,
    Notice,
    NoticeHouseType,
    NotifyLog,
    SessionLocal,
    engine,
    evaluate_all,
    init_db,
    save_match_results,
    upsert_notices,
)
from src.filters import FilterConfig
from src.models import ApplyhomeNotice
from src.scoring import load_profile

from test_applyhome import SAMPLE

TODAY = date(2026, 7, 7)


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
    d["RCEPT_ENDDE"] = "2099-12-31"  # only_open 통과
    d.update(over)
    return ApplyhomeNotice.model_validate(d)


@pytest.fixture
def session():
    init_db()
    s = SessionLocal()
    for t in (NotifyLog, MatchResult, NoticeHouseType, Notice):
        s.execute(delete(t))
    s.commit()
    yield s
    for t in (NotifyLog, MatchResult, NoticeHouseType, Notice):
        s.execute(delete(t))
    s.commit()
    s.close()


# ── 저장: 4-튜플이면 my_rank 저장, 기존 3-튜플은 NULL(호환) ──
def test_save_match_results_with_rank(session):
    upsert_notices([_notice("R1"), _notice("R2")], session=session)
    save_match_results([("R1", True, [], "1순위"), ("R2", True, [])], session=session)
    rows = {
        m.pblanc_no: m.my_rank
        for m in session.scalars(select(MatchResult)).all()
    }
    assert rows == {"R1": "1순위", "R2": None}


# ── 평가: 매칭된 민영 공고에 순위 저장, 미지원(국민)은 NULL ──
def test_evaluate_all_stores_rank(session, monkeypatch):
    profile = load_profile("config/profile.example.yaml")
    assert profile is not None
    monkeypatch.setattr(db_mod, "load_profile", lambda: profile)

    upsert_notices(
        [_notice("RM1", HOUSE_DTL_SECD_NM="민영"), _notice("RM2", HOUSE_DTL_SECD_NM="국민")],
        session=session,
    )
    evaluate_all(FilterConfig(), today=TODAY, session=session)
    rows = {m.pblanc_no: (m.matched, m.my_rank) for m in session.scalars(select(MatchResult)).all()}
    assert rows["applyhome:RM1"][0] is True
    assert rows["applyhome:RM1"][1] in ("1순위", "2순위")  # example 프로필(통장 10년·예치금 충족) → 판정됨
    # 국민주택은 judge_rank_public 으로 판정된다(R2). example 프로필은 납입횟수 0회 → 2순위.
    assert rows["applyhome:RM2"] == (True, "2순위")


# ── 평가: 프로필 없으면 전부 NULL (경계값) ──
def test_evaluate_all_without_profile(session, monkeypatch):
    monkeypatch.setattr(db_mod, "load_profile", lambda: None)
    upsert_notices([_notice("RN1", HOUSE_DTL_SECD_NM="민영")], session=session)
    evaluate_all(FilterConfig(), today=TODAY, session=session)
    m = session.scalar(select(MatchResult).where(MatchResult.pblanc_no == "applyhome:RN1"))
    assert m.matched is True and m.my_rank is None


# ── 대시보드: 1순위 → 2순위 → 판정불가 순 정렬 + 그룹 내 마감임박순 ──
def test_dashboard_sorted_by_rank(session):
    from src.web.app import matched_dashboard

    upsert_notices(
        [
            _notice("S1", RCEPT_ENDDE="2099-01-01"),  # 판정불가(null) — 마감 빠름
            _notice("S2", RCEPT_ENDDE="2099-06-01"),  # 2순위
            _notice("S3", RCEPT_ENDDE="2099-12-01"),  # 1순위 — 마감 가장 늦어도 최상단
            _notice("S4", RCEPT_ENDDE="2099-03-01"),  # 1순위 — 같은 그룹 내 마감임박 우선
        ],
        session=session,
    )
    save_match_results(
        [("applyhome:S1", True, []), ("applyhome:S2", True, [], "2순위"),
         ("applyhome:S3", True, [], "1순위"), ("applyhome:S4", True, [], "1순위")],
        session=session,
    )
    items = matched_dashboard(session, today=TODAY)
    assert [it["notice"].pblanc_no for it in items] == ["applyhome:S4", "applyhome:S3", "applyhome:S2", "applyhome:S1"]
    assert items[0]["my_rank"] == "1순위"


# ── 인덱스 렌더: 순위 칩 + 카드 배지/속성 ──
def test_index_rank_chip_and_badge(session):
    """인덱스의 순위는 저장값이 아니라 **로그인 회원 프로필**로 계산된다(Task 11, D17).

    그래서 여기선 배치 저장값 대신 회원 프로필(민영 공고 + 통장/예치금)로 1순위를 만든다.
    """
    from test_auth_routes import EMAIL, login_client

    from src.members import get_member_by_email, update_profile

    upsert_notices([_notice("S5", HOUSE_DTL_SECD_NM="민영")], session=session)
    save_match_results([("applyhome:S5", True, [], "1순위")], session=session)
    client = login_client()
    member = get_member_by_email(EMAIL, session=session)
    update_profile(
        member.id,
        {
            "region": "경기",
            "account_opened": date(2010, 1, 1),
            "account_balance_manwon": 5000,
            "is_household_head": True,
        },
        session=session,
    )
    r = client.get("/")
    assert 'data-ftype="rank"' in r.text
    assert 'data-rank="1순위"' in r.text
    # 디자인 개편으로 이모지(🏅) 제거 — 순위 배지는 텍스트만 렌더한다.
    # t06: 배지가 유형을 순위와 함께 읽히게 한다(판정 사유는 title 속성으로만 붙는다).
    assert 'class="rank rank-1"' in r.text
    assert "민영 · 1순위" in r.text
