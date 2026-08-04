"""SH(서울주택도시공사) 공고 수집 — 서울주거포털 임대·분양 목록 HTML 파싱.

목록 페이지에 접수기간이 없어 `rcept_bgnde`/`rcept_endde` 는 항상 None 이고,
임대는 모집상태 '모집중' 행만 수집해 이를 대체한다(D16). 상세 페이지 크롤링과
`www.i-sh.co.kr` 로의 요청은 하지 않는다(robots.txt 전면 거부) — 링크로만 쓴다.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from datetime import date
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

SH_BASE = "https://housing.seoul.go.kr"
BOARDS = (
    ("lease", "/site/main/sh/publicLease/list"),
    ("sale", "/site/main/sh/publicSale/01/list"),
)
# D14 — 목록에서 뽑은 링크는 이 두 호스트만 허용한다(리다이렉트·오염 방지).
ALLOWED_LINK_HOSTS = ("housing.seoul.go.kr", "www.i-sh.co.kr")
MAX_NAME_LEN = 200
MAX_URL_LEN = 500

# 임대 8칸 [번호,청약유형,공고명,공고게시일,발표일,모집상태,담당부서,링크]
# 분양 7칸 [번호,청약유형,공고명,공고게시일,발표일,담당부서,링크]
_MIN_CELLS = 7
_RECRUITING = "모집중"
_CORRECTION_RAW = "[정정]"
_CORRECTION_STD = "[정정공고]"

_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"', re.I)


def _strip_comments(page_html: str) -> str:
    """주석을 먼저 제거한다 — 공고명 칸에 주석 처리된 <a href>가 들어있어
    그대로 두면 링크 추출이 그 값을 잘못 집는다(실측)."""
    return _COMMENT_RE.sub("", page_html)


def _text(cell: str) -> str:
    """셀 HTML → 공백 정규화된 순수 텍스트."""
    return " ".join(html.unescape(_TAG_RE.sub(" ", cell)).split())


def _rows(page_html: str) -> list[list[str]]:
    """페이지 HTML → 행별 <td> 내부 HTML 목록. 셀이 모자란 행(헤더 등)은 건너뛴다."""
    out: list[list[str]] = []
    for row in _TR_RE.findall(_strip_comments(page_html)):
        tds = _TD_RE.findall(row)
        if len(tds) < _MIN_CELLS:
            continue
        out.append(tds)
    return out


def _safe_link(cell: str) -> str | None:
    """마지막 셀에서 링크 1개를 뽑아 스킴·호스트를 검증한다(D14)."""
    m = _HREF_RE.search(cell)
    if not m:
        return None
    url = urljoin(SH_BASE, html.unescape(m.group(1)).strip())
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_LINK_HOSTS:
        return None
    return url


def _seq_of(url: str | None) -> str | None:
    if not url:
        return None
    values = parse_qs(urlsplit(url).query).get("seq") or []
    return values[0] if values and values[0] else None


def _parse_row(tds: list[str], board: str) -> dict | None:
    """행의 셀 목록 → 통합 스키마 dict. 공고명이 없으면 None(그 행 스킵)."""
    texts = [_text(td) for td in tds]
    if len(texts) >= 8:
        status = texts[5] or None
        dept = texts[6]
    else:
        status = None
        dept = texts[5]

    name = texts[2]
    if not name:
        return None

    posted = texts[3]
    link = _safe_link(tds[-1])
    seq = _seq_of(link)
    if seq:
        native = f"{board}-{seq}"
    else:
        # 번호 칸(texts[0])은 게시물이 쌓이며 밀리므로 PK 로 쓰지 않는다.
        digest = hashlib.sha1(name.encode()).hexdigest()[:8]
        native = f"{board}-{posted}-{digest}"

    return {
        "pblanc_no": native,
        "house_nm": name,
        "house_secd_nm": texts[1] or None,
        "house_dtl_secd_nm": texts[1] or None,
        "rcrit_pblanc_de": posted,
        "przwner_presnatn_de": texts[4],
        "pblanc_url": link,
        "raw": {
            "_sh_board": board,
            "_sh_status": status,
            "_sh_dept": dept,
            "_sh_row_no": texts[0],
        },
    }


class ShNotice(BaseModel):
    """SH 공고 1건 (통합 notice 스키마에 맞춘 정규화)."""

    model_config = ConfigDict(populate_by_name=True)

    pblanc_no: str
    house_nm: str
    area_nm: str | None = "서울"  # 서울주거포털은 서울 전용
    house_secd_nm: str | None = None
    house_dtl_secd_nm: str | None = None
    rcrit_pblanc_de: date | None = None
    przwner_presnatn_de: date | None = None
    pblanc_url: str | None = None
    agency: str = "SH"
    bsns_mby_nm: str | None = "서울주택도시공사"

    # 통합 notice 컬럼 호환용(SH 목록에 없는 값은 None)
    house_manage_no: str | None = None  # 이름 기반 정정 그룹핑 경로(D25 else 가지)
    rent_secd_nm: str | None = None
    hsslpy_adres: str | None = None
    rcept_bgnde: date | None = None  # D16 — 목록에 접수기간 없음
    rcept_endde: date | None = None
    spsply_rcept_bgnde: date | None = None
    spsply_rcept_endde: date | None = None
    tot_suply_hshldco: int | None = None
    mvn_prearnge_ym: str | None = None
    rent_gtn: int | None = None
    mt_rntchrg: int | None = None

    raw: dict = Field(default_factory=dict, exclude=True)

    @field_validator("house_nm", mode="before")
    @classmethod
    def _norm_name(cls, v):
        """정정 접두를 기존 규약(`[정정공고]`)으로 맞추고 길이 상한을 적용한다(D14)."""
        s = str(v or "").strip()
        if s.startswith(_CORRECTION_RAW):
            s = _CORRECTION_STD + s[len(_CORRECTION_RAW) :]
        return s[:MAX_NAME_LEN]

    @field_validator("rcrit_pblanc_de", "przwner_presnatn_de", mode="before")
    @classmethod
    def _shdate(cls, v):
        """'-' 이나 빈 칸, 날짜가 아닌 안내문은 None 으로."""
        s = str(v or "").strip()
        parts = s.split("-")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return None
        return s

    @field_validator("pblanc_url", mode="before")
    @classmethod
    def _cap_url(cls, v):
        if not v or len(str(v)) > MAX_URL_LEN:
            return None
        return v


def fetch_sh_notices(*, client: httpx.Client | None = None) -> list[ShNotice]:
    """서울주거포털에서 SH 임대·분양 공고 목록을 수집한다."""
    own_client = client is None
    client = client or httpx.Client(timeout=30.0, follow_redirects=True)  # D10
    out: list[ShNotice] = []
    seen: set[str] = set()
    try:
        for board, path in BOARDS:
            resp = client.get(SH_BASE + path)
            resp.raise_for_status()
            for tds in _rows(resp.text):
                row = _parse_row(tds, board)
                if row is None:
                    continue
                try:
                    n = ShNotice.model_validate(row)
                except ValidationError as e:
                    logger.warning("SH 공고 파싱 실패 스킵(%s): %s", board, e)
                    continue
                # D16 — 임대는 모집중만. 분양은 상태 컬럼이 없어 전량.
                if board == "lease" and n.raw.get("_sh_status") != _RECRUITING:
                    continue
                if n.pblanc_no not in seen:
                    seen.add(n.pblanc_no)
                    out.append(n)
    finally:
        if own_client:
            client.close()
    return out
