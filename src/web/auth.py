"""회원 인증 라우트 + 로그인 의존성(Task 05).

단일 백엔드 + 동일 사이트 브라우저 앱이므로 토큰이 아닌 서버 세션 쿠키를 쓴다
(쿠키 옵션은 app.py 의 SessionMiddleware: httpOnly, Secure=session_https_only, SameSite=Lax).
세션에는 member_id 만 담고, 인가 판단은 항상 세션의 member_id 로만 한다 —
요청 폼/경로가 보낸 id 는 신뢰하지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError

from ..db import SessionLocal
from ..members import (
    MAX_PASSWORD_LEN,
    authenticate_member,
    create_member,
    get_member_by_email,
    hash_password,
)

router = APIRouter()

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# 이메일 형식 allowlist(공백/@ 없는 값 거부). 상한 254는 RFC 5321 주소 최대 길이.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

# 에러 문구는 서버가 정한 고정 문자열 — 사용자 입력을 그대로 반사하지 않는다.
_LOGIN_FAILED = "이메일 또는 비밀번호가 올바르지 않습니다"
_INVALID_INPUT = "이메일 형식과 비밀번호(1~128자)를 확인해주세요"
_DUPLICATE_EMAIL = "이미 가입된 이메일입니다"


class Credentials(BaseModel):
    """폼 입력의 신뢰 경계 검증(형식·길이). 통과 뒤 계층은 이 형태를 신뢰한다."""

    email: str = Field(min_length=3, max_length=254, pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LEN)


def current_member_id(request: Request) -> int | None:
    """로그인한 회원 id(없으면 None). JSON API 라우트는 이걸 직접 보고 401을 반환한다."""
    return request.session.get("member_id")


def require_login(request: Request) -> int:
    """HTML 보호 페이지용 의존성 — 미로그인이면 /login 으로 303."""
    member_id = current_member_id(request)
    if member_id is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return member_id


def _render(
    request: Request,
    template: str,
    *,
    error: str | None = None,
    email: str = "",
    status_code: int = 200,
):
    return _TEMPLATES.TemplateResponse(
        request,
        template,
        {
            "error": error,
            "email": email,
            # 구 basic-auth login.html 과의 호환 키. Task 06에서 템플릿 교체 시 제거된다.
            "errors": {"form": error} if error else {},
            "username": email,
        },
        status_code=status_code,
    )


@router.get("/register")
def register_page(request: Request):
    return _render(request, "register.html")


@router.post("/register")
def register_submit(request: Request, email: str = Form(""), password: str = Form("")):
    try:
        creds = Credentials(email=email, password=password)
    except ValidationError:
        return _render(request, "register.html", error=_INVALID_INPUT, email=email, status_code=400)

    with SessionLocal() as session:
        if get_member_by_email(creds.email, session=session) is not None:
            return _render(
                request, "register.html", error=_DUPLICATE_EMAIL, email=email, status_code=409
            )
        member = create_member(creds.email, hash_password(creds.password), session=session)
        request.session["member_id"] = member.id
    return RedirectResponse("/", status_code=303)


@router.get("/login")
def login_page(request: Request):
    return _render(request, "login.html")


@router.post("/login")
def login_submit(request: Request, email: str = Form(""), password: str = Form("")):
    try:
        creds = Credentials(email=email, password=password)
    except ValidationError:
        return _render(request, "login.html", error=_INVALID_INPUT, email=email, status_code=400)

    with SessionLocal() as session:
        member = authenticate_member(creds.email, creds.password, session=session)
        if member is None:
            return _render(
                request, "login.html", error=_LOGIN_FAILED, email=email, status_code=401
            )
        request.session["member_id"] = member.id
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
