from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError
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
from ..members import get_profile, update_profile
from ..scoring import judge_notice, load_profile
from . import auth
from .auth import require_login

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

# 규제/특성 플래그(raw 필드 → 라벨). 값이 'Y'/'N' 또는 코드.
REGULATION_FLAGS = {
    "SPECLT_RDN_EARTH_AT": "투기과열지구",
    "PARCPRC_ULS_AT": "분양가상한제",
    "PUBLIC_HOUSE_SPCLW_APPLC_AT": "공공주택특별법",
    "LRSCL_BLDLND_AT": "대규모택지",
}

if not settings.web_user or not settings.web_password:
    logger.warning(
        "웹 인증 미설정(WEB_USER/WEB_PASSWORD 비어있음) — 북마크 API/페이지가 인증 없이 "
        "노출됩니다(대시보드는 회원 로그인으로 보호됨). 회원별 북마크 전환은 Task 12."
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


def _dashboard_item(session, n, my_rank, today: date, bookmarked: bool) -> dict:
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
    }


def matched_dashboard(session, today: date | None = None) -> list[dict]:
    """매칭된(관심) 공고를 마감임박순으로, 분양가·면적·D-day 계산해 반환."""
    today = today or date.today()
    bmarks = bookmarked_pblanc_nos(session=session)
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


def bookmarked_dashboard(session, today: date | None = None) -> list[dict]:
    """북마크된 공고만(매칭 여부 무관) 최근 북마크순으로 반환."""
    today = today or date.today()
    q = (
        select(Notice, MatchResult.my_rank)
        .join(Bookmark, Notice.pblanc_no == Bookmark.pblanc_no)
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

# 세대유형 — DB CHECK 제약(ck_member_profile_household_type)과 같은 닫힌 4값(D7).
HOUSEHOLD_TYPES: tuple[tuple[str, str], ...] = (
    ("general", "일반"),
    ("newlywed", "신혼부부"),
    ("pre_newlywed", "예비신혼부부"),
    ("youth", "청년"),
)
# 두 사람의 거주지·소득본거지를 함께 입력받아야 하는 세대유형
COUPLE_HOUSEHOLD_TYPES: tuple[str, ...] = ("newlywed", "pre_newlywed")

_DATE_FIELDS = frozenset({"birth_date", "marriage_date", "homeless_since", "account_opened"})
_NUMBER_FIELDS = frozenset(
    {
        "dependents", "children_minor", "real_estate_manwon", "account_balance_manwon",
        "income_monthly_manwon", "income_base_manwon", "car_value_manwon",
    }
)
_REGION_FIELDS = frozenset({"residence_regions", "income_base_regions", "interest_regions"})


def _blank_to(default):
    """빈 문자열(미입력)을 그 필드의 '값 없음'으로 접는다 — 폼은 미입력도 ''로 보낸다."""

    def convert(v):
        return default if isinstance(v, str) and not v.strip() else v

    return convert


def _split_regions(v):
    """콤마 구분 문자열 → list[str](공백 트림, 빈 항목 제거). 빈 입력은 []."""
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return v


_OptDate = Annotated[date | None, BeforeValidator(_blank_to(None))]
# ge 는 Optional 의 **안쪽 int** 에 붙여야 한다. Annotated[int | None, Field(ge=0)] 로 두면
# 제약이 유니온 전체에 걸려 None 이 들어온 순간 `None >= 0` 으로 TypeError 가 난다.
# 상한은 저장 컬럼(INTEGER)의 최대치 — 이걸 넘기면 검증을 통과하고도 INSERT 가 터진다.
_INT_MAX = 2_147_483_647
_NonNegInt = Annotated[int, Field(ge=0, le=_INT_MAX)]
_Count = Annotated[_NonNegInt, BeforeValidator(_blank_to(0))]
_OptCount = Annotated[_NonNegInt | None, BeforeValidator(_blank_to(None))]
_Regions = Annotated[
    list[Annotated[str, Field(max_length=50)]],
    BeforeValidator(_split_regions),
    Field(max_length=20),
]


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


def _field_error(field: str) -> str:
    """필드별 오류 문구 — '무엇이 틀렸나'가 아니라 '무엇을 하면 되나'를 적는다."""
    if field in _DATE_FIELDS:
        return "날짜를 YYYY-MM-DD 형식으로 입력해주세요"
    if field in _NUMBER_FIELDS:
        return f"0 이상의 숫자를 입력해주세요(최대 {_INT_MAX:,})"
    if field == "household_type":
        return "세대유형을 목록에서 선택해주세요"
    if field in _REGION_FIELDS:
        return "지역은 콤마로 구분해 20개까지, 각 50자 이내로 입력해주세요"
    return "입력값을 확인해주세요"


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


@app.put("/bookmark/{pblanc_no}")
def bookmark_add(pblanc_no: str, request: Request) -> dict:
    if not _authed(request):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    with SessionLocal() as session:
        if session.scalar(select(Notice.pblanc_no).where(Notice.pblanc_no == pblanc_no)) is None:
            raise HTTPException(status_code=404, detail="공고를 찾을 수 없습니다")
        add_bookmark(pblanc_no, session=session)
    return {"bookmarked": True}


@app.delete("/bookmark/{pblanc_no}")
def bookmark_remove(pblanc_no: str, request: Request) -> dict:
    if not _authed(request):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    with SessionLocal() as session:
        remove_bookmark(pblanc_no, session=session)
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
        items = matched_dashboard(session)
    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "items": items,
            "cfg": cfg,
            "today": date.today(),
            "kakao_key": settings.kakao_js_key,
            "agencies": AGENCIES,
        },
    )


@app.get("/bookmarks")
def bookmarks_page(request: Request):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    with SessionLocal() as session:
        items = bookmarked_dashboard(session)
    return _TEMPLATES.TemplateResponse(request, "bookmarks.html", {"items": items})
