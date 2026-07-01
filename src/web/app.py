from __future__ import annotations

import logging
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..config import settings
from ..db import MatchResult, Notice, SessionLocal, house_types_of
from ..filters import load_filter_config

logger = logging.getLogger(__name__)

app = FastAPI(title="청약 알리미")
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_security = HTTPBasic(auto_error=False)

if not settings.web_user or not settings.web_password:
    logger.warning(
        "웹 인증 미설정(WEB_USER/WEB_PASSWORD 비어있음) — 대시보드가 인증 없이 노출됩니다. "
        "외부 공개 시 반드시 설정하세요."
    )


def require_login(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_security)],
) -> None:
    """web_user/web_password 가 설정돼 있으면 Basic 인증을 강제한다(외부공개용).

    설정이 비어있으면(로컬 전용) 인증을 건너뛴다.
    """
    if not settings.web_user or not settings.web_password:
        return
    ok = credentials is not None and (
        secrets.compare_digest(credentials.username, settings.web_user)
        and secrets.compare_digest(credentials.password, settings.web_password)
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다",
            headers={"WWW-Authenticate": "Basic"},
        )


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
                "price_lo": min(prices) if prices else None,
                "price_hi": max(prices) if prices else None,
                "area_lo": min(areas) if areas else None,
                "area_hi": max(areas) if areas else None,
                "deadline": deadline,
                "dday": (deadline - today).days if deadline else None,
            }
        )
    return items


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/")
def index(request: Request, _: Annotated[None, Depends(require_login)] = None):
    cfg = load_filter_config()
    with SessionLocal() as session:
        items = matched_dashboard(session)
    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {"items": items, "cfg": cfg, "today": date.today()},
    )
