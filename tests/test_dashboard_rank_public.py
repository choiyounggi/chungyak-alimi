"""국민주택 순위의 대시보드 표시 + 선호 전형 배지/토글 (postgres 필요, gated).

국민 공고는 민영 가점제가 아니라 순차제(가입기간·납입횟수)로 판정되므로
`judge_rank_public` 기준 순위가 카드에 실려야 한다(R2). 카드는 유형(`housing_type`)과
판정 사유(`rank_reasons`)를 함께 싣고, 회원 선호 전형 해당 여부(`preferred_hits`)는
**서버가** 판정해 내려준다 — 라벨 정규화 로직을 JS 에 복제하지 않는다(D4).

픽스처는 이 파일이 스스로 소유한다(다른 테스트 파일의 픽스처를 재사용하지 않는다) —
테스트 간 결합을 피하기 위해서다.
"""
from __future__ import annotations

import copy
import re
from datetime import date

import pytest
from sqlalchemy import delete

from src.db import (
    Bookmark,
    MatchResult,
    Member,
    MemberProfile,
    Notice,
    NoticeHouseType,
    SessionLocal,
    add_bookmark,
    engine,
    init_db,
    save_match_results,
    upsert_house_types,
    upsert_notices,
)
from src.members import get_member_by_email, update_profile
from src.models import ApplyhomeHouseType, ApplyhomeNotice
from src.web.app import (
    _preferred_hits,
    bookmarked_dashboard,
    matched_dashboard,
    member_dashboard,
)

from test_applyhome import SAMPLE
from test_auth_routes import login_client
from test_housetype import SAMPLE_HT

TODAY = date(2026, 7, 7)
# 이 파일 전용 계정 — 하드코딩 공용 주소를 쓰면 다른 파일과 유니크 제약이 충돌한다.
EMAIL = "dash-rank-public@example.com"
PASSWORD = "Vu8#mQ2rTz"

# 국민주택 1순위가 나오는 프로필(수도권 비규제: 가입 12개월·납입 12회가 하한).
# 납입횟수만 케이스별로 갈아끼운다.
BASE_PROFILE = {
    "region": "서울",
    "account_opened": date(2010, 1, 1),
    "account_balance_manwon": 5000,
    "is_household_head": True,
    "household_all_homeless": True,
    "won_within_5y": False,
    "residence_regions": ["서울"],
}

# _dashboard_item 이 이 변경 전부터 돌려주던 키 — 하나라도 사라지면 지도·북마크 화면이 깨진다.
LEGACY_KEYS = {
    "notice",
    "my_rank",
    "specials",
    "adres",
    "lat",
    "lng",
    "price_lo",
    "price_hi",
    "area_lo",
    "area_hi",
    "deadline",
    "dday",
    "bookmarked",
    "in_area",
    "in_interest",
}


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


def _notice(pblanc_no: str, area: str, *, dtl: str, endde: str):
    d = copy.deepcopy(SAMPLE)
    d.update(
        {
            "PBLANC_NO": pblanc_no,
            "HOUSE_MANAGE_NO": pblanc_no,
            "SUBSCRPT_AREA_CODE_NM": area,
            "HOUSE_DTL_SECD_NM": dtl,
            "RCEPT_ENDDE": endde,
        }
    )
    return ApplyhomeNotice.model_validate(d)


def _house_type(pblanc_no: str, **raw):
    """주택형 1건. raw 추가 필드(예: NWBB_HSHLDCO)로 특별공급 라벨을 만든다."""
    return ApplyhomeHouseType.model_validate({**SAMPLE_HT, "PBLANC_NO": pblanc_no, **raw})


def _reset(session) -> None:
    for t in (Bookmark, MatchResult, NoticeHouseType, Notice, MemberProfile, Member):
        session.execute(delete(t))
    session.commit()


def _card_attrs(html: str, pblanc_no: str) -> dict:
    """카드 div 하나의 data-* 속성을 뽑는다(정규식 — 순수 문자열 파싱)."""
    m = re.search(r'<div class="card"[^>]*data-pblanc="%s"[^>]*>' % re.escape(pblanc_no), html)
    assert m, f"카드를 찾지 못함: {pblanc_no}"
    return dict(re.findall(r'(data-[a-z]+)="([^"]*)"', m.group(0)))


@pytest.fixture
def seeded():
    """공고 3건 + 로그인 회원. 반환: (session, member_id, client).

    PUB  = 서울·국민(마감 최이름), PRIV = 서울·민영, NWBB = 경기·국민 + 신혼부부 특별공급.
    """
    init_db()
    s = SessionLocal()
    _reset(s)
    for pid, area, dtl, endde, ht_raw in (
        ("PUB", "서울", "국민", "2099-01-31", {}),
        ("PRIV", "서울", "민영", "2099-12-31", {}),
        ("NWBB", "경기", "국민", "2099-06-30", {"NWBB_HSHLDCO": 12}),
    ):
        upsert_notices([_notice(pid, area, dtl=dtl, endde=endde)], session=s)
        upsert_house_types([_house_type(pid, **ht_raw)], session=s)
    save_match_results([(f"applyhome:{p}", True, []) for p in ("PUB", "PRIV", "NWBB")], session=s)
    client = login_client(EMAIL, PASSWORD)
    member_id = get_member_by_email(EMAIL, session=s).id
    yield s, member_id, client
    _reset(s)
    s.close()


def _set_profile(session, member_id: int, **over):
    update_profile(member_id, {**BASE_PROFILE, **over}, session=session)


def _by_no(session, member_id: int) -> dict:
    return {it["notice"].pblanc_no: it for it in member_dashboard(session, member_id, today=TODAY)}


# ── ① 정상: 국민 공고 + 납입 13회(하한 초과) → 1순위, 유형이 카드에 실린다 ──
def test_public_notice_rank1_with_enough_payments(seeded):
    s, mid, _c = seeded
    _set_profile(s, mid, account_payment_count=13)
    pub = _by_no(s, mid)["applyhome:PUB"]
    assert pub["my_rank"] == "1순위"
    assert pub["housing_type"] == "국민"          # 민영 기준이 아니라 국민 기준으로 판정됐다
    assert pub["in_area"] is True


# ── ② 에러/차등: 납입 부족 → 2순위 + 사유 문구 계약까지 단언 ──
def test_public_notice_rank2_with_payment_reason(seeded):
    s, mid, _c = seeded
    _set_profile(s, mid, account_payment_count=5)
    pub = _by_no(s, mid)["applyhome:PUB"]
    assert pub["my_rank"] == "2순위"
    assert pub["housing_type"] == "국민"
    reasons = pub["rank_reasons"]
    assert any("납입횟수" in r for r in reasons), reasons
    # 사유는 화면에 그대로 노출되므로 문구(부족분)까지 계약으로 못박는다.
    assert "5회<12회" in " ".join(reasons), reasons


# ── ③ 경계(off-by-one): 납입횟수가 하한과 정확히 같으면 1순위(하한 포함) ──
def test_public_notice_payment_count_lower_bound_is_inclusive(seeded):
    s, mid, _c = seeded
    _set_profile(s, mid, account_payment_count=12)
    pub = _by_no(s, mid)["applyhome:PUB"]
    assert pub["my_rank"] == "1순위"
    assert not any("납입횟수" in r for r in pub["rank_reasons"]), pub["rank_reasons"]


# ── ④ 경계: 프로필 행이 아예 없는 회원 — 예외 없이 판정 없음 + GET / 200 ──
def test_member_without_profile_row_has_no_rank(seeded):
    s, mid, client = seeded
    s.execute(delete(MemberProfile).where(MemberProfile.member_id == mid))
    s.commit()
    items = member_dashboard(s, mid, today=TODAY)   # 예외 없이 통과해야 한다
    assert len(items) == 3
    for it in items:
        assert it["my_rank"] is None
        assert it["housing_type"] is None
        assert it["rank_reasons"] == []
        assert it["preferred_hits"] == []
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="only-preferred"' not in r.text       # 선호 미지정 → 토글 없음


# ── ⑤ 경계: preferred_types 가 빈 리스트면 토글을 렌더하지 않는다 ──
def test_empty_preferred_types_renders_no_toggle(seeded):
    s, mid, client = seeded
    _set_profile(s, mid, account_payment_count=13, preferred_types=[])
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="only-preferred"' not in r.text
    # 그룹 전체가 렌더되지 않는다. ("선호 전형만" 문자열만으로는 IIFE 주석에도 걸려 무의미하다)
    assert '<span class="filter-label">선호 전형</span>' not in r.text
    assert ">선호 전형만</button>" not in r.text
    # 카드는 전부 내려오되 필터 대상이 아님을 서버가 표시한다.
    assert _card_attrs(r.text, "applyhome:NWBB")["data-preferred"] == "0"


# ── ⑥ 정상: 선호 전형을 지정하면 배지 + 토글이 보이고, 해당 카드만 1로 표시된다 ──
def test_preferred_types_render_badge_and_toggle(seeded):
    s, mid, client = seeded
    _set_profile(s, mid, account_payment_count=13, preferred_types=["newlywed"])
    r = client.get("/")
    assert r.status_code == 200
    # 토글은 관심지역 토글과 같은 컴포넌트(button.chip + aria-pressed)로 렌더된다.
    assert '<button type="button" class="chip" id="only-preferred" aria-pressed="false">선호 전형만</button>' in r.text
    assert '<span class="filter-label">선호 전형</span>' in r.text
    assert "신혼부부 선호" in r.text                    # 카드 배지(자동이스케이프 경로)
    # 신혼부부 특별공급이 있는 공고만 선호 해당 — 서버 판정 결과가 카드에 실린다.
    assert _card_attrs(r.text, "applyhome:NWBB")["data-preferred"] == "1"
    assert _card_attrs(r.text, "applyhome:PUB")["data-preferred"] == "0"
    # 배지가 유형과 순위를 함께 읽히게 한다(국민 공고에 가점은 내세우지 않는다).
    assert "국민 · 해당지역 1순위" in r.text
    assert "민영 · 해당지역 1순위" in r.text
    # 판정 사유는 카드 본문이 아니라 title 속성으로만 붙는다.
    assert re.search(r'<span class="rank rank-1" title="[^"]+">국민 · 해당지역 1순위</span>', r.text)


# ── ⑦ 회귀: 민영 공고 판정은 이 변경 전후로 동일하다 ──
def test_private_notice_rank_unchanged(seeded):
    s, mid, _c = seeded
    _set_profile(s, mid, account_payment_count=0)   # 납입횟수는 민영 순위와 무관
    priv = _by_no(s, mid)["applyhome:PRIV"]
    assert priv["housing_type"] == "민영"
    assert priv["my_rank"] == "1순위"               # 통장 16년 + 예치금 5000만 → 1순위(불변)
    assert priv["in_area"] is True


# ── ⑧ 회귀: 다른 두 호출자는 새 인자 없이도 그대로 동작한다(기존 키 전부 유지) ──
def test_other_dashboard_callers_unaffected(seeded):
    s, mid, _c = seeded
    _set_profile(s, mid, account_payment_count=13)
    add_bookmark(mid, "applyhome:PUB", session=s)
    matched = matched_dashboard(s, mid, today=TODAY)
    booked = bookmarked_dashboard(s, mid, today=TODAY)
    assert len(matched) == 3
    assert len(booked) == 1
    for it in matched + booked:
        assert LEGACY_KEYS <= set(it), LEGACY_KEYS - set(it)   # 기존 키 제거·개명 금지
        # 이 두 경로는 회원 프로필 판정을 하지 않으므로 새 키는 전부 비어 있다.
        assert it["housing_type"] is None
        assert it["rank_reasons"] == []
        assert it["preferred_hits"] == []


# ── ⑧-2 회귀: 같은 매크로를 쓰는 북마크 화면이 그대로 렌더된다(새 표현은 전부 가드됨) ──
def test_bookmarks_page_still_renders(seeded):
    s, mid, client = seeded
    _set_profile(s, mid, account_payment_count=13, preferred_types=["newlywed"])
    add_bookmark(mid, "applyhome:NWBB", session=s)
    r = client.get("/bookmarks")
    assert r.status_code == 200
    assert 'data-pblanc="applyhome:NWBB"' in r.text
    # 북마크 경로는 회원 판정을 하지 않으므로 순위·유형·선호 표현이 하나도 나오지 않는다.
    assert _card_attrs(r.text, "applyhome:NWBB")["data-preferred"] == "0"
    assert "신혼부부 선호" not in r.text
    assert "국민 · " not in r.text
    assert 'id="only-preferred"' not in r.text


# ── ⑨ 정렬 불변: 해당지역 → 순위 → 마감임박 ──
def test_sort_order_unchanged(seeded):
    s, mid, _c = seeded
    _set_profile(s, mid, account_payment_count=13)
    order = [it["notice"].pblanc_no for it in member_dashboard(s, mid, today=TODAY)]
    # PUB/PRIV 는 해당지역(서울) 1순위 → 마감임박순, NWBB 는 경기라 기타지역이라 뒤로.
    assert order == ["applyhome:PUB", "applyhome:PRIV", "applyhome:NWBB"]


# ── 선호 전형 매칭 헬퍼 단위 테스트(정상·경계) ──
def test_preferred_hits_matches_special_labels():
    assert _preferred_hits(["신혼부부", "다자녀"], ["newlywed"]) == ["신혼부부"]
    assert _preferred_hits(["신혼부부"], ["pre_newlywed"]) == ["신혼부부"]
    assert _preferred_hits(["청년"], ["youth"]) == ["청년"]


def test_preferred_hits_special_means_all_labels_of_this_notice():
    assert _preferred_hits(["신혼부부", "청년"], ["special"]) == ["신혼부부", "청년"]


def test_preferred_hits_general_always_matches():
    # 일반공급은 모든 공고에 있으므로 특공 라벨이 없어도 해당한다.
    assert _preferred_hits([], ["general"]) == ["일반"]


def test_preferred_hits_empty_inputs():
    assert _preferred_hits([], []) == []                  # 선호 미지정 → 항상 빈 결과
    assert _preferred_hits(["신혼부부"], []) == []
    assert _preferred_hits([], ["newlywed"]) == []        # 해당 특공이 없는 공고
    assert _preferred_hits([], ["unknown_code"]) == []     # 모르는 코드는 무시(예외 없음)


def test_preferred_hits_deduplicates_and_sorts():
    # 신혼/예비신혼을 함께 고르면 같은 라벨이 두 번 나오지 않는다.
    assert _preferred_hits(["신혼부부"], ["newlywed", "pre_newlywed"]) == ["신혼부부"]
