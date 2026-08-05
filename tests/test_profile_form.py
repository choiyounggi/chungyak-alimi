"""프로필 입력/수정 폼 라우트(Task 10) — postgres 필요, _db_available 게이트.

검증 지점 3가지:
- 신뢰 경계 검증(allowlist): 날짜 형식·숫자 범위·household_type enum 을 서버가 다시 본다.
- 인가(D14): 쓰기 대상은 **세션의 member_id** 뿐 — 폼이 보낸 member_id 는 무시한다.
- 폼 UX: 검증 실패 시 저장하지 않고 입력값을 되살려 필드별 오류와 함께 재렌더한다.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from src.db import Member, MemberProfile, SessionLocal, engine, init_db
from src.members import (
    create_member,
    get_member_by_email,
    get_profile,
    hash_password,
    update_profile,
)
from src.web.app import app

from test_auth_routes import BASE_URL, login_client

EMAIL = "profile-tester@example.com"
# 가입 경계가 KISA 정책을 강제하므로(Task 04) 픽스처 비밀번호도 정책을 통과해야 한다.
PASSWORD = "Vu8#mQ2rTz"
OTHER_EMAIL = "profile-other@example.com"

# 체크박스는 **체크된 것만** 전송된다(미체크 = 키 자체가 없음) — 브라우저 폼과 동일하게 구성.
# 미체크로 남기는 것: won_within_5y, fl_ever_owned_house, fl_income_tax_5y,
#                    fl_currently_earning, household_head_owns_home
FULL_FORM = {
    "birth_date": "1990-03-05",
    "marriage_date": "2024-05-01",
    "homeless_since": "2020-01-01",
    "account_opened": "2015-02-10",
    "dependents": "3",
    "children_minor": "2",
    "real_estate_manwon": "12000",
    "region": "서울",
    "account_balance_manwon": "1500",
    "income_monthly_manwon": "620",
    "income_base_manwon": "700",
    "car_value_manwon": "3000",
    "household_type": "newlywed",
    "residence_regions": "서울, 경기",
    "income_base_regions": "성남시",
    "interest_regions": "서울,인천",
    "engaged": "on",
    "is_household_head": "on",
    "household_all_homeless": "on",
    "income_dual": "on",
    "is_first_home": "on",
}

# 빈 폼(모든 텍스트 필드 공백 + 체크박스 전부 미체크) — 경계값 케이스에 쓴다.
BLANK_FORM = {
    k: "" for k in FULL_FORM if k not in ("household_type", "engaged", "is_household_head",
                                          "household_all_homeless", "income_dual", "is_first_home")
} | {"household_type": "general"}


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


def _reset() -> None:
    init_db()
    with SessionLocal() as s:
        for t in (MemberProfile, Member):
            s.execute(delete(t))
        s.commit()


def _member_id(email: str = EMAIL) -> int:
    with SessionLocal() as s:
        m = get_member_by_email(email, session=s)
        assert m is not None, f"회원 없음: {email}"
        return m.id


@pytest.fixture
def client():
    _reset()
    yield login_client(EMAIL, PASSWORD)
    _reset()


# ── ① 정상: GET 이 현재 값을 채워 렌더 ──────────────────────────────────────
def test_get_profile_renders_current_values(client):
    mid = _member_id()
    with SessionLocal() as s:
        update_profile(
            mid,
            {
                "region": "서울",
                "dependents": 3,
                "household_type": "newlywed",
                "interest_regions": ["서울", "경기"],
                "birth_date": date(1990, 3, 5),
            },
            session=s,
        )
    r = client.get("/profile")
    assert r.status_code == 200
    assert 'name="region"' in r.text
    assert 'value="서울"' in r.text                     # 텍스트 필드 현재 값
    assert 'value="3"' in r.text                        # 숫자 필드 현재 값
    assert 'value="1990-03-05"' in r.text               # 날짜 현재 값
    assert "서울, 경기" in r.text                        # 지역 리스트 = 콤마 구분 재표시
    assert 'value="newlywed" selected' in r.text        # select 현재 선택값
    # D14: 회원 식별자는 세션에서만 온다 — 폼에 숨김 필드로 두지 않는다.
    assert 'name="member_id"' not in r.text


# ── ② 정상: POST 로 전 섹션 저장 → 재조회 반영 ────────────────────────────
def test_post_profile_saves_all_sections(client):
    r = client.post("/profile", data=FULL_FORM, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/profile")
    with SessionLocal() as s:
        p = get_profile(_member_id(), session=s)
    assert p.household_type == "newlywed"
    assert p.residence_regions == ["서울", "경기"]       # 콤마 구분 → list[str](공백 트림)
    assert p.income_base_regions == ["성남시"]
    assert p.interest_regions == ["서울", "인천"]
    assert p.birth_date == date(1990, 3, 5)
    assert p.account_opened == date(2015, 2, 10)
    assert p.dependents == 3
    assert p.children_minor == 2
    assert p.real_estate_manwon == 12000
    assert p.account_balance_manwon == 1500
    assert p.income_monthly_manwon == 620
    assert p.income_base_manwon == 700
    assert p.car_value_manwon == 3000
    assert p.region == "서울"
    assert p.engaged is True and p.is_household_head is True
    assert p.income_dual is True and p.is_first_home is True
    assert p.won_within_5y is False                     # 미체크 체크박스 → False
    assert p.fl_ever_owned_house is False
    assert p.household_head_owns_home is False


# ── ③ 에러: household_type 허용 외 값 → 400, 저장 안 됨 ────────────────────
def test_post_invalid_household_type_rejected(client):
    r = client.post(
        "/profile", data={**FULL_FORM, "household_type": "alien"}, follow_redirects=False
    )
    assert r.status_code == 400
    # 라벨에도 "세대유형"이 있으므로 오류 문구 전문으로 확인한다.
    assert "세대유형을 목록에서 선택해주세요" in r.text
    assert 'value="서울, 경기"' in r.text                # 입력값 되살림(다시 타이핑 안 시킴)
    with SessionLocal() as s:
        p = get_profile(_member_id(), session=s)
    assert p.household_type == "general"                # 기본값 그대로 — 저장 안 됨
    assert p.residence_regions == []                    # 같은 요청의 다른 필드도 저장 안 됨


# ── ④ 에러: 날짜 형식/음수 → 400, 필드별 안내 문구 ────────────────────────
def test_post_invalid_date_and_negative_number_rejected(client):
    r = client.post(
        "/profile",
        data={**FULL_FORM, "birth_date": "1990/03/05", "dependents": "-1"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "날짜를 YYYY-MM-DD 형식으로 입력해주세요" in r.text  # 무엇을 하면 되는지 알려주는 문구
    assert "0 이상의 숫자를 입력해주세요" in r.text
    assert 'aria-invalid="true"' in r.text              # 스크린리더에 오류 필드로 알림
    with SessionLocal() as s:
        assert get_profile(_member_id(), session=s).birth_date is None


# ── ⑤ 경계값: 빈 입력 → 지역 [], 날짜 None, 숫자 0/None ────────────────────
def test_post_blank_values_saved_as_empty(client):
    mid = _member_id()
    with SessionLocal() as s:                            # 먼저 값을 채워 두고 비워본다
        update_profile(
            mid,
            {"residence_regions": ["서울"], "birth_date": date(1990, 1, 1), "dependents": 5},
            session=s,
        )
    r = client.post("/profile", data=BLANK_FORM, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        p = get_profile(mid, session=s)
    assert p.residence_regions == []                     # 빈 지역 입력 → []
    assert p.income_base_regions == [] and p.interest_regions == []
    assert p.birth_date is None and p.account_opened is None
    assert p.dependents == 0 and p.real_estate_manwon == 0   # NOT NULL 숫자는 0
    assert p.income_monthly_manwon is None                   # nullable 숫자는 None
    assert p.region == ""
    assert p.engaged is False                                # 체크박스 전부 해제


# ── ⑥ 경계: 미로그인 → GET/POST 모두 303 /login (저장 없음) ────────────────
def test_profile_requires_login():
    _reset()
    c = TestClient(app, base_url=BASE_URL)
    r = c.get("/profile", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    r2 = c.post("/profile", data=FULL_FORM, follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/login"


# ── ⑦ 경계: INTEGER 상한 초과 숫자 → 400(저장 시 DB INSERT 가 터지기 전에 막는다) ──
def test_post_number_over_int_max_rejected(client):
    over = str(2_147_483_647 + 1)
    r = client.post(
        "/profile", data={**FULL_FORM, "real_estate_manwon": over}, follow_redirects=False
    )
    assert r.status_code == 400
    assert "0 이상의 숫자를 입력해주세요" in r.text
    with SessionLocal() as s:
        assert get_profile(_member_id(), session=s).real_estate_manwon == 0   # 저장 안 됨


# ── ⑧ XSS: 되살린 입력값은 이스케이프되어 마크업이 되지 않는다 ─────────────
def test_error_rerender_escapes_user_input(client):
    payload = '"><script>alert(1)</script>'
    r = client.post(
        "/profile",
        data={**FULL_FORM, "household_type": "alien", "region": payload},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text                    # 텍스트로만 렌더


# ── ⑨ 인가(D14): 폼이 보낸 member_id 는 무시 — 남의 프로필은 안 바뀐다 ─────
def test_form_member_id_is_ignored(client):
    with SessionLocal() as s:
        other_id = create_member(OTHER_EMAIL, hash_password(PASSWORD), session=s).id
    r = client.post(
        "/profile", data={**FULL_FORM, "member_id": str(other_id)}, follow_redirects=False
    )
    assert r.status_code == 303
    with SessionLocal() as s:
        assert get_profile(other_id, session=s).household_type == "general"   # 타인 불변
        assert get_profile(_member_id(), session=s).household_type == "newlywed"
