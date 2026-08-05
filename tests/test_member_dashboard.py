"""회원별 대시보드 온더플라이 순위 + 관심지역 필터 (postgres 필요, gated).

순위는 배치가 저장해 둔 MatchResult.my_rank 가 아니라 **요청 시점에 그 회원 프로필**로
계산한다(D17). 해당지역 판정은 거주지 ∪ 소득본거지(D18/D19)이며 순위와 독립이다.
관심지역 기본 필터는 서버가 카드마다 판정해 data-interest 로 내려주고, "전체 보기"는
순수 화면 상태라 클라이언트에서만 토글한다(D20).
"""
from __future__ import annotations

import copy
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
    engine,
    init_db,
    save_match_results,
    upsert_house_types,
    upsert_notices,
)
from src.members import get_member_by_email, update_profile
from src.models import ApplyhomeHouseType, ApplyhomeNotice
from src.web.app import app, member_dashboard

from test_applyhome import SAMPLE
from test_auth_routes import BASE_URL, login_client
from test_housetype import SAMPLE_HT

from fastapi.testclient import TestClient  # noqa: E402  (테스트 헬퍼 뒤 임포트 유지)

TODAY = date(2026, 7, 7)
EMAIL = "dashboard-member@example.com"
# 가입 경계가 KISA 정책을 강제하므로(Task 04) 픽스처 비밀번호도 정책을 통과해야 한다.
PASSWORD = "Vu8#mQ2rTz"

# 1순위가 나오는 프로필: 통장 16년(수도권 비규제 요건 1년) + 예치금 충분 + 세대주.
RANK1_PROFILE = {
    "region": "서울",
    "account_opened": date(2010, 1, 1),
    "account_balance_manwon": 5000,
    "is_household_head": True,
    "won_within_5y": False,
}


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


def _notice(pblanc_no: str, area: str, *, dtl: str = "민영", endde: str = "2099-12-31"):
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


def _reset(session) -> None:
    for t in (Bookmark, MatchResult, NoticeHouseType, Notice, MemberProfile, Member):
        session.execute(delete(t))
    session.commit()


@pytest.fixture
def seeded():
    """공고 3건(서울 민영 / 부산 민영 / 서울 국민=판정불가) + 로그인 회원. 반환: (session, member_id)."""
    init_db()
    s = SessionLocal()
    _reset(s)
    for pid, area, dtl, endde in (
        ("SEOUL", "서울", "민영", "2099-12-31"),
        ("BUSAN", "부산", "민영", "2099-06-30"),
        ("PUBLIC", "서울", "국민", "2099-01-31"),
    ):
        upsert_notices([_notice(pid, area, dtl=dtl, endde=endde)], session=s)
        upsert_house_types(
            [ApplyhomeHouseType.model_validate({**SAMPLE_HT, "PBLANC_NO": pid})], session=s
        )
    save_match_results(
        [(f"applyhome:{p}", True, []) for p in ("SEOUL", "BUSAN", "PUBLIC")], session=s
    )
    client = login_client(EMAIL, PASSWORD)          # 회원 생성 + 세션 쿠키
    member_id = get_member_by_email(EMAIL, session=s).id
    yield s, member_id, client
    _reset(s)
    s.close()


def _set_profile(session, member_id: int, **over):
    update_profile(member_id, {**RANK1_PROFILE, **over}, session=session)


# ── ① 정상: 거주지 매칭 공고가 해당지역 1순위로 계산된다 ──
def test_residence_region_gives_in_area_rank1(seeded):
    s, mid, _c = seeded
    _set_profile(s, mid, residence_regions=["서울특별시"])   # 풀네임 입력도 정규화되어 매칭
    by_no = {it["notice"].pblanc_no: it for it in member_dashboard(s, mid, today=TODAY)}
    assert by_no["applyhome:SEOUL"]["my_rank"] == "1순위"
    assert by_no["applyhome:SEOUL"]["in_area"] is True
    assert by_no["applyhome:BUSAN"]["my_rank"] == "1순위"    # 순위는 지역과 무관(D19)
    assert by_no["applyhome:BUSAN"]["in_area"] is False      # 기타지역
    assert by_no["applyhome:PUBLIC"]["my_rank"] is None      # 국민주택 → 판정 미지원
    assert by_no["applyhome:PUBLIC"]["in_area"] is True      # 지역 판정은 그래도 한다


# ── ② 정상: 소득본거지도 해당지역으로 인정된다(D19 사용자 지정 정책) ──
def test_income_base_region_also_counts_as_in_area(seeded):
    s, mid, _c = seeded
    _set_profile(s, mid, residence_regions=[], income_base_regions=["부산"])
    by_no = {it["notice"].pblanc_no: it for it in member_dashboard(s, mid, today=TODAY)}
    assert by_no["applyhome:BUSAN"]["in_area"] is True
    assert by_no["applyhome:SEOUL"]["in_area"] is False


# ── ③ 정렬: 해당지역 우선 → 1순위 → 2순위 → 판정불가 → 마감임박순 ──
def test_sorted_in_area_first_then_rank(seeded):
    s, mid, _c = seeded
    _set_profile(s, mid, residence_regions=["서울"])
    order = [it["notice"].pblanc_no for it in member_dashboard(s, mid, today=TODAY)]
    # 부산은 1순위지만 기타지역이라, 해당지역의 판정불가(PUBLIC)보다 뒤로 간다.
    assert order == ["applyhome:SEOUL", "applyhome:PUBLIC", "applyhome:BUSAN"]


# ── ④ 온더플라이: 저장된 MatchResult.my_rank 가 아니라 회원 프로필로 계산한다 ──
def test_rank_is_computed_from_member_profile_not_stored_rank(seeded):
    s, mid, _c = seeded
    save_match_results([("applyhome:SEOUL", True, [], "1순위")], session=s)  # 배치가 저장한 값
    _set_profile(s, mid, account_opened=None, account_balance_manwon=0)      # 통장 없는 회원
    by_no = {it["notice"].pblanc_no: it for it in member_dashboard(s, mid, today=TODAY)}
    assert by_no["applyhome:SEOUL"]["my_rank"] == "2순위"   # 저장값(1순위)을 쓰지 않는다


# ── ⑤ 경계: 프로필이 완전히 빈 회원 — 예외 없이 2순위/기타지역 ──
def test_empty_profile_is_second_rank_and_out_of_area(seeded):
    s, mid, _c = seeded
    by_no = {it["notice"].pblanc_no: it for it in member_dashboard(s, mid, today=TODAY)}
    assert by_no["applyhome:SEOUL"]["my_rank"] == "2순위"
    assert by_no["applyhome:SEOUL"]["in_area"] is False     # 거주지 미입력 → 기타지역
    assert all(it["in_interest"] is True for it in by_no.values())  # 관심지역 미입력 → 전체


# ── ⑥ 관심지역: 밖의 공고는 기본 미표시(data-interest=0), "전체 보기" 토글 제공 ──
def test_interest_region_default_filter_and_toggle(seeded):
    s, mid, client = seeded
    _set_profile(s, mid, residence_regions=["서울"], interest_regions=["서울"])
    r = client.get("/")
    assert r.status_code == 200
    # 서버가 카드마다 관심지역 여부를 판정해 내려준다(지역 정규화는 서버 로직 재사용)
    assert 'data-pblanc="applyhome:SEOUL"' in r.text
    assert 'data-pblanc="applyhome:BUSAN"' in r.text        # 카드는 내려오되 기본 숨김 대상
    seoul = _card_attrs(r.text, "applyhome:SEOUL")
    busan = _card_attrs(r.text, "applyhome:BUSAN")
    assert seoul["data-interest"] == "1"
    assert busan["data-interest"] == "0"
    assert seoul["data-inarea"] == "1"
    assert busan["data-inarea"] == "0"
    # "전체 보기" 토글은 눌리지 않은 상태로 렌더된다(클라이언트 화면 상태)
    assert 'id="show-all-regions"' in r.text
    assert "전체 보기" in r.text
    assert 'aria-pressed="false"' in r.text


# ── ⑦ 경계: interest_regions 가 비면 기본 렌더가 전체 — 토글 자체가 없다 ──
def test_empty_interest_regions_shows_everything(seeded):
    s, mid, client = seeded
    _set_profile(s, mid, residence_regions=["서울"], interest_regions=[])
    r = client.get("/")
    assert _card_attrs(r.text, "applyhome:SEOUL")["data-interest"] == "1"
    assert _card_attrs(r.text, "applyhome:BUSAN")["data-interest"] == "1"
    assert 'id="show-all-regions"' not in r.text     # 전부 보이는데 "전체 보기"는 무의미


# ── ⑧ 경계: 지역 미상(area_nm=None) 공고 — 해당지역/관심지역 모두 False ──
def test_notice_without_area_is_out_of_area_and_interest(seeded):
    """지역을 모르는 공고는 "해당지역"도 "관심지역"도 아니다(안전하게 제외).

    목록에서 사라지지는 않는다 — 카드는 내려가고 "전체 보기"로 볼 수 있다.
    """
    s, mid, client = seeded
    upsert_notices([_notice("NOAREA", None)], session=s)
    save_match_results([("applyhome:NOAREA", True, [])], session=s)
    _set_profile(s, mid, residence_regions=["서울"], interest_regions=["서울"])
    by_no = {it["notice"].pblanc_no: it for it in member_dashboard(s, mid, today=TODAY)}
    assert by_no["applyhome:NOAREA"]["in_area"] is False
    assert by_no["applyhome:NOAREA"]["in_interest"] is False
    attrs = _card_attrs(client.get("/").text, "applyhome:NOAREA")
    assert attrs["data-interest"] == "0" and attrs["data-inarea"] == "0"


# ── ⑨ 렌더: 해당지역 1순위 배지 ──
def test_index_renders_in_area_rank_badge(seeded):
    s, mid, client = seeded
    _set_profile(s, mid, residence_regions=["서울"])
    text = client.get("/").text
    assert "해당지역 1순위" in text
    assert 'class="rank rank-1"' in text


# ── ⑨ 인가: 미로그인 / → 303 /login ──
def test_index_requires_login(seeded):
    r = TestClient(app, base_url=BASE_URL).get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# ── ⑩ 회귀: 기존 칩/지도/뷰포트 배선이 그대로 렌더된다 ──
def test_existing_chip_and_map_wiring_still_renders(seeded, monkeypatch):
    from src.web import app as webapp

    s, mid, client = seeded
    _set_profile(s, mid, residence_regions=["서울"], interest_regions=["서울"])
    monkeypatch.setattr(webapp.settings, "kakao_js_key", "TESTKAKAOKEY")
    text = client.get("/").text
    assert 'data-ftype="agency"' in text and 'data-ftype="rank"' in text
    assert 'data-area="서울"' in text                 # 칩 필터가 보는 카드 속성
    assert 'data-rank="1순위"' in text
    assert 'id="chungyak-map"' in text                # 지도 컨테이너
    assert "chungyakApplyList" in text                # 뷰포트 ∩ 칩 필터 훅
    assert "js-empty" in text


def _card_attrs(html: str, pblanc_no: str) -> dict:
    """카드 div 하나의 data-* 속성을 뽑는다(정규식 — 순수 문자열 파싱)."""
    import re

    m = re.search(r'<div class="card"[^>]*data-pblanc="%s"[^>]*>' % re.escape(pblanc_no), html)
    assert m, f"카드를 찾지 못함: {pblanc_no}"
    return dict(re.findall(r'(data-[a-z]+)="([^"]*)"', m.group(0)))
