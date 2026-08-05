"""3스텝 온보딩 라우터(Task 05) — postgres 필요, _db_available 게이트.

검증 지점:
- 스텝별 신뢰 경계 검증(allowlist): 각 스텝 POST 는 **그 스텝 필드만** 검증·저장한다.
- 진행상태(O1/O4): `onboarding_step` 은 DB 에 남고 `max(현재, 방금 완료한 step)` 으로만 오른다.
- 폼 UX: 실패 시 400 + 입력값 보존 + 필드 인라인 오류, 성공 시 303.
- 유도만 하고 차단하지 않는다(O5): 온보딩 미완성이어도 `GET /` 는 200.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import delete
from starlette.datastructures import FormData

from src.db import Member, MemberProfile, SessionLocal, engine, init_db
from src.members import create_member, get_member_by_email, get_profile, hash_password
from src.web.app import app
from src.web.onboarding import OnboardingStep1, _collect_errors, _step3_payload_from_form

from test_auth_routes import BASE_URL, login_client

EMAIL = "onboarding-tester@example.com"
# 가입 경계가 KISA 정책을 강제하므로(Task 04) 픽스처 비밀번호도 정책을 통과해야 한다.
PASSWORD = "Vu8#mQ2rTz"
OTHER_EMAIL = "onboarding-other@example.com"

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)
# 혼인 인정기간(7년)을 확실히 넘긴 날짜 — 경계에 걸치지 않게 8년 전으로 둔다.
EIGHT_YEARS_AGO = (TODAY - timedelta(days=365 * 8 + 3)).isoformat()

# 체크박스는 **체크된 것만** 전송된다(미체크 = 키 자체가 없음) — 브라우저 폼과 동일하게 구성.
STEP1 = {
    "birth_date": "1990-03-05",
    "household_type": "newlywed",
    "marriage_date": "2024-05-01",
    "dependents": "3",
    "children_minor": "2",
    "is_household_head": "on",
    "household_all_homeless": "on",
}

# 미체크로 남기는 것: household_head_owns_home, fl_ever_owned_house
STEP2 = {
    "car_value_manwon": "3000",
    "real_estate_manwon": "12000",
    "account_opened": "2015-02-10",
    "account_payment_count": "30",
    "account_balance_manwon": "1500",
    "income_monthly_manwon": "620",
    "income_base_manwon": "700",
    "owns_car": "on",
    "income_dual": "on",
}


def _step3(
    *,
    rows: list[tuple[str, str]] | None = None,
    pad_to: int = 10,
    preferred: tuple[str, ...] = ("newlywed", "pre_newlywed"),
    partners: bool = True,
) -> dict:
    """스텝3 폼 본문. 반복 필드는 dict 의 list 값으로 실어야 httpx 가 같은 키를 반복 전송한다
    (list[tuple] 을 넘기면 httpx 가 raw body 로 오해한다)."""
    rows = rows if rows is not None else [("서울", "2019-04-01")]
    # 화면은 늘 pad_to 행을 보낸다 — 빈 행은 서버가 버린다.
    pad = max(0, pad_to - len(rows))
    data: dict = {
        "residence_region": [r for r, _ in rows] + [""] * pad,
        "residence_since": [s for _, s in rows] + [""] * pad,
        "income_base_regions": "성남시",
        "interest_regions": "서울,인천",
        "preferred_types": list(preferred),
    }
    if partners:
        data |= {
            "partner_0_residence_region": "서울",
            "partner_0_income_base_region": "서울",
            "partner_0_lives_with_parents": "on",
            "partner_1_residence_region": "경기",
            "partner_1_income_base_region": "성남시",
            "partner_1_owns_home": "on",
        }
    return data


def _db_available() -> bool:
    try:
        engine.connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="postgres 미가용")


def _clear_members() -> None:
    init_db()
    with SessionLocal() as s:
        for t in (MemberProfile, Member):
            s.execute(delete(t))
        s.commit()


@pytest.fixture
def client():
    _clear_members()
    yield login_client(EMAIL, PASSWORD)
    _clear_members()


def _profile():
    with SessionLocal() as s:
        member = get_member_by_email(EMAIL, session=s)
        assert member is not None
        prof = get_profile(member.id, session=s)
        assert prof is not None
        s.expunge(prof)
        return prof


def _post(client: TestClient, step: int, data):
    return client.post(f"/onboarding/{step}", data=data, follow_redirects=False)


# ── ① 정상: 1→2→3 순차 제출 → 저장 + onboarding_step 전진 ────────────────────


def test_three_steps_advance_and_persist(client):
    r1 = _post(client, 1, STEP1)
    assert r1.status_code == 303, r1.text[:400]
    assert r1.headers["location"] == "/onboarding/2"
    assert _profile().onboarding_step == 1

    r2 = _post(client, 2, STEP2)
    assert r2.status_code == 303, r2.text[:400]
    assert r2.headers["location"] == "/onboarding/3"
    assert _profile().onboarding_step == 2

    r3 = _post(client, 3, _step3())
    assert r3.status_code == 303, r3.text[:400]
    assert r3.headers["location"] == "/"

    prof = _profile()
    assert prof.onboarding_step == 3
    # 스텝 1
    assert prof.birth_date == date(1990, 3, 5)
    assert prof.household_type == "newlywed"
    assert prof.marriage_date == date(2024, 5, 1)
    assert prof.is_household_head is True
    assert prof.household_all_homeless is True
    assert prof.dependents == 3 and prof.children_minor == 2
    # 스텝 2
    assert prof.owns_car is True and prof.car_value_manwon == 3000
    assert prof.real_estate_manwon == 12000
    assert prof.household_head_owns_home is False
    assert prof.fl_ever_owned_house is False
    assert prof.account_opened == date(2015, 2, 10)
    assert prof.account_payment_count == 30
    assert prof.account_balance_manwon == 1500
    assert prof.income_monthly_manwon == 620 and prof.income_base_manwon == 700
    assert prof.income_dual is True
    # 스텝 3 — residence_regions 는 residence_history 에서 파생된다(D3)
    assert prof.residence_history == [{"region": "서울", "since": "2019-04-01"}]
    assert prof.residence_regions == ["서울"]
    assert prof.income_base_regions == ["성남시"]
    assert prof.interest_regions == ["서울", "인천"]
    assert sorted(prof.preferred_types) == ["newlywed", "pre_newlywed"]
    assert prof.partners == [
        {
            "label": "본인",
            "lives_with_parents": True,
            "owns_home": False,
            "residence_region": "서울",
            "income_base_region": "서울",
        },
        {
            "label": "상대방",
            "lives_with_parents": False,
            "owns_home": True,
            "residence_region": "경기",
            "income_base_region": "성남시",
        },
    ]


def test_get_renders_saved_values_and_step_marker(client):
    assert _post(client, 1, STEP1).status_code == 303
    assert _post(client, 2, STEP2).status_code == 303

    r = client.get("/onboarding/2")
    assert r.status_code == 200
    # 저장값이 폼에 되살아난다
    assert 'value="30"' in r.text
    assert 'value="1500"' in r.text
    # 스텝 표시는 <ol> + aria-current="step"
    assert 'aria-current="step"' in r.text
    # 완료한 이전 스텝으로는 링크로 되돌아갈 수 있다
    assert 'href="/onboarding/1"' in r.text


def test_step3_get_renders_saved_rows_and_checked_preferences(client):
    assert _post(client, 3, _step3()).status_code == 303
    r = client.get("/onboarding/3")
    assert r.status_code == 200
    assert 'value="서울"' in r.text and 'value="2019-04-01"' in r.text
    # 체크된 선호전형과 파트너 블록이 복원된다
    assert r.text.count("checked") >= 3
    assert "partner_1_residence_region" in r.text


# ── ② 에러: 교차 필드 검증 ────────────────────────────────────────────────────


def test_newlywed_over_seven_years_rejected(client):
    r = _post(client, 1, {**STEP1, "marriage_date": EIGHT_YEARS_AGO})
    assert r.status_code == 400
    assert 'id="e-marriage_date"' in r.text
    assert "7년 이내" in r.text
    # 실패한 제출은 아무것도 저장하지 않는다
    prof = _profile()
    assert prof.onboarding_step == 0 and prof.birth_date is None


def test_newlywed_without_marriage_date_rejected(client):
    r = _post(client, 1, {**STEP1, "marriage_date": ""})
    assert r.status_code == 400
    assert "혼인신고일을 입력해주세요" in r.text
    assert _profile().onboarding_step == 0


def test_non_newlywed_without_marriage_date_is_fine(client):
    """7년 규칙은 신혼부부를 고른 경우에만 건다 — 일반 세대는 혼인신고일이 없어도 통과."""
    r = _post(client, 1, {**STEP1, "household_type": "general", "marriage_date": ""})
    assert r.status_code == 303
    prof = _profile()
    assert prof.household_type == "general" and prof.marriage_date is None


def test_pre_newlywed_requires_two_partners(client):
    data = {k: v for k, v in _step3().items() if not k.startswith("partner_1_")}
    r = _post(client, 3, data)
    assert r.status_code == 400
    assert 'id="e-partners"' in r.text
    assert "두 사람의 거주지" in r.text
    assert _profile().onboarding_step == 0


def test_pre_newlywed_partner_blank_region_rejected(client):
    r = _post(client, 3, {**_step3(), "partner_1_residence_region": ""})
    assert r.status_code == 400
    assert 'id="e-partners"' in r.text


def test_partners_not_required_without_pre_newlywed(client):
    """예비신혼을 고르지 않으면 파트너 입력은 요구하지 않고 저장도 하지 않는다."""
    r = _post(client, 3, _step3(preferred=("youth",), partners=False))
    assert r.status_code == 303
    assert _profile().partners == []


def test_preferred_type_outside_allowlist_rejected(client):
    r = _post(client, 3, _step3(preferred=("newlywed", "martian")))
    assert r.status_code == 400
    assert 'id="e-preferred_types"' in r.text
    assert _profile().preferred_types == []


# ── ③ 경계값 ─────────────────────────────────────────────────────────────────


def test_requires_login():
    anon = TestClient(app, base_url=BASE_URL)
    r = anon.get("/onboarding/1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    p = anon.post("/onboarding/1", data=STEP1, follow_redirects=False)
    assert p.status_code == 303 and p.headers["location"] == "/login"


def test_step_out_of_range_is_404(client):
    assert client.get("/onboarding/4").status_code == 404
    assert client.get("/onboarding/0").status_code == 404
    assert _post(client, 4, STEP1).status_code == 404


def test_residence_future_since_rejected(client):
    r = _post(client, 3, _step3(rows=[("서울", TOMORROW.isoformat())]))
    assert r.status_code == 400
    assert 'id="e-residence_history"' in r.text
    assert _profile().residence_history == []


def test_residence_eleven_rows_rejected(client):
    rows = [(f"지역{i}", "2020-01-01") for i in range(11)]
    r = _post(client, 3, _step3(rows=rows, pad_to=11))
    assert r.status_code == 400
    assert 'id="e-residence_history"' in r.text
    assert _profile().residence_history == []


def test_residence_exactly_ten_rows_accepted(client):
    rows = [(f"지역{i}", "2020-01-01") for i in range(10)]
    r = _post(client, 3, _step3(rows=rows))
    assert r.status_code == 303
    assert len(_profile().residence_history) == 10


def test_residence_empty_saves_empty_list(client):
    r = _post(client, 3, _step3(rows=[]))
    assert r.status_code == 303
    prof = _profile()
    assert prof.residence_history == []
    assert prof.residence_regions == []


def test_residence_row_without_since_is_kept(client):
    """거주 시작일은 선택 입력 — 지역만 있어도 행으로 남는다(기간은 조회 시 계산, D2)."""
    r = _post(client, 3, _step3(rows=[("부산", "")]))
    assert r.status_code == 303
    assert _profile().residence_history == [{"region": "부산", "since": None}]


def test_lower_step_resubmit_does_not_lower_progress(client):
    assert _post(client, 1, STEP1).status_code == 303
    assert _post(client, 2, STEP2).status_code == 303
    assert _profile().onboarding_step == 2

    again = _post(client, 1, {**STEP1, "dependents": "5"})
    assert again.status_code == 303
    prof = _profile()
    assert prof.onboarding_step == 2, "낮은 스텝 재제출로 진행상태가 내려가면 안 된다(O4)"
    assert prof.dependents == 5, "재제출한 값 자체는 반영되어야 한다"


def test_step_submit_does_not_clobber_other_steps(client):
    assert _post(client, 1, STEP1).status_code == 303
    assert _post(client, 2, STEP2).status_code == 303
    assert _post(client, 1, {**STEP1, "dependents": "5"}).status_code == 303
    prof = _profile()
    assert prof.account_payment_count == 30, "스텝1 재제출이 스텝2 값을 지우면 안 된다"
    assert prof.account_balance_manwon == 1500
    assert prof.owns_car is True


def test_negative_number_rejected_even_when_owns_car_unchecked(client):
    """조건부 표시는 JS 의 일이고 검증은 서버가 항상 한다 — owns_car 미체크여도 값을 본다."""
    data = {k: v for k, v in STEP2.items() if k != "owns_car"}
    r = _post(client, 2, {**data, "car_value_manwon": "-1"})
    assert r.status_code == 400
    assert 'id="e-car_value_manwon"' in r.text
    assert _profile().onboarding_step == 0


def test_invalid_date_rejected_with_field_error(client):
    r = _post(client, 1, {**STEP1, "birth_date": "1990-13-45"})
    assert r.status_code == 400
    assert 'id="e-birth_date"' in r.text
    assert "YYYY-MM-DD" in r.text


def test_error_rerender_preserves_input_and_escapes(client):
    """재렌더는 입력값을 되살리되 Jinja 자동이스케이프에 맡긴다 — 원문 태그가 나오면 안 된다."""
    payload = '<script>alert(1)</script>'
    data = _step3(rows=[(payload, "2019-04-01")], preferred=("martian",))
    r = _post(client, 3, data)
    assert r.status_code == 400
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text


def test_form_member_id_is_ignored(client):
    """쓰기 대상은 언제나 세션의 회원 — 폼이 보낸 member_id 는 무시한다(D14)."""
    with SessionLocal() as s:
        other = create_member(OTHER_EMAIL, hash_password(PASSWORD), session=s)
        other_id = other.id

    r = _post(client, 1, {**STEP1, "member_id": str(other_id)})
    assert r.status_code == 303
    assert _profile().birth_date == date(1990, 3, 5)
    with SessionLocal() as s:
        victim = get_profile(other_id, session=s)
        assert victim is not None
        assert victim.birth_date is None and victim.onboarding_step == 0


# ── ④ 배너: 유도만 하고 차단하지 않는다(O5) ─────────────────────────────────


def test_mismatched_repeated_field_lengths_do_not_crash():
    """손으로 만든 POST 가 두 반복 목록의 길이를 어긋나게 보내도 짝짓기가 터지지 않는다."""
    form = FormData(
        [("residence_region", "서울"), ("residence_region", "부산"), ("residence_since", "2019-04-01")]
    )
    payload = _step3_payload_from_form(form)
    assert payload["residence_history"] == [
        {"region": "서울", "since": "2019-04-01"},
        {"region": "부산", "since": ""},
    ]
    # 지역 없이 날짜만 있는 행은 의미가 없으므로 버린다
    orphan = _step3_payload_from_form(FormData([("residence_since", "2019-04-01")]))
    assert orphan["residence_history"] == []


def test_collect_errors_maps_cross_field_and_falls_back():
    """loc 가 빈 교차 필드 오류는 type 으로 필드에 되돌리고, 모르는 것은 폼 레벨로 떨어뜨린다."""
    with pytest.raises(ValidationError) as exc:
        OnboardingStep1.model_validate({"household_type": "newlywed"})
    assert _collect_errors(exc.value) == {
        "marriage_date": "신혼부부를 선택하면 혼인신고일을 입력해주세요"
    }

    class _Unmapped(BaseModel):
        @model_validator(mode="after")
        def _boom(self):
            raise PydanticCustomError("nobody_maps_this", "x")

    with pytest.raises(ValidationError) as exc2:
        _Unmapped.model_validate({})
    assert _collect_errors(exc2.value) == {"__form__": "입력값을 확인해주세요"}


def test_banner_present_until_complete_and_index_never_blocked(client):
    before = client.get("/")
    assert before.status_code == 200, "온보딩 미완성이어도 목록은 볼 수 있어야 한다(O5)"
    # 클래스 이름은 base.html 의 CSS 에도 등장하므로 마크업 쪽만 본다
    assert 'class="onboarding-banner"' in before.text
    assert 'href="/onboarding/1"' in before.text

    assert _post(client, 1, STEP1).status_code == 303
    mid = client.get("/")
    assert mid.status_code == 200
    assert 'href="/onboarding/2"' in mid.text, "이어하기 링크는 다음 미완성 스텝을 가리킨다"

    assert _post(client, 2, STEP2).status_code == 303
    assert _post(client, 3, _step3()).status_code == 303
    after = client.get("/")
    assert after.status_code == 200
    assert 'class="onboarding-banner"' not in after.text
    assert "/onboarding/" not in after.text
