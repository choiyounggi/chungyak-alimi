"""회원 인증 라우트 + 로그인 의존성(Task 05).

단일 백엔드 + 동일 사이트 브라우저 앱이므로 토큰이 아닌 서버 세션 쿠키를 쓴다
(쿠키 옵션은 app.py 의 SessionMiddleware: httpOnly, Secure=session_https_only, SameSite=Lax).
세션에는 member_id 만 담고, 인가 판단은 항상 세션의 member_id 로만 한다 —
요청 폼/경로가 보낸 id 는 신뢰하지 않는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr, Field, ValidationError

from ..db import SessionLocal
from ..members import (
    MAX_PASSWORD_LEN,
    authenticate_member,
    create_member,
    get_member_by_email,
    hash_password,
)
from ..password_policy import POLICY_HINTS, validate_password

router = APIRouter()

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# 형식 판정은 직접 만든 정규식이 아니라 EmailStr(email-validator)에 위임한다.
# pydantic 이 내부적으로 check_deliverability=False 로 호출하므로 가입 응답에 DNS/MX 왕복이
# 들어가지 않는다(E1) — 우리 쪽에서 끌 설정 지점이 따로 없다.
# 상한 254는 RFC 5321 주소 최대 길이.
_Email = Annotated[EmailStr, Field(max_length=254)]

# 에러 문구는 서버가 정한 고정 문자열 — 사용자 입력을 그대로 반사하지 않는다.
_LOGIN_FAILED = "이메일 또는 비밀번호가 올바르지 않습니다"
_INVALID_INPUT = "이메일 형식과 비밀번호(1~128자)를 확인해주세요"
_DUPLICATE_EMAIL = "이미 가입된 이메일입니다"
_PASSWORD_MISMATCH = "비밀번호와 비밀번호 확인이 다릅니다"

# pydantic 이 만든 원문 메시지는 영문이고 입력 일부를 담을 수 있으므로 화면에 쓰지 않는다.
# 실패한 필드 이름(loc)만 읽어 서버가 정한 고정 문장으로 치환한다.
_FIELD_MESSAGES: dict[str, str] = {
    "email": "이메일 주소 형식을 확인해주세요",
    "password": f"비밀번호를 1~{MAX_PASSWORD_LEN}자로 입력해주세요",
    "password2": f"비밀번호 확인을 1~{MAX_PASSWORD_LEN}자로 입력해주세요",
}


class Credentials(BaseModel):
    """로그인 폼의 신뢰 경계 검증(형식·길이). 통과 뒤 계층은 이 형태를 신뢰한다.

    KISA 정책 검증은 하지 않는다 — 정책 시행 전에 만들어진 계정을 잠그지 않기 위해서다(P4).
    """

    email: _Email
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LEN)


class RegisterForm(BaseModel):
    """가입 폼의 형태(형식·길이) 검증.

    확인 일치와 KISA 정책은 여기서 보지 않는다 — 위반 사유를 필드별 *리스트* 로 전부
    돌려줘야 하는데(P5) pydantic 검증자는 필드당 예외를 하나만 낼 수 있기 때문이다.
    두 검사는 register_submit 이 이어서 수행한다.
    """

    email: _Email
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LEN)
    password2: str = Field(min_length=1, max_length=MAX_PASSWORD_LEN)


def _shape_errors(exc: ValidationError) -> dict[str, list[str]]:
    """ValidationError -> {필드: [고정 문장]}. 사용자 입력을 반사하지 않는다."""
    out: dict[str, list[str]] = {}
    for err in exc.errors():
        field = str(err["loc"][0]) if err["loc"] else "email"
        message = _FIELD_MESSAGES.get(field)
        if message is not None and message not in out.setdefault(field, []):
            out[field].append(message)
    return {field: msgs for field, msgs in out.items() if msgs}


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
    errors: dict[str, list[str]] | None = None,
    status_code: int = 200,
):
    return _TEMPLATES.TemplateResponse(
        request,
        template,
        {
            # 필드에 붙일 수 없는 폼 레벨 오류(로그인 실패·중복 이메일)
            "error": error,
            "email": email,
            # 필드 인라인 오류: {"email"|"password"|"password2": [고정 문장, ...]}
            "errors": errors or {},
            # 규칙 체크리스트의 단일 출처는 password_policy.POLICY_HINTS 다(U3).
            "policy_hints": POLICY_HINTS,
        },
        status_code=status_code,
    )


@router.get("/register")
def register_page(request: Request):
    return _render(request, "register.html")


@router.post("/register")
def register_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
):
    try:
        form = RegisterForm(email=email, password=password, password2=password2)
    except ValidationError as exc:
        # 재렌더는 이메일만 되살린다 — 비밀번호 값은 응답 본문에 넣지 않는다.
        return _render(
            request, "register.html", errors=_shape_errors(exc), email=email, status_code=400
        )

    # 확인 일치와 정책 위반을 함께 모아 한 번에 보여준다(제출 시 사유 전량 노출, P5).
    errors: dict[str, list[str]] = {}
    if form.password != form.password2:
        errors["password2"] = [_PASSWORD_MISMATCH]
    reasons = validate_password(form.password, email=form.email)
    if reasons:
        errors["password"] = reasons
    if errors:
        return _render(request, "register.html", errors=errors, email=email, status_code=400)

    with SessionLocal() as session:
        if get_member_by_email(form.email, session=session) is not None:
            return _render(
                request, "register.html", error=_DUPLICATE_EMAIL, email=email, status_code=409
            )
        member = create_member(form.email, hash_password(form.password), session=session)
        request.session["member_id"] = member.id
    # 가입 직후는 3스텝 온보딩으로 보낸다(이 경로는 Task 05가 구현한다).
    return RedirectResponse("/onboarding/1", status_code=303)


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
