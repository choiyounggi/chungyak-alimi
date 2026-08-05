from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from ..config import settings
from ..db import (
    AGENCIES,
    SUPERSEDED_REASON,
    Bookmark,
    MatchResult,
    MemberProfile,
    Notice,
    SessionLocal,
    add_bookmark,
    bookmarked_pblanc_nos,
    house_types_of,
    init_db,
    remove_bookmark,
)
from ..filters import load_filter_config
from ..members import get_profile, profile_from_member, update_profile
from ..regions import region_matches
from ..scoring import judge_notice, judge_rank, judge_rank_public, load_profile
from . import auth, onboarding
from .auth import current_member_id, require_login
from .onboarding import PREFERRED_TYPE_LABELS
from .forms import (
    COUPLE_HOUSEHOLD_TYPES,
    HOUSEHOLD_TYPES,
    _Count,
    _field_error,
    _OptCount,
    _OptDate,
    _REGION_FIELDS,
    _Regions,
)

logger = logging.getLogger(__name__)

_DEFAULT_SESSION_SECRET = "chungyak-alimi-dev-secret-change-me"

app = FastAPI(title="청약 알리미")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=settings.session_https_only,  # HTTPS 전용 쿠키(Secure)
    same_site="lax",
    max_age=60 * 60 * 24 * 14,
)
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# 배치보다 웹이 먼저 재기동되는 배포에서도 스키마(my_rank 등)가 준비되도록 보장
init_db()

app.include_router(auth.router)
app.include_router(onboarding.router)

# 특별공급 세대수 필드(raw) → 라벨
SPECIAL_SUPPLY_LABELS = {
    "LFE_FRST_HSHLDCO": "생애최초",
    "NWBB_HSHLDCO": "신혼부부",
    "MNYCH_HSHLDCO": "다자녀",
    "OLD_PARNTS_SUPORT_HSHLDCO": "노부모부양",
    "INSTT_RECOMEND_HSHLDCO": "기관추천",
    "NWWDS_HSHLDCO": "신생아",
    "YGMN_HSHLDCO": "청년",
    "TRANSR_INSTT_ENFSN_HSHLDCO": "이전기관",
    "ETC_HSHLDCO": "기타",
}

# 회원 선호 전형(scoring.PREFERRED_TYPES) → 공고 특별공급 라벨(SPECIAL_SUPPLY_LABELS) 매칭.
# "special"(특별공급 전반)/"general"(일반공급)은 특정 라벨에 대응하지 않아 아래 함수가 따로 다룬다.
_PREFERRED_TYPE_SPECIALS: dict[str, frozenset[str]] = {
    "newlywed": frozenset({"신혼부부"}),
    "pre_newlywed": frozenset({"신혼부부"}),  # 예비신혼도 신혼부부 특별공급 대상이다
    "youth": frozenset({"청년"}),
}


def _preferred_hits(specials: list[str], preferred: list[str]) -> list[str]:
    """이 공고가 회원 선호 전형 중 무엇에 해당하는가 → 표시용 한글 라벨(정렬·중복제거).

    선호를 지정하지 않았으면 항상 빈 리스트다 — 미지정 회원에겐 배지도 토글도 내지 않는다.
    판정은 서버가 한 번만 한다(라벨 정규화 로직을 JS 에 복제하지 않는다).
    """
    if not preferred:
        return []
    have = set(specials)
    hits: set[str] = set()
    for code in preferred:
        if code == "general":
            hits.add("일반")  # 일반공급은 모든 공고에 있다
        elif code == "special":
            hits |= have  # 특별공급 전반 — 이 공고가 가진 특공 라벨 전부
        else:
            hits |= have & _PREFERRED_TYPE_SPECIALS.get(code, frozenset())
    return sorted(hits)


# 규제/특성 플래그(raw 필드 → 라벨). 값이 'Y'/'N' 또는 코드.
REGULATION_FLAGS = {
    "SPECLT_RDN_EARTH_AT": "투기과열지구",
    "PARCPRC_ULS_AT": "분양가상한제",
    "PUBLIC_HOUSE_SPCLW_APPLC_AT": "공공주택특별법",
    "LRSCL_BLDLND_AT": "대규모택지",
}

if not settings.web_user or not settings.web_password:
    logger.warning(
        "웹 인증 미설정(WEB_USER/WEB_PASSWORD 비어있음) — 공고 상세가 인증 없이 노출됩니다"
        "(대시보드·북마크는 회원 로그인으로 보호됨)."
    )
elif settings.session_secret == _DEFAULT_SESSION_SECRET:
    # 인증은 켰지만 세션 서명키가 기본값이면, 키를 아는 누구나 authed 쿠키를 위조해 우회 가능
    logger.warning(
        "SESSION_SECRET이 기본값입니다 — 세션 쿠키 위조로 인증 우회 위험. "
        ".env에 랜덤 SESSION_SECRET(예: openssl rand -hex 32)을 설정하세요."
    )


def _auth_enabled() -> bool:
    return bool(settings.web_user and settings.web_password)


def _authed(request: Request) -> bool:
    """인증이 꺼져있으면(로컬) 항상 통과, 켜져있으면 세션 로그인 여부."""
    return not _auth_enabled() or request.session.get("authed") is True


def _dashboard_item(
    session,
    n,
    my_rank,
    today: date,
    bookmarked: bool,
    *,
    in_area: bool | None = None,
    in_interest: bool = True,
    housing_type: str | None = None,
    rank_reasons: list[str] | None = None,
    preferred: list[str] | None = None,
) -> dict:
    """공고 1건 → 카드용 dict(분양가·면적·D-day·좌표·특공·북마크). 대시보드/북마크 공용."""
    hts = house_types_of(n.pblanc_no, session=session)
    prices = [h.lttot_top_amount for h in hts if h.lttot_top_amount]
    areas = [float(h.suply_ar) for h in hts if h.suply_ar is not None]
    deadlines = [d for d in (n.rcept_endde, n.spsply_rcept_endde) if d]
    deadline = max(deadlines) if deadlines else None
    # 카드 필터링용 특공 라벨: 주택형 raw 세대수 + 이름 기반(신혼희망타운 등 LH엔 세대수 필드가 없음)
    specials = {
        label
        for ht in hts
        for key, label in SPECIAL_SUPPLY_LABELS.items()
        if _int(ht.raw.get(key)) > 0
    }
    if "신혼" in ((n.house_secd_nm or "") + (n.house_nm or "")):
        specials.add("신혼부부")
    centroid = _polygon_centroid((n.raw or {}).get("_polygon"))
    return {
        "notice": n,
        "my_rank": my_rank,
        "specials": sorted(specials),
        "adres": n.hsslpy_adres or (n.raw or {}).get("HSSPLY_ADRES"),
        "lat": centroid[0] if centroid else None,
        "lng": centroid[1] if centroid else None,
        "price_lo": min(prices) if prices else None,
        "price_hi": max(prices) if prices else None,
        "area_lo": min(areas) if areas else None,
        "area_hi": max(areas) if areas else None,
        "deadline": deadline,
        "dday": (deadline - today).days if deadline else None,
        "bookmarked": bookmarked,
        # 해당지역 여부(거주지 ∪ 소득본거지). 지역 판정을 하지 않은 문맥에선 None.
        "in_area": in_area,
        # 회원 관심지역에 드는가 — 관심지역 미입력이면 전부 True(폴백=전체).
        "in_interest": in_interest,
        # 공고 유형("민영"/"국민"). judge_notice 가 판별한 값만 싣는다 — app.py 는 재판별하지 않는다.
        "housing_type": housing_type,
        # 순위 판정 사유(표시 전용). 호출자 간 리스트 공유를 막으려 새 리스트로 복사한다.
        "rank_reasons": list(rank_reasons or []),
        # 회원 선호 전형에 해당하는 라벨. 선호 미지정이면 빈 리스트(배지·필터 모두 비활성).
        "preferred_hits": _preferred_hits(sorted(specials), list(preferred or [])),
    }


def matched_dashboard(
    session, member_id: int | None = None, today: date | None = None
) -> list[dict]:
    """매칭된(관심) 공고를 마감임박순으로, 분양가·면적·D-day 계산해 반환.

    북마크 플래그는 `member_id` 회원 기준. 회원이 없으면(비로그인 문맥) 북마크는 비어 있다.
    """
    today = today or date.today()
    bmarks = bookmarked_pblanc_nos(member_id, session=session) if member_id is not None else set()
    q = (
        select(Notice, MatchResult.my_rank)
        .join(MatchResult, Notice.pblanc_no == MatchResult.pblanc_no)
        .where(MatchResult.matched.is_(True))
        .order_by(Notice.rcept_endde)
    )
    items = [
        _dashboard_item(session, n, my_rank, today, n.pblanc_no in bmarks)
        for n, my_rank in session.execute(q).all()
    ]
    # 순위별 정렬: 1순위 → 2순위 → 판정불가(공공 등), 같은 그룹 안에선 마감임박순
    rank_order = {"1순위": 0, "2순위": 1}
    items.sort(key=lambda it: (rank_order.get(it["my_rank"], 2), it["deadline"] or date.max))
    return items


def member_dashboard(session, member_id: int, today: date | None = None) -> list[dict]:
    """매칭 공고를 **요청 시점에 그 회원 프로필로** 판정해 반환(D17 온더플라이).

    배치가 저장한 `MatchResult.my_rank` 는 단일 사용자 시절의 값이라 쓰지 않는다.
    해당지역은 거주지 ∪ 소득본거지로 판정하고(D18/D19), 순위와는 독립이다 —
    "해당지역 1순위"는 `my_rank == "1순위" and in_area` 로 표현된다.
    정렬: 해당지역 우선 → 1순위 → 2순위 → 판정불가 → 같은 그룹 안에선 마감임박순.
    """
    today = today or date.today()
    prof = get_profile(member_id, session=session)
    profile = profile_from_member(prof) if prof is not None else None
    applicant_regions = (
        list(prof.residence_regions or []) + list(prof.income_base_regions or [])
        if prof is not None
        else []
    )
    interest_regions = list(prof.interest_regions or []) if prof is not None else []
    # 선호 전형은 코드로 넘긴다 — 라벨 매칭은 특공 라벨을 이미 가진 _dashboard_item 안에서 한 번만.
    preferred = list(prof.preferred_types or []) if prof is not None else []
    bmarks = bookmarked_pblanc_nos(member_id, session=session)

    q = (
        select(Notice)
        .join(MatchResult, Notice.pblanc_no == MatchResult.pblanc_no)
        .where(MatchResult.matched.is_(True))
        .order_by(Notice.rcept_endde)
    )
    items = []
    for n in session.scalars(q).all():
        hts = house_types_of(n.pblanc_no, session=session)
        my_rank: str | None = None
        housing_type: str | None = None
        rank_reasons: list[str] = []
        # 지역 판정은 순위 지원 여부와 무관하다(공공 공고도 해당지역일 수 있다).
        in_area = region_matches(n.area_nm, applicant_regions)
        notice_judged = judge_notice(n, hts, profile, today=today) if profile is not None else None
        if notice_judged is not None and notice_judged["supported"]:
            # supported 게이트와 유형 판별은 judge_notice 가 쥐고 있어 그대로 재사용한다(중복 정의 금지).
            housing_type = notice_judged["housing_type"]
            if housing_type == "민영":
                judged = judge_rank(n, hts, profile, today=today, applicant_regions=applicant_regions)
            else:
                judged = judge_rank_public(n, profile, today=today, applicant_regions=applicant_regions)
            my_rank, in_area = judged["rank"], judged["in_area"]
            rank_reasons = judged["reasons"]
        items.append(
            _dashboard_item(
                session,
                n,
                my_rank,
                today,
                n.pblanc_no in bmarks,
                in_area=in_area,
                # 관심지역 미입력이면 필터하지 않는다(폴백=전체) — 빈 화면을 만들지 않는다.
                in_interest=region_matches(n.area_nm, interest_regions)
                if interest_regions
                else True,
                housing_type=housing_type,
                rank_reasons=rank_reasons,
                preferred=preferred,
            )
        )
    rank_order = {"1순위": 0, "2순위": 1}
    items.sort(
        key=lambda it: (
            0 if it["in_area"] else 1,
            rank_order.get(it["my_rank"], 2),
            it["deadline"] or date.max,
        )
    )
    return items


def bookmarked_dashboard(session, member_id: int, today: date | None = None) -> list[dict]:
    """그 회원이 북마크한 공고만(매칭 여부 무관) 최근 북마크순으로 반환.

    소유 술어를 조인 조건에 실어 다른 회원 행은 애초에 결과에 들어오지 못하게 한다.
    """
    today = today or date.today()
    q = (
        select(Notice, MatchResult.my_rank)
        .join(
            Bookmark,
            (Notice.pblanc_no == Bookmark.pblanc_no) & (Bookmark.member_id == member_id),
        )
        .outerjoin(MatchResult, Notice.pblanc_no == MatchResult.pblanc_no)
        .order_by(Bookmark.created_at.desc())
    )
    return [
        _dashboard_item(session, n, my_rank, today, True)
        for n, my_rank in session.execute(q).all()
    ]


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _polygon_centroid(poly) -> tuple[float, float] | None:
    """폴리곤 [[lng,lat],...] → 중심좌표 (lat, lng). 없거나 형식 이상이면 None."""
    if not poly or not isinstance(poly, list):
        return None
    lats, lngs = [], []
    for pt in poly:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            lng, lat = float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            continue
        lats.append(lat)
        lngs.append(lng)
    if not lats:
        return None
    return (sum(lats) / len(lats), sum(lngs) / len(lngs))


def _range(raw: dict, bgn: str, end: str) -> str | None:
    b, e = raw.get(bgn), raw.get(end)
    if b and e:
        return f"{b} ~ {e}"
    return b or e or None


def notice_detail_data(session, n) -> dict:
    """상세 페이지용 데이터 조립 — 주택형별 모집(특공별)·일정·규제."""
    raw = n.raw or {}
    hts = house_types_of(n.pblanc_no, session=session)
    rows = []
    for ht in hts:
        specials = [
            (label, _int(ht.raw.get(key)))
            for key, label in SPECIAL_SUPPLY_LABELS.items()
            if _int(ht.raw.get(key)) > 0
        ]
        rows.append({"ht": ht, "specials": specials})

    # 일정(있는 것만). 청약홈은 순위별 상세, LH는 접수/마감 위주.
    schedule = []

    def add(label, val):
        if val:
            schedule.append((label, val))

    add("모집공고", raw.get("RCRIT_PBLANC_DE"))
    add("특별공급 접수", _range(raw, "SPSPLY_RCEPT_BGNDE", "SPSPLY_RCEPT_ENDDE"))
    add("1순위 해당지역", _range(raw, "GNRL_RNK1_CRSPAREA_RCPTDE", "GNRL_RNK1_CRSPAREA_ENDDE"))
    add("1순위 기타경기", _range(raw, "GNRL_RNK1_ETC_GG_RCPTDE", "GNRL_RNK1_ETC_GG_ENDDE"))
    add("1순위 기타지역", _range(raw, "GNRL_RNK1_ETC_AREA_RCPTDE", "GNRL_RNK1_ETC_AREA_ENDDE"))
    add("2순위", _range(raw, "GNRL_RNK2_CRSPAREA_RCPTDE", "GNRL_RNK2_CRSPAREA_ENDDE"))
    add("당첨자발표", raw.get("PRZWNER_PRESNATN_DE"))
    add("계약", _range(raw, "CNTRCT_CNCLS_BGNDE", "CNTRCT_CNCLS_ENDDE"))
    if not schedule:  # LH 등 — ORM 컬럼 기반 접수 일정으로 대체
        if n.rcept_bgnde and n.rcept_endde:
            schedule.append(("접수", f"{n.rcept_bgnde} ~ {n.rcept_endde}"))
        elif n.rcept_bgnde or n.rcept_endde:
            schedule.append(("접수", str(n.rcept_bgnde or n.rcept_endde)))

    regs = [label for f, label in REGULATION_FLAGS.items() if raw.get(f) == "Y"]
    if raw.get("MDAT_TRGET_AREA_SECD") not in (None, "N", ""):
        regs.insert(0, "조정대상지역")

    profile = load_profile()
    judged = judge_notice(n, hts, profile) if profile is not None else None

    # 정정공고로 대체된 공고면 최신 공고번호를 배너로 안내
    mr = session.scalar(select(MatchResult).where(MatchResult.pblanc_no == n.pblanc_no))
    superseded_by = None
    for reason in (mr.fail_reasons or []) if mr else []:
        if reason.startswith(f"{SUPERSEDED_REASON}:"):
            superseded_by = reason.split(":", 1)[1]

    lh = raw.get("_lh_detail") or {}
    return {
        "notice": n,
        "judged": judged,
        "superseded_by": superseded_by,
        "rows": rows,
        "schedule": schedule,
        "lh_schedule": lh.get("schedule") or [],
        "pan_dtl": lh.get("pan_dtl_cts"),
        "regs": regs,
        "lh_images": lh.get("images") or [],
        "lh_files": lh.get("files") or [],
        "adres": raw.get("HSSPLY_ADRES") or n.hsslpy_adres,
        "tel": raw.get("MDHS_TELNO"),
        "builder": raw.get("CNSTRCT_ENTRPS_NM"),
        "mvn": raw.get("MVN_PREARNGE_YM") or lh.get("mvin"),
        "kakao_key": settings.kakao_js_key,
        "polygon": raw.get("_polygon") or None,
    }


# ── 프로필 폼(Task 10) ────────────────────────────────────────────────────────
# 필드 애너테이션·헬퍼는 온보딩 폼과 공유하려고 `forms.py` 로 옮겼다(O6).


class ProfileForm(BaseModel):
    """프로필 폼의 신뢰 경계 검증 — 타입·범위·형식을 진입점에서 한 번만 본다(allowlist).

    브라우저 쪽 검증은 UX 보조일 뿐이라 서버가 모든 제출을 다시 검증한다.
    모델에 없는 키(폼이 끼워 넣은 member_id 등)는 무시한다 — 회원 식별자는 세션에서만 온다(D14).
    """

    model_config = ConfigDict(extra="ignore")

    # 인적/세대
    birth_date: _OptDate = None
    marriage_date: _OptDate = None
    engaged: bool = False
    is_household_head: bool = False
    household_all_homeless: bool = False
    homeless_since: _OptDate = None
    dependents: _Count = 0
    won_within_5y: bool = False
    children_minor: _Count = 0
    real_estate_manwon: _Count = 0
    region: str = Field("", max_length=50)
    household_type: Literal["general", "newlywed", "pre_newlywed", "youth"] = "general"
    household_head_owns_home: bool = False
    car_value_manwon: _Count = 0
    # 청약통장
    account_opened: _OptDate = None
    account_balance_manwon: _Count = 0
    # 소득
    income_monthly_manwon: _OptCount = None
    income_base_manwon: _OptCount = None
    income_dual: bool = False
    # 생애최초
    is_first_home: bool = False
    fl_ever_owned_house: bool = False
    fl_income_tax_5y: bool = False
    fl_currently_earning: bool = False
    # 지역(콤마 구분 입력)
    residence_regions: _Regions = []
    income_base_regions: _Regions = []
    interest_regions: _Regions = []


def _profile_form_values(prof: MemberProfile | None) -> dict:
    """DB 행 → 폼 표시용 값(날짜/숫자는 문자열, 지역은 콤마 구분, 체크박스는 bool)."""
    if prof is None:
        return {"household_type": "general"}
    values: dict = {}
    for name in ProfileForm.model_fields:
        v = getattr(prof, name)
        if name in _REGION_FIELDS:
            values[name] = ", ".join(v or [])
        elif isinstance(v, bool):  # bool 은 int 의 하위형이라 숫자보다 먼저 본다
            values[name] = v
        elif v is None:
            values[name] = ""
        elif isinstance(v, date):
            values[name] = v.isoformat()
        else:
            values[name] = str(v)
    return values


def _profile_context(values: dict, errors: dict, *, saved: bool = False) -> dict:
    return {
        "values": values,
        "errors": errors,
        "saved": saved,
        "household_types": HOUSEHOLD_TYPES,
        "couple_types": COUPLE_HOUSEHOLD_TYPES,
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/profile")
def profile_page(request: Request, member_id: int = Depends(require_login)):
    with SessionLocal() as session:
        values = _profile_form_values(get_profile(member_id, session=session))
    saved = request.query_params.get("saved") == "1"
    return _TEMPLATES.TemplateResponse(
        request, "profile.html", _profile_context(values, {}, saved=saved)
    )


@app.post("/profile")
async def profile_submit(request: Request, member_id: int = Depends(require_login)):
    form = dict(await request.form())
    try:
        data = ProfileForm.model_validate(form)
    except ValidationError as exc:
        # 오류를 필드로 되돌려 같은 슬롯에 인라인 표시하고, 입력값은 그대로 되살린다.
        errors: dict[str, str] = {}
        for err in exc.errors():
            if err["loc"]:
                field = str(err["loc"][0])
                errors.setdefault(field, _field_error(field))
        return _TEMPLATES.TemplateResponse(
            request, "profile.html", _profile_context(form, errors), status_code=400
        )
    with SessionLocal() as session:
        update_profile(member_id, data.model_dump(), session=session)
    return RedirectResponse("/profile?saved=1", status_code=303)


def _api_member_id(request: Request) -> int:
    """JSON API 용 로그인 게이트 — HTML 페이지의 303 과 달리 미로그인은 401(D15)."""
    member_id = current_member_id(request)
    if member_id is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return member_id


@app.put("/bookmark/{pblanc_no}")
def bookmark_add(pblanc_no: str, request: Request) -> dict:
    member_id = _api_member_id(request)
    with SessionLocal() as session:
        if session.scalar(select(Notice.pblanc_no).where(Notice.pblanc_no == pblanc_no)) is None:
            raise HTTPException(status_code=404, detail="공고를 찾을 수 없습니다")
        add_bookmark(member_id, pblanc_no, session=session)
    return {"bookmarked": True}


@app.delete("/bookmark/{pblanc_no}")
def bookmark_remove(pblanc_no: str, request: Request) -> dict:
    member_id = _api_member_id(request)
    with SessionLocal() as session:
        remove_bookmark(member_id, pblanc_no, session=session)
    return {"bookmarked": False}


@app.get("/notice/{pblanc_no}")
def notice_detail(pblanc_no: str, request: Request):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    with SessionLocal() as session:
        n = session.scalar(select(Notice).where(Notice.pblanc_no == pblanc_no))
        if n is None:
            raise HTTPException(status_code=404, detail="공고를 찾을 수 없습니다")
        data = notice_detail_data(session, n)
    return _TEMPLATES.TemplateResponse(request, "detail.html", data)


@app.get("/")
def index(request: Request, member_id: int = Depends(require_login)):
    cfg = load_filter_config()
    with SessionLocal() as session:
        items = member_dashboard(session, member_id)
        prof = get_profile(member_id, session=session)
        interest_regions = list(prof.interest_regions or []) if prof is not None else []
        onboarding_step = prof.onboarding_step if prof is not None else 0
        chosen = set(prof.preferred_types or []) if prof is not None else set()
        # 표시용 한글 라벨만 내려보낸다(정의 순서 유지). 비면 템플릿이 토글을 렌더하지 않는다.
        preferred_types = [label for code, label in PREFERRED_TYPE_LABELS if code in chosen]
    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "items": items,
            "cfg": cfg,
            "today": date.today(),
            "kakao_key": settings.kakao_js_key,
            "agencies": AGENCIES,
            "interest_regions": interest_regions,
            # 선호 전형(한글 라벨). 비어 있으면 index.html 이 "선호 전형만" 토글을 렌더하지 않는다.
            "preferred_types": preferred_types,
            # 온보딩 미완성 배너용(O5) — base.html 이 3 미만일 때만 이어하기 링크를 띄운다.
            "onboarding_step": onboarding_step,
        },
    )


@app.get("/bookmarks")
def bookmarks_page(request: Request, member_id: int = Depends(require_login)):
    with SessionLocal() as session:
        items = bookmarked_dashboard(session, member_id)
    return _TEMPLATES.TemplateResponse(request, "bookmarks.html", {"items": items})
