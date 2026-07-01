from __future__ import annotations

import logging
import secrets
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from ..config import settings
from ..db import MatchResult, Notice, SessionLocal, house_types_of
from ..filters import load_filter_config

logger = logging.getLogger(__name__)

app = FastAPI(title="청약 알리미")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, max_age=60 * 60 * 24 * 14)
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

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
        "웹 인증 미설정(WEB_USER/WEB_PASSWORD 비어있음) — 대시보드가 인증 없이 노출됩니다. "
        "외부 공개 시 반드시 설정하세요."
    )


def _auth_enabled() -> bool:
    return bool(settings.web_user and settings.web_password)


def _authed(request: Request) -> bool:
    """인증이 꺼져있으면(로컬) 항상 통과, 켜져있으면 세션 로그인 여부."""
    return not _auth_enabled() or request.session.get("authed") is True


@app.get("/login")
def login_page(request: Request):
    if _authed(request):
        return RedirectResponse("/", status_code=303)
    return _TEMPLATES.TemplateResponse(request, "login.html", {"errors": {}, "username": ""})


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
):
    errors: dict[str, str] = {}
    if not username.strip():
        errors["username"] = "아이디를 입력해주세요"
    if not password:
        errors["password"] = "비밀번호를 입력해주세요"
    if not errors:
        ok = bool(settings.web_user) and (
            secrets.compare_digest(username, settings.web_user)
            and secrets.compare_digest(password, settings.web_password)
        )
        if not ok:
            errors["form"] = "아이디 또는 비밀번호가 올바르지 않습니다"
    if errors:
        return _TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"errors": errors, "username": username},
            status_code=401,
        )
    request.session["authed"] = True
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def matched_dashboard(session, today: date | None = None) -> list[dict]:
    """매칭된(관심) 공고를 마감임박순으로, 분양가·면적·D-day 계산해 반환."""
    today = today or date.today()
    q = (
        select(Notice)
        .join(MatchResult, Notice.pblanc_no == MatchResult.pblanc_no)
        .where(MatchResult.matched.is_(True))
        .order_by(Notice.rcept_endde)
    )
    items: list[dict] = []
    for n in session.scalars(q).all():
        hts = house_types_of(n.pblanc_no, session=session)
        prices = [h.lttot_top_amount for h in hts if h.lttot_top_amount]
        areas = [float(h.suply_ar) for h in hts if h.suply_ar is not None]
        deadlines = [d for d in (n.rcept_endde, n.spsply_rcept_endde) if d]
        deadline = max(deadlines) if deadlines else None
        items.append(
            {
                "notice": n,
                "adres": n.hsslpy_adres or (n.raw or {}).get("HSSPLY_ADRES"),
                "price_lo": min(prices) if prices else None,
                "price_hi": max(prices) if prices else None,
                "area_lo": min(areas) if areas else None,
                "area_hi": max(areas) if areas else None,
                "deadline": deadline,
                "dday": (deadline - today).days if deadline else None,
            }
        )
    return items


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


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
        if n.rcept_bgnde or n.rcept_endde:
            schedule.append(("접수", f"{n.rcept_bgnde} ~ {n.rcept_endde}"))

    regs = [label for f, label in REGULATION_FLAGS.items() if raw.get(f) == "Y"]
    if raw.get("MDAT_TRGET_AREA_SECD") not in (None, "N", ""):
        regs.insert(0, "조정대상지역")

    lh = raw.get("_lh_detail") or {}
    return {
        "notice": n,
        "rows": rows,
        "schedule": schedule,
        "lh_schedule": lh.get("schedule") or [],
        "pan_dtl": lh.get("pan_dtl_cts"),
        "regs": regs,
        "adres": raw.get("HSSPLY_ADRES") or n.hsslpy_adres,
        "tel": raw.get("MDHS_TELNO"),
        "builder": raw.get("CNSTRCT_ENTRPS_NM"),
        "mvn": raw.get("MVN_PREARNGE_YM") or lh.get("mvin"),
        "kakao_key": settings.kakao_js_key,
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


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
def index(request: Request):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    cfg = load_filter_config()
    with SessionLocal() as session:
        items = matched_dashboard(session)
    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {"items": items, "cfg": cfg, "today": date.today()},
    )
